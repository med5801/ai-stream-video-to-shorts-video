"""Twitch VOD -> YouTube Shorts in one command.

  run.bat 2929768251
  run.bat 2929768251 --brain gemini
  run.bat 2929768251 --idea "shorter hooks, exaggerate the zooms"
  run.bat 2929768251 --only 2,4 --replan
  run.bat 2929768251 --no-review          (no questions, fully automatic)
"""
import argparse
import datetime
import glob
import os
import re
import subprocess
import sys

import brain
from frames import make_frames
from render import render
from shorts_common import (ASSETS, SHORTS, TOOLS, VODS, config, fmt, load_json, probe, save_json,
                           utf8_console)
from transcribe import transcribe

LAYOUTS = os.path.join(TOOLS, "layouts.json")


def open_file(path):
    try:
        os.startfile(path)  # Windows only
    except Exception:
        pass


# ---------- data ----------

def get_clips(vod_dir):
    data = load_json(os.path.join(vod_dir, "clips.json"))
    if data:
        return [d for d in data if os.path.exists(os.path.join(vod_dir, d["file"]))]
    out = []
    for p in sorted(glob.glob(os.path.join(vod_dir, "clip_*.mp4"))):
        m = re.search(r"clip_(\d+)_(\d+)h(\d+)m(\d+)s", os.path.basename(p))
        if m:
            n, h, mi, se = map(int, m.groups())
            out.append({"n": n, "file": os.path.basename(p), "start": h * 3600 + mi * 60 + se})
    return out


def transcript_text(words):
    lines, cur, t0 = [], [], None
    prev_e = None
    for w in words:
        if cur and (len(cur) >= 14 or (prev_e is not None and w["s"] - prev_e > 0.8)):
            lines.append(f"[{t0:.1f}] " + " ".join(cur))
            cur = []
        if not cur:
            t0 = w["s"]
        cur.append(w["w"])
        prev_e = w["e"]
    if cur:
        lines.append(f"[{t0:.1f}] " + " ".join(cur))
    return "\n".join(lines)


def chat_text(chat, start, dur, limit=150):
    out = []
    for cm in chat.get("comments", []):
        t = cm.get("content_offset_seconds", 0) - start
        if 0 <= t <= dur:
            user = (cm.get("commenter") or {}).get("display_name", "?")
            body = (cm.get("message") or {}).get("body", "")
            out.append(f"[{t:.0f}] {user}: {body}")
    if len(out) > limit:
        step = len(out) / limit
        out = [out[int(i * step)] for i in range(limit)]
    return "\n".join(out)


def clip_chat(chat, start, dur):
    out = []
    for cm in chat.get("comments", []):
        t = cm.get("content_offset_seconds", 0) - start
        if -5 <= t <= dur + 5:
            msg = cm.get("message") or {}
            out.append({"t": t, "user": (cm.get("commenter") or {}).get("display_name", "?"),
                        "body": msg.get("body", ""), "color": msg.get("user_color")})
    return out


# ---------- review (the owner's idea slot) ----------

