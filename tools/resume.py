"""
RESUME: continues clipping half-finished streams from their last clip.

Usage:
    python resume.py                 every half-finished VOD of the last days (found in vods/)
    python resume.py 2932113307      only this VOD
    python resume.py --list          only shows the status, downloads nothing
    python resume.py --top 8         at most 8 new moments per VOD (default: based on the time left)
    python resume.py --no-render     only download the clips, don't make Shorts

For each VOD:
  1) Clips that were downloaded but never processed (no plan) -> Short queue.
  2) If the stream is STILL LIVE: opens live.py <streamer> --render --resume in a new window.
     If it ENDED: downloads the chat from the end of the last clip to the end of the VOD,
     finds the most active moments, downloads them and adds them to clips.json (numbering continues).
  3) run.py <VOD> --only <new + unprocessed> --no-review  (only clips above min_score become Shorts)
"""
import argparse
import json
import os
import subprocess
import sys
import time

from highlights import TD, find_highlights, fmt, tag
from shorts_common import TOOLS, VODS, load_json, save_json, utf8_console

GAP = 150
WINDOW, PRE, POST = 30, 35, 25


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def is_record_folder(vod):
    return vod.startswith("9") and len(vod) >= 13      # live.py recording-mode folder (no VOD)


def candidates(days):
    out = []
    if not os.path.isdir(VODS):
        return out
    for vod in os.listdir(VODS):
        p = os.path.join(VODS, vod, "clips.json")
        if vod.isdigit() and os.path.exists(p) and time.time() - os.path.getmtime(p) < days * 86400:
            out.append(vod)
    return sorted(out, key=lambda v: os.path.getmtime(os.path.join(VODS, v, "clips.json")))


def unplanned(vod_dir, clips):
    return [c["n"] for c in clips
            if os.path.exists(os.path.join(vod_dir, c["file"]))
            and not os.path.exists(os.path.join(vod_dir, "work", f"clip_{c['n']:02d}", "plan.json"))]


def live_state(login, vod):
    """'live' = this VOD is still live, 'other' = the streamer is live with another stream, 'off' = offline"""
    try:
        from live import live_vod, stream_info
        is_live, started, _, _ = stream_info(login)
        if not is_live:
            return "off"
        return "live" if live_vod(login, started) == vod else "other"
    except SystemExit as e:          # no yt-dlp
        log(f"   (live check not possible: {e}) -> treating it as ended")
        return "off"
    except Exception as e:
        log(f"   (live check failed: {e!r}) -> treating it as ended")
        return "off"


def open_live_window(login):
    cmd = [sys.executable, os.path.join(TOOLS, "live.py"), login, "--render", "--resume"]
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(cmd, cwd=TOOLS, creationflags=flags)


