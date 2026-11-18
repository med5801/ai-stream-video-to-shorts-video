"""
LIVE MODE: listens to chat while the stream is running and, when chat explodes,
cuts that moment from the live VOD a couple of minutes later.

Usage:
    python live.py xqc                    clips only (vods/<VOD_ID>/clip_XX.mp4)
    python live.py xqc --render           also turns every clip into a Short (run.py --no-review)
    python live.py xqc --ratio 2.5        more selective (default 2.2)
    python live.py xqc --test             force one clip right after warm-up (pipeline test)

How it works:
  1) yt-dlp: checks the stream is live, gets its start time and the live VOD id.
  2) Joins Twitch chat anonymously (no account, read-only IRC).
  3) Every 5 s, compares the chat score of the last 30 s with the median of the last 5 min.
     A clear spike marks a moment (it watches 30 s more and keeps the strongest window).
  4) Waits until Twitch has written that part to the VOD (~1.5 min), then downloads only that part.
  5) Writes chat.json / clips.json / highlights.txt in the same format as highlights.py,
     so you can also run run.bat <VOD_ID> later.
Stop with Ctrl+C (pending clips are cancelled, downloaded ones stay).
"""
import argparse
import collections
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time

from highlights import TD, fmt, score_message, tag
from shorts_common import TOOLS, VODS, load_json, save_json, utf8_console
from watcher import ytdlp_cmd

BIN = 5
LAG = 90          # margin for Twitch's delay in writing the live VOD (s)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------- stream info ----------

