"""
Twitch VOD -> chat analysis -> download the most active moments as clips.

Usage:
    python highlights.py 2929768251
    python highlights.py 2929768251 --top 8 --pre 40 --post 25
    python highlights.py 2929768251 --dry-run      (list only, no video download)

Output: vods/<VOD_ID>/
    chat.json        - full chat (without embedded emote images, small)
    highlights.txt   - found moments + the chat messages at each moment
    clips.json       - clip list with start/end times
    clip_01_....mp4  - downloaded clips
"""
import argparse
import collections
import json
import os
import re
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TD = os.path.join(HERE, "TwitchDownloader", "TwitchDownloaderCLI.exe")
ROOT = os.path.dirname(HERE)

# Words that signal "something just happened" in chat (lower case).
HYPE = {
    "kekw", "lul", "lol", "lmao", "lmfao", "omegalul", "icant", "pog", "pogchamp",
    "poggers", "pogu", "wtf", "omg", "nooo", "noway", "monkas", "pepelaugh",
    "kappa", "w", "l", "clip", "clipit", "dead", "bruh", "aware", "sadge",
    "holy", "bro", "what", "huh", "gasp", "really", "😂", "💀", "🤣",
}
WORD = re.compile(r"[\w😂💀🤣]+", re.UNICODE)

BIN = 5  # seconds


def fmt(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def tag(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m{sec % 60:02d}s"


def run(cmd):
    print(">", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"ERROR: command exited with code {r.returncode}.")
    return r.returncode


def score_message(text):
    words = [w.lower() for w in WORD.findall(text or "")]
    hype = sum(1 for w in words if w in HYPE)
    caps = 1 if len(text or "") >= 4 and text.isupper() else 0
    return 1.0 + 0.5 * min(hype, 3) + 0.3 * caps


def find_highlights(comments, length, window, top, min_gap):
    nbins = int(length // BIN) + 2
    raw = [0.0] * nbins
    for c in comments:
        t = c.get("content_offset_seconds", 0)
        b = int(t // BIN)
        if 0 <= b < nbins:
            raw[b] += score_message(c.get("message", {}).get("body", ""))

    w = max(1, window // BIN)
    win = [sum(raw[i:i + w]) for i in range(nbins)]

    # Local baseline: median of the windows within +-5 min,
    # so generally busy hours of the stream are not favoured.
    span = 300 // BIN
    scores = []
    for i in range(nbins):
        lo, hi = max(0, i - span), min(nbins, i + span)
        base = statistics.median(win[lo:hi]) or 1.0
        scores.append((win[i] / base) * (win[i] ** 0.5))

    order = sorted(range(nbins), key=lambda i: -scores[i])
    picked = []
    for i in order:
        if len(picked) >= top:
            break
        if win[i] < 5:
            break
        t = i * BIN
        if all(abs(t - p) >= min_gap for p in picked):
            picked.append(t)
    return sorted(picked), win, w


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("vod", help="VOD id or link")
    ap.add_argument("--top", type=int, default=5, help="how many moments (5)")
    ap.add_argument("--window", type=int, default=30, help="chat counting window, s (30)")
    ap.add_argument("--pre", type=int, default=35, help="seconds before the peak (35)")
    ap.add_argument("--post", type=int, default=25, help="seconds after the window (25)")
    ap.add_argument("--gap", type=int, default=120, help="min seconds between moments (120)")
    ap.add_argument("--quality", default="1080p60", help="video quality (1080p60)")
    ap.add_argument("--dry-run", action="store_true", help="no video download, list only")
    ap.add_argument("--chat", help="use an existing chat.json (skip download)")
    a = ap.parse_args()

    vod = re.sub(r"\D", "", a.vod.rstrip("/").split("/")[-1]) or a.vod
    out = os.path.join(ROOT, "vods", vod)
    os.makedirs(out, exist_ok=True)

    chat_path = a.chat or os.path.join(out, "chat.json")
    if not a.chat:
        print(f"[1/3] Downloading chat ({vod})...")
        if run([TD, "chatdownload", "--id", vod, "-o", chat_path, "--collision", "Overwrite"]):
            sys.exit(1)

    with open(chat_path, encoding="utf-8") as f:
        data = json.load(f)
    comments = data.get("comments", [])
    length = data.get("video", {}).get("length") or max(
        (c["content_offset_seconds"] for c in comments), default=0)
    streamer = (data.get("streamer") or {}).get("name", "?")
    title = data.get("video", {}).get("title", "")
    print(f"[2/3] {streamer} | {fmt(length)} | {len(comments)} messages, analysing...")

    peaks, win, w = find_highlights(comments, length, a.window, a.top, a.gap)
    if not peaks:
        print("No clear moment found (chat too quiet).")
        sys.exit(0)

    lines = [f"{streamer} - {title}", f"VOD: https://www.twitch.tv/videos/{vod}", ""]
    clips = []
    for n, t in enumerate(peaks, 1):
        start = max(0, t - a.pre)
        end = min(length, t + a.window + a.post)
        msgs = [c for c in comments if t <= c["content_offset_seconds"] < t + a.window]
        sample = [m.get("message", {}).get("body", "") for m in msgs][:15]
        name = f"clip_{n:02d}_{tag(start)}.mp4"
        clips.append((name, start, end))
        lines.append(f"#{n}  {fmt(start)} - {fmt(end)}  ({len(msgs)} messages / {a.window} s)  -> {name}")
        lines.append(f"    link: https://www.twitch.tv/videos/{vod}?t={tag(start)}")
        for s in sample:
            lines.append(f"      | {s}")
        lines.append("")

    report = "\n".join(lines)
    with open(os.path.join(out, "highlights.txt"), "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    if a.dry_run:
        print("--dry-run: no video downloaded.")
        return

    print(f"[3/3] {len(clips)} clips downloading ({a.quality})...")
    for name, start, end in clips:
        run([TD, "videodownload", "--id", vod, "-b", str(int(start)), "-e", str(int(end)),
             "-q", a.quality, "--collision", "Overwrite", "-o", os.path.join(out, name)])
    info = [{"n": i + 1, "file": nm, "start": st, "end": en, "peak": pk}
            for i, ((nm, st, en), pk) in enumerate(zip(clips, peaks))]
    with open(os.path.join(out, "clips.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    print(f"Done. Folder: {out}")


if __name__ == "__main__":
    main()