def download_chat(vod, frm, path):
    base = [TD, "chatdownload", "--id", vod, "-b", str(int(frm)), "-o", path, "--collision", "Overwrite"]
    r = subprocess.run(base + ["--threads", "12"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        r = subprocess.run(base, capture_output=True, text=True, encoding="utf-8", errors="replace")
    data = load_json(path, None)
    if r.returncode != 0 or not data:
        err = (r.stderr or r.stdout or "?").strip().splitlines()[-1:]
        raise RuntimeError(f"chat download failed: {err}")
    return data


def merge_chat(vod_dir, new_comments, login, title, vod, length):
    path = os.path.join(vod_dir, "chat.json")
    old = load_json(path, {}) or {}
    have = {(c.get("content_offset_seconds"), (c.get("message") or {}).get("body"))
            for c in old.get("comments", [])}
    add = [c for c in new_comments
           if (c["content_offset_seconds"], c["message"]["body"]) not in have]
    merged = sorted(old.get("comments", []) + add, key=lambda c: c.get("content_offset_seconds", 0))
    old.setdefault("streamer", {"name": login, "login": login})
    video = old.setdefault("video", {"title": title, "id": vod})
    video["length"] = max(video.get("length") or 0, length)
    old["comments"] = merged
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return len(add)


def resume_vod(vod, a):
    vod_dir = os.path.join(VODS, vod)
    clips = load_json(os.path.join(vod_dir, "clips.json"), []) or []
    chat = load_json(os.path.join(vod_dir, "chat.json"), {}) or {}
    st = chat.get("streamer") or {}
    login = (st.get("login") or st.get("name") or "?").lower()
    title = (chat.get("video") or {}).get("title", "")
    last = max([c.get("end", c.get("start", 0)) for c in clips], default=0)
    todo = unplanned(vod_dir, clips)
    log(f"■ {login} | VOD {vod} | {len(clips)} clips | last clip ends at {fmt(last)}"
        + (f" | unprocessed clips: {', '.join(map(str, todo))}" if todo else ""))
    if a.list:
        return

    new = []
    if is_record_folder(vod):
        log("   recording-mode folder (the streamer has no VOD): can't go back in time, only unprocessed clips will be done.")
    else:
        state = live_state(login, vod)
        if state == "live":
            log(f"   stream is STILL LIVE -> in a new window: live.py {login} --render --resume")
            open_live_window(login)
            # the live window renders its own clips; here only the unprocessed ones
        else:
            if state == "other":
                log(f"   {login} is live with a NEW stream; this VOD has ended, scanning its rest."
                    f" (For the new stream: live.bat {login} --render)")
            new = scan_rest(vod, vod_dir, clips, last, login, title, a)

    targets = sorted(set(todo + new))
    if not targets:
        log("   no new clips to do.")
        return
    if a.no_render:
        log(f"   --no-render: clips ready ({', '.join(map(str, targets))}), not turned into Shorts.")
        return
    log(f"   🎬 making Shorts: {', '.join(map(str, targets))} (only those above min_score)")
    subprocess.run([sys.executable, os.path.join(TOOLS, "run.py"), vod, "--only",
                    ",".join(map(str, targets)), "--no-review"], cwd=TOOLS)


def scan_rest(vod, vod_dir, clips, last, login, title, a):
    frm = max(0, int(last) - 60)        # start 1 min earlier so the baseline has data
    path = os.path.join(vod_dir, "chat_resume.json")
    log(f"   ⏪ downloading chat from {fmt(frm)} to the end of the VOD (may take a few minutes for big chats)...")
    try:
        data = download_chat(vod, frm, path)
    except RuntimeError as e:
        log(f"   ✖ {e}")
        return []
    v = data.get("video") or {}
    comments = [{"content_offset_seconds": c.get("content_offset_seconds", 0),
                 "commenter": {"display_name": (c.get("commenter") or {}).get("display_name", "")},
                 "message": {"body": (c.get("message") or {}).get("body", ""),
                             "user_color": (c.get("message") or {}).get("user_color")}}
                for c in data.get("comments", []) if c.get("content_offset_seconds", 0) >= frm]
    end = v.get("end") or v.get("length") or max((c["content_offset_seconds"] for c in comments), default=0)
    end = int(end)
    rest = end - last
    log(f"   VOD is {fmt(end)} long; part to scan {fmt(rest)} ({len(comments)} messages)")
    if rest < 180 or not comments:
        log("   already at the end of the stream.")
        return []
    merge_chat(vod_dir, comments, login, title, vod, end)

    top = a.top or max(3, min(20, int(rest // 900)))
    shifted = [dict(c, content_offset_seconds=c["content_offset_seconds"] - frm) for c in comments]
    peaks, seen = [], set()
    old_peaks = [c.get("peak", c.get("start", 0) + PRE) for c in clips]
    for k in range(1, top * 4 + 1):
        got = set(find_highlights(shifted, end - frm, WINDOW, k, GAP)[0])
        fresh = got - seen
        if not fresh:
            break
        seen |= got
        p = fresh.pop() + frm
        if p < last - 20 or p + WINDOW + POST > end:
            continue
        if any(abs(p - q) < GAP for q in old_peaks + peaks):
            continue
        peaks.append(p)
        if len(peaks) >= top:
            break
    if not peaks:
        log("   no clear moment in the rest of the stream.")
        return []
    log(f"   {len(peaks)} new moment(s): " + ", ".join(fmt(p) for p in sorted(peaks)))

    n = max([c["n"] for c in clips], default=0)
    hl = os.path.join(vod_dir, "highlights.txt")
    made = []
    for peak in sorted(peaks):
        n += 1
        start, stop = max(0, peak - PRE), min(end, peak + WINDOW + POST)
        name = f"clip_{n:02d}_{tag(start)}.mp4"
        out = os.path.join(vod_dir, name)
        log(f"   ⬇ #{n} {fmt(start)}-{fmt(stop)} downloading...")
        ok = False
        for attempt in (1, 2):
            r = subprocess.run([TD, "videodownload", "--id", vod, "-b", str(int(start)), "-e", str(int(stop)),
                                "-q", a.quality, "--collision", "Overwrite", "-o", out],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0 and os.path.exists(out):
                ok = True
                break
            log(f"   ✖ #{n} download failed (attempt {attempt}): "
                f"{((r.stderr or r.stdout or '?').strip().splitlines() or ['?'])[-1]}")
            time.sleep(10)
        if not ok:
            n -= 1
            continue
        clips.append({"n": n, "file": name, "start": start, "end": stop, "peak": peak})
        clips.sort(key=lambda c: c["n"])
        save_json(os.path.join(vod_dir, "clips.json"), clips)
        msgs = [c["message"]["body"] for c in comments if peak <= c["content_offset_seconds"] < peak + WINDOW][:15]
        with open(hl, "a", encoding="utf-8") as f:
            f.write(f"#{n}  {fmt(start)} - {fmt(stop)}  -> {name}  (resume)\n"
                    f"    link: https://www.twitch.tv/videos/{vod}?t={tag(start)}\n"
                    + "".join(f"      | {m}\n" for m in msgs) + "\n")
        made.append(n)
        log(f"   ✔ #{n} ready")
    return made


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("vods", nargs="*", help="VOD ids (empty = all from the last days)")
    ap.add_argument("--list", action="store_true", help="only show the status")
    ap.add_argument("--top", type=int, default=0, help="max new moments per VOD (0 = automatic)")
    ap.add_argument("--days", type=float, default=4, help="without VOD ids: VODs of the last N days (4)")
    ap.add_argument("--quality", default="1080p60")
    ap.add_argument("--no-render", action="store_true", help="only download, don't make Shorts")
    a = ap.parse_args()
    vods = [v.rstrip("/").split("/")[-1] for v in a.vods] or candidates(a.days)
    if not vods:
        sys.exit("No VOD to resume (no clips.json in the vods folder).")
    log(f"{len(vods)} VOD: {', '.join(vods)}")
    for v in vods:
        try:
            resume_vod(v, a)
        except Exception as e:
            log(f"✖ {v} error: {e!r}")
    log("Done.")


if __name__ == "__main__":
    main()