def show_plan(name, p, model):
    s, e = p["cut"]["start"], p["cut"]["end"]
    verdict = "KEEP" if p["keep"] else "SKIP"
    print(f"\n  ┌─ {name}  [{model}]")
    print(f"  │ Verdict: {verdict}   Score: {p['score']:.0f}/10   {p.get('reason', '')}")
    print(f"  │ Cut  : {s:.1f}-{e:.1f} s ({e - s:.0f} s)   Layout: {p['layout']}")
    print(f"  │ Hook : {p['hook']}")
    if p["zooms"]:
        print("  │ Zoom : " + ", ".join(f"{z['t']:.1f}s {z['target']} x{z['scale']}" for z in p["zooms"]))
    if p.get("scenes"):
        def sd(x):
            extra = []
            if x.get("cam_ratio"):
                extra.append(f"camera {x['cam_ratio'] * 100:.0f}%")
            if x.get("zoom", 1) > 1.01:
                extra.append(f"zoom x{x['zoom']:.1f} @({x['focus_x']:.2f},{x['focus_y']:.2f})")
            return f"{x['t']:.1f}s {x['layout']}" + (f" [{', '.join(extra)}]" if extra else "")
        print("  │ Scenes: " + " → ".join(sd(x) for x in p["scenes"]))
    if p.get("stickers"):
        print("  │ Sticker: " + ", ".join(f"{x['t']:.1f}s '{x['text']}'" for x in p["stickers"]))
    if p.get("overlays"):
        print("  │ Image: " + ", ".join(f"{x['t']:.1f}s {x['image']}" for x in p["overlays"]))
    if p.get("sfx"):
        print("  │ SFX  : " + ", ".join(f"{x['t']:.1f}s {x['name']}" for x in p["sfx"]))
    if p.get("chat_show"):
        print("  │ Chat : " + ", ".join(f"{x['t']:.1f}s ({x['dur']:.0f} s)" for x in p["chat_show"]))
    print(f"  │ Title: {p['title']}")
    print("  └─")


