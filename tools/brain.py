"""Brain: frames + transcript + chat -> edit plan (JSON).

DEFAULT: Gemini through the Antigravity CLI ("agy", model from "agy_model" in config.json).
Chosen with "brain" in config.json:
  "gemini"     -> Gemini via Antigravity CLI (default; "antigravity"/"agy" are aliases)
  "claude"     -> Claude Code CLI (Claude Pro/Max login)    command: claude
  "gemini_cli" -> the older Gemini CLI                      command: gemini
  "auto"       -> tries the "order" list; on a limit/error it falls back to the next one
"""
import json
import os
import re
import shutil
import subprocess

from shorts_common import (ASSETS, IMG_EXT, TOOLS, config, list_files, read_text,
                           sfx_catalog)

QUOTA_RE = re.compile(r"usage limit|rate.?limit|quota|exhaust|429|limit reached|too many requests|"
                      r"resets? at|credit balance|out of (?:credits|tokens)|RESOURCE_EXHAUSTED", re.I)


class BrainError(Exception):
    pass


class QuotaError(BrainError):
    pass


SCHEMA = """{
  "keep": true,
  "score": 7.8,
  "reason": "one sentence: why",
  "cut": {"start": 12.0, "end": 41.5},
  "layout": "stacked | fullcam | game",
  "cam": [0.70, 0.05, 0.28, 0.30],
  "game": [0.0, 0.0, 1.0, 1.0],
  "focus_x": 0.5,
  "focus_y": 0.5,
  "zoom": 1.0,
  "cam_ratio": 0.32,
  "scenes": [{"t": 28.0, "layout": "stacked", "cam_ratio": 0.55},
             {"t": 31.5, "layout": "game_full", "focus_x": 0.3, "focus_y": 0.6, "zoom": 1.6},
             {"t": 35.0, "layout": "stacked", "cam_ratio": 0.32}],
  "hook": "HE DID NOT SEE THAT COMING",
  "zooms": [{"t": 30.2, "dur": 1.2, "target": "game", "scale": 1.25}],
  "emphasis": ["NO", "WAY"],
  "overlays": [{"t": 23.4, "dur": 2.0, "image": "exact_file_name.png", "pos": "top", "size": 0.6}],
  "stickers": [{"t": 23.4, "dur": 1.5, "text": "PRO PLAYER?", "pos": "center", "color": "yellow"}],
  "sfx": [{"t": 30.2, "name": "boom", "vol": 0.8}],
  "chat_show": [{"t": 33.0, "dur": 3.5}],
  "title": "... #shorts",
  "description": "...",
  "hashtags": ["#gta6", "#shorts"]
}"""