def stream_info(login):
    """(is_live, start_unix, title, category)"""
    cmd = ytdlp_cmd()
    if not cmd:
        sys.exit("yt-dlp not found: pip install yt-dlp")
    r = subprocess.run(cmd + ["-J", "--skip-download", f"https://www.twitch.tv/{login}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return False, None, "", ""
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return False, None, "", ""
    title = d.get("description") or d.get("title") or ""
    return bool(d.get("is_live")), d.get("timestamp"), title, ""


def live_vod(login, started):
    """VOD id of the live stream's archive (None if there is none)."""
    cmd = ytdlp_cmd()
    url = f"https://www.twitch.tv/{login}/videos?filter=archives&sort=time"
    r = subprocess.run(cmd + ["--flat-playlist", "-J", "--playlist-end", "1", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        e = (json.loads(r.stdout).get("entries") or [None])[0]
    except (json.JSONDecodeError, AttributeError):
        return None
    if not e:
        return None
    vid = str(e.get("id", "")).lstrip("v")
    dur = e.get("duration")
    # If the newest VOD's length is close to the stream's uptime, it is the live VOD.
    if started and dur and abs((time.time() - started) - dur) > 20 * 60:
        return None
    return vid if vid.isdigit() else None


# ---------- chat (anonymous IRC) ----------

class Chat(threading.Thread):
    def __init__(self, login, started, sink):
        super().__init__(daemon=True)
        self.login, self.started, self.sink = login, started, sink
        self.stop = False

    def run(self):
        while not self.stop:
            try:
                self._session()
            except Exception as e:
                log(f"chat connection lost ({e}), retrying in 5 s...")
                time.sleep(5)

    def _session(self):
        s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=300)
        f = s.makefile("r", encoding="utf-8", errors="replace")
        for line in ("CAP REQ :twitch.tv/tags", "PASS SCHMOOPIIE",
                     f"NICK justinfan{int(time.time()) % 90000 + 10000}", f"JOIN #{self.login}"):
            s.sendall((line + "\r\n").encode())
        log(f"joined chat: #{self.login}")
        for raw in f:
            if self.stop:
                break
            raw = raw.rstrip("\r\n")
            if raw.startswith("PING"):
                s.sendall(("PONG" + raw[4:] + "\r\n").encode())
                continue
            if " PRIVMSG #" not in raw:
                continue
            tags = {}
            if raw.startswith("@"):
                t, raw = raw[1:].split(" ", 1)
                tags = dict(kv.split("=", 1) for kv in t.split(";") if "=" in kv)
            body = raw.split(" :", 1)[1] if " :" in raw else ""
            ts = int(tags.get("tmi-sent-ts", time.time() * 1000)) / 1000
            user = tags.get("display-name") or raw.split("!", 1)[0].lstrip(":")
            self.sink(ts - self.started, user, body)
        s.close()


# ---------- main ----------

class Live:
    def __init__(self, a):
        self.a = a
        self.lock = threading.Lock()
        self.bins = collections.defaultdict(float)
        self.comments = []
        self.jobs = []        # (ready_time, n, start, end, peak)
        self.last_peak = -10 ** 9
        self.render_q = []
        self.status = None
        self.cand = None
        self.tested = False

    def on_msg(self, off, user, body):
        with self.lock:
            self.bins[int(off // BIN)] += score_message(body)
            self.comments.append({"content_offset_seconds": round(off, 2),
                                  "commenter": {"display_name": user},
                                  "message": {"body": body}})

    def detect(self, now_off):
        """How many times the 5-min median is the last complete 30 s window?
        On crossing the threshold it doesn't decide at once: it watches 30 s more and keeps the best window."""
        a = self.a
        w = a.window // BIN
        cur = int(now_off // BIN) - 1          # last complete bin
        with self.lock:
            first = min(self.bins) if self.bins else cur
            sums = [sum(self.bins.get(i - k, 0) for k in range(w))
                    for i in range(max(cur - 60, first + w - 1), cur + 1)]
        if len(sums) < 24:                     # no decision without ~2 min of data
            return None
        base = statistics.median(sums[:-1]) or 1.0
        win = sums[-1]
        ratio = win / base
        self.status = (ratio, win, base)
        peak = (cur - w + 1) * BIN
        cand = self.cand
        if ratio >= a.ratio and win >= a.min_score and peak - self.last_peak >= a.gap:
            if not cand or ratio > cand[1]:
                self.cand = (cand[0] if cand else now_off, ratio, win, peak)
        if a.test and not self.tested:          # test: cut one clip as soon as warm-up ends
            self.tested = True
            return peak, ratio, win
        cand = self.cand
        if cand and now_off - cand[0] >= a.window:
            self.cand = None
            return cand[3], cand[1], cand[2]
        return None

    def main(self):
        a = self.a
        login = a.streamer.lower().rstrip("/").split("/")[-1]
        log(f"checking {login}...")
        is_live, started, title, _ = stream_info(login)
        if not is_live or not started:
            sys.exit(f"{login} is not live right now (or no info could be fetched).")
        vod = live_vod(login, started)
        if not vod:
            sys.exit("No VOD found for the live stream. The streamer may have VODs disabled.")
        out = os.path.join(VODS, vod)
        os.makedirs(out, exist_ok=True)
        self.out, self.vod, self.login, self.title = out, vod, login, title
        old = load_json(os.path.join(out, "clips.json"), []) or []
        self.clips = old
        self.n = max([c["n"] for c in old], default=0)
        log(f"LIVE: {title!r} | up for {fmt(time.time() - started)} | VOD {vod}")
        log(f"Threshold: last {a.window} s chat score >= {a.ratio}x median. Ctrl+C to stop.")

        Chat(login, started, self.on_msg).start()
        threading.Thread(target=self.renderer, daemon=True).start()
        last_check = last_stat = time.time()
        while True:
            time.sleep(BIN)
            now_off = time.time() - started
            hit = self.detect(now_off)
            if hit and self.n < a.max_clips:
                peak, ratio, win = hit
                self.last_peak = peak
                self.n += 1
                start = max(0, peak - a.pre)
                end = peak + a.window + a.post
                ready = started + end + LAG
                self.jobs.append((ready, self.n, start, end, peak))
                log(f"🔥 MOMENT #{self.n}: {fmt(start)}-{fmt(end)}  chat {ratio:.1f}x  "
                    f"(download in ~{int(ready - time.time())} s)")
            if time.time() - last_stat > 60:
                last_stat = time.time()
                with self.lock:
                    nmsg = len(self.comments)
                if self.status:
                    r, w, b = self.status
                    log(f"   chat: {nmsg} msgs total | last 30 s score {w:.0f} (normal {b:.0f}) = {r:.1f}x"
                        f" | clips {self.n}")
                else:
                    log(f"   chat warming up... {nmsg} msgs (~2 min of data needed)")
            for job in [j for j in self.jobs if j[0] <= time.time()]:
                self.jobs.remove(job)
                if not self.download(*job[1:]):
                    if job[1] > 0 and len(job) == 5:
                        self.jobs.append((time.time() + 60, -job[1], *job[2:]))  # retry once
            if time.time() - last_check > 300:
                last_check = time.time()
                self.save_chat()
                if not stream_info(login)[0]:
                    log("Stream ended. Downloading pending clips...")
                    for job in sorted(self.jobs):
                        self.download(*job[1:])
                    self.save_chat()
                    while self.render_q:
                        time.sleep(5)
                    log(f"Done. Folder: {out}")
                    return

    def download(self, n, start, end, peak):
        retry = n < 0
        n = abs(n)
        name = f"clip_{n:02d}_{tag(start)}.mp4"
        path = os.path.join(self.out, name)
        log(f"⬇ #{n} downloading: {name}")
        r = subprocess.run([TD, "videodownload", "--id", self.vod, "-b", str(int(start)),
                            "-e", str(int(end)), "-q", self.a.quality, "--collision", "Overwrite",
                            "-o", path], capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0 or not os.path.exists(path):
            err = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["?"]
            log(f"✖ #{n} download failed{' (last try)' if retry else ', retrying in 1 min'}: {err[0]}")
            return False
        self.clips = [c for c in self.clips if c["n"] != n]
        self.clips.append({"n": n, "file": name, "start": start, "end": end, "peak": peak})
        self.clips.sort(key=lambda c: c["n"])
        save_json(os.path.join(self.out, "clips.json"), self.clips)
        self.save_chat()
        self.add_highlight(n, start, end, peak, name)
        log(f"✔ #{n} ready: {path}")
        if self.a.render:
            self.render_q.append(n)
        return True

    def save_chat(self):
        with self.lock:
            comments = list(self.comments)
        old = load_json(os.path.join(self.out, "chat.json"), {}) or {}
        keep = {(c["content_offset_seconds"], c["message"]["body"]) for c in comments}
        merged = [c for c in old.get("comments", [])
                  if (c.get("content_offset_seconds"), (c.get("message") or {}).get("body")) not in keep]
        merged = sorted(merged + comments, key=lambda c: c["content_offset_seconds"])
        save_json(os.path.join(self.out, "chat.json"), {
            "streamer": {"name": self.login, "login": self.login},
            "video": {"title": self.title, "id": self.vod,
                      "length": merged[-1]["content_offset_seconds"] if merged else 0},
            "comments": merged})

    def add_highlight(self, n, start, end, peak, name):
        with self.lock:
            msgs = [c["message"]["body"] for c in self.comments
                    if peak <= c["content_offset_seconds"] < peak + self.a.window][:15]
        p = os.path.join(self.out, "highlights.txt")
        head = "" if os.path.exists(p) else (f"{self.login} - {self.title} (LIVE)\n"
                                             f"VOD: https://www.twitch.tv/videos/{self.vod}\n\n")
        with open(p, "a", encoding="utf-8") as f:
            f.write(head + f"#{n}  {fmt(start)} - {fmt(end)}  -> {name}\n"
                    f"    link: https://www.twitch.tv/videos/{self.vod}?t={tag(start)}\n"
                    + "".join(f"      | {m}\n" for m in msgs) + "\n")

    def renderer(self):
        while True:
            if not self.render_q:
                time.sleep(3)
                continue
            n = self.render_q[0]
            log(f"🎬 #{n} turning into a Short (run.py)...")
            subprocess.run([sys.executable, os.path.join(TOOLS, "run.py"), self.vod,
                            "--only", str(n), "--no-review"], cwd=TOOLS)
            self.render_q.pop(0)


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("streamer", help="Twitch username (xqc)")
    ap.add_argument("--render", action="store_true", help="turn every clip into a Short right away")
    ap.add_argument("--ratio", type=float, default=2.2, help="spike threshold, multiple of the median (2.2)")
    ap.add_argument("--min-score", type=float, default=15, help="minimum chat score in 30 s (15)")
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--pre", type=int, default=35, help="seconds before the moment (35)")
    ap.add_argument("--post", type=int, default=25, help="seconds after the window (25)")
    ap.add_argument("--gap", type=int, default=150, help="min seconds between moments (150)")
    ap.add_argument("--max-clips", type=int, default=30)
    ap.add_argument("--quality", default="1080p60")
    ap.add_argument("--test", action="store_true", help="cut one clip right after warm-up to test downloading")
    a = ap.parse_args()
    try:
        Live(a).main()
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    main()