def ask_user(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


# ---------- main flow ----------

def process_clip(ctx, clip, args, c):
    vod_dir, name = ctx["vod_dir"], f"clip_{clip['n']:02d}"
    clip_path = os.path.join(vod_dir, clip["file"])
    work = os.path.join(vod_dir, "work", name)
    dur = probe(clip_path)["duration"]
    print(f"\n=== {name}  (stream time {fmt(clip['start'])}, {dur:.0f} s) ===")

    print("  transcribing...")
    words = transcribe(clip_path, os.path.join(work, "words.json"), force=args.force_transcribe)["words"]
    fr = load_json(os.path.join(work, "frames.json"))
    if not fr:
        print("  extracting frames...")
        fr = make_frames(clip_path, work)
        save_json(os.path.join(work, "frames.json"), fr)
    images = fr["sheets"] + [fr["layout"]]

    layouts = load_json(LAYOUTS, {}) or {}
    pctx = {
        "streamer": ctx["streamer"], "title": ctx["title"], "game": ctx["game"],
        "transcript": transcript_text(words),
        "chat": chat_text(ctx["chat"], clip["start"], dur),
        "known_layout": layouts.get(ctx["login"]),
        "notes": brain.standing_notes(),
        "run_idea": args.idea,
    }
    plan_path = os.path.join(work, "plan.json")
    saved = load_json(plan_path)
    if saved and not args.replan and not args.idea:
        plan, model = saved["plan"], saved.get("model", "?")
        print("  (using the saved plan; add --replan to think again)")
    else:
        raw, model = brain.ask(brain.build_prompt(pctx, fr), work, images, args.brain)
        plan = brain.normalize(raw, dur)
        save_json(plan_path, {"plan": plan, "model": model})

    review = c["review_plan"] and not args.no_review
    while True:
        show_plan(name, plan, model)
        if not review:
            if not plan["keep"] or plan["score"] < c["min_score"]:
                print("  -> skipped (brain decision / low score)")
                return None
            break
        if plan["keep"] and plan["score"] >= c["min_score"]:
            ans = ask_user("  [Enter] approve  [s] skip  [k] frames  [a] open assets folder  or TYPE YOUR IDEA: ")
        else:
            ans = ask_user("  The brain suggests SKIPPING. [Enter] skip  [r] render anyway  [k] frames"
                           "  or TYPE YOUR IDEA: ")
            if ans == "":
                return None
            if ans.lower() == "r":
                plan["keep"], ans = True, ""
        if ans == "":
            break
        if ans.lower() == "s":
            return None
        if ans.lower() == "a":
            os.makedirs(ASSETS, exist_ok=True)
            open_file(ASSETS)
            print("  Drop the image in the folder, then type your idea (e.g. 'put file_name.png at 12 s').")
            continue
        if ans.lower() == "k":
            for im in images:
                open_file(os.path.join(work, im))
            continue
        raw, model = brain.ask(brain.build_prompt(pctx, fr, plan, ans), work, images, args.brain)
        plan = brain.normalize(raw, dur)
        save_json(plan_path, {"plan": plan, "model": model, "feedback": ans})

    if plan["layout"] == "stacked" and review:
        layouts[ctx["login"]] = {"layout": "stacked", "cam": plan["cam"], "game": plan["game"]}
        save_json(LAYOUTS, layouts)

    out_dir = os.path.join(SHORTS, datetime.date.today().isoformat(),
                           f"{ctx['login']}_{ctx['vod']}_{clip['n']:02d}")
    while True:
        print("  rendering...")
        out = render(clip_path, plan, words, out_dir, clip_chat(ctx["chat"], clip["start"], dur))
        print(f"  ✔ {out}")
        if not (c["review_render"] and not args.no_review):
            return out
        open_file(out)
        ans = ask_user("  Video opened. [Enter] OK  or TYPE YOUR IDEA (it will be re-edited): ")
        if not ans:
            return out
        raw, model = brain.ask(brain.build_prompt(pctx, fr, plan, ans), work, images, args.brain)
        plan = brain.normalize(raw, dur)
        plan["keep"] = True
        save_json(plan_path, {"plan": plan, "model": model, "feedback": ans})
        show_plan(name, plan, model)


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("vod", help="VOD id or link")
    ap.add_argument("--brain", choices=["auto", "claude", "gemini"], help="overrides config.json")
    ap.add_argument("--idea", "--fikir", dest="idea", default="", help="your editing idea for this run")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--only", default="", help="only these clips, e.g. 1,3")
    ap.add_argument("--redownload", action="store_true", help="download the clips again")
    ap.add_argument("--replan", action="store_true", help="ignore the saved plan")
    ap.add_argument("--force-transcribe", action="store_true")
    ap.add_argument("--no-review", action="store_true", help="no questions, do everything")
    args = ap.parse_args()
    c = config()

    vod = re.sub(r"\D", "", args.vod.rstrip("/").split("/")[-1]) or args.vod
    vod_dir = os.path.join(VODS, vod)
    clips = get_clips(vod_dir) if os.path.isdir(vod_dir) else []
    if args.redownload or not clips:
        print("[1] Finding hype moments and downloading them...")
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "highlights.py"), vod,
                            "--top", str(args.top)])
        if r.returncode != 0:
            sys.exit("highlights.py failed.")
        clips = get_clips(vod_dir)
    else:
        print(f"[1] {len(clips)} clips already downloaded (use --redownload to fetch again)")
    if args.only:
        keep = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
        clips = [cl for cl in clips if cl["n"] in keep]

    chat = load_json(os.path.join(vod_dir, "chat.json"), {}) or {}
    st = chat.get("streamer") or {}
    ctx = {"vod": vod, "vod_dir": vod_dir, "chat": chat,
           "streamer": st.get("name", "?"), "login": (st.get("login") or "unknown").lower(),
           "title": (chat.get("video") or {}).get("title", ""),
           "game": (chat.get("video") or {}).get("game", "")}

    print(f"[2] {ctx['streamer']} | {len(clips)} clips | brain: {args.brain or c['brain']}")
    if args.idea:
        print(f"    your idea: {args.idea}")
    done = []
    for cl in clips:
        try:
            out = process_clip(ctx, cl, args, c)
            if out:
                done.append(out)
        except brain.BrainError as e:
            print(f"  ✖ brain error, clip skipped:\n  {e}")
        except Exception as e:
            print(f"  ✖ error, clip skipped: {e}")
    print(f"\nDone: {len(done)} Shorts ready.")
    for d in done:
        print("  " + d)
    if done:
        open_file(os.path.dirname(done[0]))


if __name__ == "__main__":
    main()