def build_prompt(ctx, frames, prev_plan=None, feedback=None):
    lines = [
        'You are the editor of the YouTube Shorts channel "Wanted Stream Clips" '
        "(audience: US gamers, English). Below is a raw clip from a Twitch stream where chat "
        "activity spiked. Turn it into the best possible standalone vertical Short, or reject it.",
        "",
        f"Streamer: {ctx['streamer']} | Stream title: {ctx['title']} | Game: {ctx['game']}",
        f"Clip length: {frames['duration']:.1f} s (all times below are seconds from clip start)",
        "",
        "IMAGES (read them before deciding):",
    ]
    for s in frames["sheets"]:
        lines.append(f"- {s}: contact sheet, one frame every {frames['step']:.0f} s, "
                     "timestamp in the top-left of each tile, left-to-right then top-to-bottom")
    lines.append(f"- {frames['layout']}: full frame at t={frames['layout_t']} s with a grid; "
                 "labels are % of width (top) and % of height (left)")
    imgs = list_files(ASSETS, IMG_EXT)
    sfx = sfx_catalog()
    lines += ["", "AVAILABLE IMAGES (assets folder, the file name describes the image): "
              + (", ".join(imgs) if imgs else "(none)"),
              "AVAILABLE SOUND EFFECTS: "
              + ("; ".join(f"{k} = {v}" for k, v in sfx.items()) if sfx else "(none)")]
    lines += ["", "TRANSCRIPT:", ctx["transcript"] or "(no speech detected)", "",
              "CHAT DURING THE CLIP:", ctx["chat"] or "(none)", ""]
    if ctx.get("known_layout"):
        lines += ["Previously confirmed layout for this streamer (use it if the frames still "
                  "look the same): " + json.dumps(ctx["known_layout"]), ""]
    if ctx.get("notes"):
        lines += ["STANDING NOTES FROM THE CHANNEL OWNER (always follow, they override defaults):",
                  ctx["notes"], ""]
    if ctx.get("run_idea"):
        lines += ["IDEA FROM THE OWNER FOR THIS RUN (follow it):", ctx["run_idea"], ""]
    if prev_plan is not None:
        lines += ["REVISION REQUEST. Your previous plan was:", json.dumps(prev_plan, ensure_ascii=False),
                  "The owner's feedback (may be in Turkish) - apply it and return the full revised plan:",
                  feedback or "", ""]
    lines += [
        "DECIDE:",
        "1. keep/score: would a US viewer who doesn't know this streamer enjoy it as a standalone "
        "Short? score 1-10 with ONE DECIMAL (e.g. 6.4, 7.3, 8.1) and be strict: the channel only "
        f"publishes clips scoring {ctx.get('min_score', 7.5)} or higher, so give that only to moments "
        "with a clear, instantly understandable payoff (a big fail/win, a hilarious reaction, a "
        "shocking moment). Average funny moments are 5-7. Reject (keep=false) boring, "
        "context-heavy or ad/sponsor moments.",
        "2. cut: 15-45 s. Start just before the setup (no dead air), end right after the "
        "payoff/reaction. Never cut mid-sentence (use the transcript times).",
        "3. layout: 'stacked' = gameplay with a facecam box -> facecam on top, gameplay below; "
        "'fullcam' = no gameplay / just chatting, camera fills the screen; "
        "'game' = gameplay without facecam. "
        "cam = [x, y, w, h] of the facecam box as fractions 0-1 of the full frame (stacked only). "
        "game = [x, y, w, h] of the gameplay area to show (stacked/game). "
        "focus_x = 0-1 horizontal centre of the main subject (fullcam/game). "
        "Keep chat boxes, alerts and ads out of the boxes when possible.",
        "3a. Layout extras: 'game_full' = gameplay fills the whole vertical screen (narrow slice, "
        "aim it with focus_x). zoom 1.0-2.5 = static zoom into the gameplay area around "
        "(focus_x, focus_y) (fractions of the full frame). cam_ratio 0.2-0.6 = share of the "
        "screen height the facecam gets in 'stacked' (default ~0.32).",
        "3b. scenes: 0-4 changes DURING the clip (hard cuts). Each scene starts at t (seconds from "
        "clip start) and lasts until the next one; any field not given is taken from the base "
        "layout. Use them when the attention moves: make the camera big (cam_ratio 0.5-0.6 or "
        "layout fullcam) when the streamer reacts hard; switch to game_full or zoom/focus on "
        "where the action is in the game (a crash on the left -> focus_x 0.3, zoom 1.6); then go "
        "back. Don't change more often than every ~3 s; a whoosh sfx on a change works well. "
        "If nothing changes, return an empty list.",
        "4. hook: 2-6 words, ALL CAPS, shown for the first seconds. Tease the payoff, don't spoil "
        "it, no lies, no emojis.",
        "5. zooms: 0-3 punch-ins on the key moments: t (seconds from clip start), dur 0.6-2.0, "
        "target game|cam|all, scale 1.1-1.4.",
        "6. emphasis: up to 6 single words from the transcript to highlight.",
        "6a. overlays: 0-2 images shown on top of the video at a moment (meme / reference). "
        "ONLY use exact file names from the AVAILABLE IMAGES list; pos top|center|bottom, "
        "size 0.3-0.9 (fraction of screen width), dur 1-3 s. If the owner asks for an image "
        "that is not in the list, use a sticker instead and say so in 'reason'.",
        "6b. stickers: 0-2 short text stickers (1-4 words, ALL CAPS) that pop on screen to punch "
        "a joke, e.g. 'PRO PLAYER?', 'BRO WHAT'. pos top|center|bottom, color "
        "yellow|red|white|green, dur 1-2.5 s. Don't repeat the captions.",
        "6c. sfx: 2-6 sound effects from the AVAILABLE SOUND EFFECTS list (exact name), on "
        "zooms, punchlines, stickers/overlays and the hook. vol 0.3-1.0. Don't spam.",
        "6d. chat_show: 0-2 moments to show the real Twitch chat on screen for 3-4 s, when chat "
        "laughs or reacts hard (KEKW, LUL, LMAO, WHAT...). Chat reacts 2-6 s after the moment, "
        "so put t where the reaction messages are.",
        "7. title <= 60 chars ending with #shorts (no lies), description = 2 sentences + a "
        "question for comments, 5-8 hashtags.",
        f"8. reason: one short sentence in {config().get('reason_language', 'English')} explaining your decision.",
        "",
        "Return ONLY one JSON object, no markdown, exactly with these keys:",
        SCHEMA,
    ]
    return "\n".join(lines)


def extract_json(text):
    text = re.sub(r"```(?:json)?", "", text or "")
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise BrainError("The model did not return JSON: " + (text or "")[:300])
    try:
        return json.loads(text[a:b + 1])
    except json.JSONDecodeError as e:
        raise BrainError(f"Could not parse JSON ({e}): " + text[a:a + 300])


def _run(cmd, prompt, cwd, timeout):
    exe = shutil.which(cmd[0])
    if not exe:
        raise BrainError(f"'{cmd[0]}' command not found (is it installed?)")
    try:
        r = subprocess.run([exe] + cmd[1:], input=prompt, cwd=cwd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BrainError(f"{cmd[0]} did not answer within {timeout} s")
    return r


def _raise_for(name, r, text):
    blob = (text or "") + "\n" + (r.stderr or "") + "\n" + (r.stdout or "")
    if QUOTA_RE.search(blob):
        raise QuotaError(f"{name}: usage limit reached ({blob.strip()[:200]})")
    raise BrainError(f"{name} failed (code {r.returncode}): {blob.strip()[:400]}")


def call_claude(prompt, cwd, c):
    cmd = [c["claude_cmd"], "-p", "--output-format", "json", "--allowedTools", "Read"]
    if c.get("claude_model"):
        cmd += ["--model", c["claude_model"]]
    r = _run(cmd, prompt, cwd, c["brain_timeout_sec"])
    text = r.stdout
    try:
        j = json.loads(r.stdout)
        text = j.get("result", "") or ""
        if j.get("is_error"):
            _raise_for("claude", r, text)
    except json.JSONDecodeError:
        pass
    if r.returncode != 0:
        _raise_for("claude", r, text)
    return text


def call_gemini(prompt, cwd, c, images):
    refs = " ".join("@" + i for i in images)
    head = f"Follow the instructions given on stdin. Attached images: {refs}"
    cmd = [c["gemini_cmd"], "-p", head, "--output-format", "json"]
    if c.get("gemini_model"):
        cmd += ["-m", c["gemini_model"]]
    r = _run(cmd, prompt, cwd, c["brain_timeout_sec"])
    if r.returncode != 0 and re.search(r"unknown (argument|option)|output-format", r.stderr or "", re.I):
        cmd = [x for x in cmd if x not in ("--output-format", "json")]  # older CLI
        r = _run(cmd, prompt, cwd, c["brain_timeout_sec"])
    text = r.stdout
    try:
        j = json.loads(r.stdout)
        if j.get("error"):
            _raise_for("gemini", r, json.dumps(j["error"]))
        text = j.get("response", "") or ""
    except json.JSONDecodeError:
        pass
    if r.returncode != 0:
        _raise_for("gemini", r, text)
    return text


def call_antigravity(prompt, cwd, c, images):
    """Antigravity CLI (agy), Gemini's newer CLI. The prompt is written to a file and agy is
    told to read it (the Windows command line can't take a prompt this long)."""
    pf = os.path.join(cwd, "agy_prompt.md")
    with open(pf, "w", encoding="utf-8") as f:
        f.write(prompt)
    imgs = ", ".join(os.path.basename(i) if os.path.dirname(os.path.abspath(i)) == os.path.abspath(cwd)
                     else i for i in images)
    head = ("Read the file agy_prompt.md in the current folder and follow its instructions exactly. "
            f"Look at these image files before deciding: {imgs}. "
            "Do not create or modify any files. Reply with only the JSON object.")
    cmd = [c.get("agy_cmd") or "agy", "-p", head, "--output-format", "json"]
    if c.get("agy_model"):
        cmd += ["--model", c["agy_model"]]
    if c.get("agy_effort"):
        cmd += ["--effort", c["agy_effort"]]
    if c.get("agy_skip_permissions"):
        cmd += ["--dangerously-skip-permissions"]
    r = _run(cmd, "", cwd, c["brain_timeout_sec"])
    text = r.stdout
    try:
        j = json.loads(r.stdout)
        if str(j.get("status", "")).upper() in ("ERROR", "FAILED"):
            _raise_for("antigravity", r, json.dumps(j)[:600])
        text = j.get("response", "") or ""
        if not isinstance(text, str):
            text = json.dumps(text)
    except json.JSONDecodeError:
        pass
    if r.returncode != 0:
        _raise_for("antigravity", r, text)
    return text


def ask(prompt, cwd, images, brain=None):
    """Returns (plan, brain_used). In auto mode tries each brain in order."""
    c = config()
    brain = brain or c["brain"]
    order = c["order"] if brain == "auto" else [brain]
    errors = []
    for name in order:
        try:
            label = "gemini (agy)" if name in ("gemini", "antigravity", "agy") else name
            print(f"  brain: {label} is thinking...", flush=True)
            if name == "claude":
                text = call_claude(prompt, cwd, c)
            elif name == "gemini_cli":
                text = call_gemini(prompt, cwd, c, images)
            elif name in ("gemini", "antigravity", "agy"):
                text = call_antigravity(prompt, cwd, c, images)
            else:
                raise BrainError(f"unknown brain: {name}")
            return extract_json(text), name
        except BrainError as e:
            kind = "LIMIT" if isinstance(e, QuotaError) else "ERROR"
            print(f"  [{kind}] {e}")
            errors.append(str(e))
            if len(order) > 1:
                print("  -> falling back to the next brain")
    raise BrainError("No brain could produce a plan:\n  " + "\n  ".join(errors))


def normalize(plan, duration):
    p = dict(plan or {})
    p["keep"] = bool(p.get("keep", True))
    try:
        p["score"] = float(p.get("score", 0))
    except (TypeError, ValueError):
        p["score"] = 0.0
    cut = p.get("cut") or {}
    s = max(0.0, float(cut.get("start", 0) or 0))
    e = min(duration, float(cut.get("end", duration) or duration))
    if e - s < 8:
        s, e = max(0.0, min(s, duration - 30)), min(duration, max(s, 0) + 30)
    if e - s > 60:
        e = s + 59
    p["cut"] = {"start": round(s, 2), "end": round(e, 2)}
    LAYS = ("stacked", "fullcam", "game", "game_full")
    if p.get("layout") not in LAYS:
        p["layout"] = "fullcam"

    def box(v, d):
        try:
            x, y, w, h = [min(1.0, max(0.0, float(t))) for t in v]
            if w > 0.03 and h > 0.03:
                return [x, y, min(w, 1 - x), min(h, 1 - y)]
        except Exception:
            pass
        return d

    p["game"] = box(p.get("game"), [0.0, 0.0, 1.0, 1.0])
    p["cam"] = box(p.get("cam"), None)
    if p["layout"] == "stacked" and not p["cam"]:
        p["layout"] = "game"
    def f01(v, d, lo=0.0, hi=1.0):
        try:
            return min(hi, max(lo, float(v)))
        except (TypeError, ValueError):
            return d

    p["focus_x"] = f01(p.get("focus_x"), 0.5)
    p["focus_y"] = f01(p.get("focus_y"), 0.5)
    p["zoom"] = f01(p.get("zoom"), 1.0, 1.0, 2.5)
    p["cam_ratio"] = f01(p.get("cam_ratio"), None, 0.2, 0.6) if p.get("cam_ratio") is not None else None

    # Scenes: layout / focus / zoom changes inside the clip
    sc_out = []
    for sc in sorted((x for x in (p.get("scenes") or []) if isinstance(x, dict) and "t" in x),
                     key=lambda x: f01(x.get("t"), 0, 0, 9999))[:5]:
        t = f01(sc.get("t"), None, 0, 9999)
        if t is None or not (p["cut"]["start"] < t < p["cut"]["end"]):
            continue
        lay = sc.get("layout") if sc.get("layout") in LAYS else p["layout"]
        cam = box(sc.get("cam"), p["cam"])
        if lay == "stacked" and not cam:
            continue
        sc_out.append({
            "t": round(t, 2), "layout": lay, "cam": cam,
            "game": box(sc.get("game"), p["game"]),
            "focus_x": f01(sc.get("focus_x"), p["focus_x"]),
            "focus_y": f01(sc.get("focus_y"), p["focus_y"]),
            "zoom": f01(sc.get("zoom"), 1.0, 1.0, 2.5),
            "cam_ratio": f01(sc.get("cam_ratio"), None, 0.2, 0.6) if sc.get("cam_ratio") is not None else None,
        })
    p["scenes"] = sc_out
    zs = []
    for z in (p.get("zooms") or [])[:3]:
        try:
            zs.append({"t": float(z["t"]), "dur": min(2.5, max(0.4, float(z.get("dur", 1)))),
                       "target": z.get("target", "all") if z.get("target") in ("game", "cam", "all") else "all",
                       "scale": min(1.5, max(1.05, float(z.get("scale", 1.2))))})
        except Exception:
            pass
    p["zooms"] = zs

    def num(v, d, lo, hi):
        try:
            return min(hi, max(lo, float(v)))
        except (TypeError, ValueError):
            return d

    imgs = {f.lower(): f for f in list_files(ASSETS, IMG_EXT)}
    ov = []
    for o in (p.get("overlays") or [])[:3]:
        if not isinstance(o, dict):
            continue
        f = imgs.get(str(o.get("image", "")).lower())
        if f and "t" in o:
            ov.append({"t": num(o["t"], 0, 0, 9999), "dur": num(o.get("dur"), 2, 0.5, 4),
                       "image": f, "pos": o.get("pos") if o.get("pos") in ("top", "center", "bottom") else "center",
                       "size": num(o.get("size"), 0.6, 0.2, 1.0)})
    p["overlays"] = ov
    st = []
    for o in (p.get("stickers") or [])[:3]:
        if isinstance(o, dict) and o.get("text") and "t" in o:
            st.append({"t": num(o["t"], 0, 0, 9999), "dur": num(o.get("dur"), 1.5, 0.5, 3),
                       "text": str(o["text"]).upper()[:30],
                       "pos": o.get("pos") if o.get("pos") in ("top", "center", "bottom") else "center",
                       "color": o.get("color") if o.get("color") in ("yellow", "red", "white", "green") else "yellow"})
    p["stickers"] = st
    names = {k.lower() for k in sfx_catalog()}
    sx = []
    for o in (p.get("sfx") or [])[:8]:
        if isinstance(o, dict) and str(o.get("name", "")).lower() in names and "t" in o:
            sx.append({"t": num(o["t"], 0, 0, 9999), "name": str(o["name"]).lower(),
                       "vol": num(o.get("vol"), 0.8, 0.1, 1.0)})
    p["sfx"] = sx
    cs = []
    for o in (p.get("chat_show") or [])[:2]:
        if isinstance(o, dict) and "t" in o:
            cs.append({"t": num(o["t"], 0, 0, 9999), "dur": num(o.get("dur"), 3.5, 2, 5)})
    p["chat_show"] = cs
    p["score"] = round(min(10.0, max(0.0, p["score"])), 1)
    p["hook"] = str(p.get("hook", "")).upper()[:60]
    p["emphasis"] = [str(w).upper() for w in (p.get("emphasis") or [])][:8]
    for k in ("title", "description", "reason"):
        p[k] = str(p.get(k, ""))
    p["hashtags"] = [str(h) for h in (p.get("hashtags") or [])]
    return p


def standing_notes():
    raw = read_text(os.path.join(TOOLS, "editor_notes.txt")) or read_text(os.path.join(TOOLS, "kurgu_notlari.txt"))
    return "\n".join(l for l in raw.splitlines() if l.strip() and not l.lstrip().startswith("#"))
