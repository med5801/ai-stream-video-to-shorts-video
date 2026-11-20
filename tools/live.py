"""
LIVE MODE: listens to chat while the stream is running and, when chat explodes, cuts that
moment from the live VOD.

Usage:
    python live.py xqc                    only cuts clips (vods/<VOD_ID>/clip_XX.mp4)
    python live.py xqc --render           also turns every clip into a Short right away (run.py --no-review)
    python live.py xqc --ratio 2.5        pickier (default 2.2)
    python live.py xqc --backfill 5       also cut the best 5 moments since the stream started
    python live.py xqc --backfill 8 --backfill-from 4h   the best 8 moments since hour 4
    python live.py xqc --render --resume  continue from the last clip of a previous run

How it works:
  1) yt-dlp: is the stream live, when did it start, what is the live VOD id.
  2) Connects to Twitch chat anonymously (no account, read only).
  3) Every 5 s compares the chat score of the last 30 s with the median of the last 5 min.
     A clear spike marks the moment.
  4) Waits for Twitch to write that part to the VOD (~1.5 min), then downloads only that part.
  5) Writes chat.json / clips.json / highlights.txt in the same format as highlights.py,
     so you can also turn them into Shorts later with run.bat <VOD_ID>.
Stop with Ctrl+C (queued clips are dropped, downloaded ones stay).
"""
import argparse
import collections
import glob
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time

from highlights import HERE, TD, find_highlights, fmt, score_message, tag
from shorts_common import TOOLS, VODS, load_json, save_json, utf8_console
from watcher import ytdlp_cmd

BIN = 5
LAG = 90          # margin for Twitch writing the live VOD (s)
LAT = 10          # recording mode: stream latency (streamer -> us), s
SEG = 10          # recording mode: segment length, s
KEEP = 20 * 60    # recording mode: seconds of history to keep
FF = os.path.join(HERE, "TwitchDownloader", "ffmpeg.exe")
if not os.path.exists(FF):
    FF = "ffmpeg"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------- stream info ----------

def stream_info(login):
    """(is_live, started_unix, title, category)"""
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
    """Archive VOD id of the live stream (None if there is none)."""
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
    # If the newest VOD's duration is close to the stream's uptime, it is the live stream's VOD.
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
        log(f"connected to chat: #{self.login}")
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


# ---------- recording mode (no VOD) ----------

class Recorder(threading.Thread):
    """For streamers with VODs disabled: records the stream to disk in 10 s segments
    (the last 20 min are kept). The file name carries the segment's start: seg_YYYYmmdd_HHMMSS.ts"""
    def __init__(self, login, segdir):
        super().__init__(daemon=True)
        self.login, self.segdir = login, segdir
        os.makedirs(segdir, exist_ok=True)

    def run(self):
        while True:
            y = subprocess.Popen(ytdlp_cmd() + ["-q", "--no-part", "-f", "best", "-o", "-",
                                                f"https://www.twitch.tv/{self.login}"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            f = subprocess.Popen([FF, "-loglevel", "error", "-i", "pipe:0", "-c", "copy", "-f", "segment",
                                  "-segment_time", str(SEG), "-reset_timestamps", "1", "-strftime", "1",
                                  os.path.join(self.segdir, "seg_%Y%m%d_%H%M%S.ts")],
                                 stdin=y.stdout, stderr=subprocess.DEVNULL)
            y.stdout.close()
            log("⏺ recording started (no VOD, clips will be cut from this recording)")
            f.wait()
            y.kill()
            log("⏺ recording dropped, retrying in 5 s...")
            time.sleep(5)


def seg_time(path):
    try:
        return time.mktime(time.strptime(os.path.basename(path)[4:19], "%Y%m%d_%H%M%S"))
    except ValueError:
        return None


# ---------- main ----------

class Live:
    def __init__(self, a):
        self.a = a
        self.lock = threading.Lock()
        self.jlock = threading.Lock()
        self.bins = collections.defaultdict(float)    # total chat score / 5 s
        self.hbins = collections.defaultdict(float)   # only the "hype" part (KEKW, OMEGALUL, CAPS...)
        self.comments = []
        self.jobs = []        # (ready_time, n, start, end, peak)
        self.last_peak = -10 ** 9
        self.last_bin = None  # last evaluated bin (so no window is missed)
        self.render_q = []
        self.status = None
        self.cand = None
        self.tested = False
        self.flush = False    # stream ended: download every queued job now
        self.record = False   # no VOD -> we record the stream ourselves
        self.busy = False

    def on_msg(self, off, user, body):
        sc = score_message(body)
        with self.lock:
            self.bins[int(off // BIN)] += sc
            self.hbins[int(off // BIN)] += sc - 1.0
            self.comments.append({"content_offset_seconds": round(off, 2),
                                  "commenter": {"display_name": user},
                                  "message": {"body": body}})

    def _window_sums(self, bins, cur, w, first):
        return [sum(bins.get(i - k, 0) for k in range(w))
                for i in range(max(cur - 60, first + w - 1), cur + 1)]

    def detect_bin(self, cur):
        """How many times the 5-min median is the 30 s window ending at bin 'cur'?
        Two signals: total chat (message count) and hype words only. In huge chats the message
        count is capped, but the KEKW/OMEGALUL share explodes; the second signal catches that.
        It doesn't decide the moment the threshold is crossed: it watches 30 s more and takes the peak."""
        a = self.a
        w = a.window // BIN
        with self.lock:
            first = min(self.bins) if self.bins else cur
            sums = self._window_sums(self.bins, cur, w, first)
            hsums = self._window_sums(self.hbins, cur, w, first)
        if len(sums) < 24:                     # no decision without ~2 min of data
            return None
        base = statistics.median(sums[:-1]) or 1.0
        hbase = max(statistics.median(hsums[:-1]), 3.0)
        win, hwin = sums[-1], hsums[-1]
        ratio, hratio = win / base, hwin / hbase
        self.status = (ratio, win, base, hratio)
        peak = (cur - w + 1) * BIN
        now_off = (cur + 1) * BIN
        strength = max(ratio / a.ratio, hratio / a.hype_ratio)
        cand = self.cand
        if strength >= 1 and win >= a.min_score and peak - self.last_peak >= a.gap:
            if not cand or strength > cand[1]:
                self.cand = (cand[0] if cand else now_off, strength, ratio, hratio, peak)
        if a.test and not self.tested:          # test: cut one clip as soon as warm-up ends
            self.tested = True
            return peak, ratio, hratio
        cand = self.cand
        if cand and now_off - cand[0] >= a.window:
            self.cand = None
            return cand[4], cand[2], cand[3]
        return None

    def queue(self, ready, n, start, end, peak):
        with self.jlock:
            self.jobs.append((ready, n, start, end, peak))
        if getattr(self, "out", None):
            self.save_jobs()

    def overlaps(self, peak):
        """Does this moment overlap a clip that was already cut?"""
        return any(abs(peak - c.get("peak", c.get("start", 0) + self.a.pre)) < self.a.gap
                   for c in self.clips) or any(abs(peak - j[4]) < self.a.gap for j in self.jobs)

    def main(self):
        a = self.a
        login = a.streamer.lower().rstrip("/").split("/")[-1]
        log(f"checking {login}...")
        is_live, started, title, _ = stream_info(login)
        if not is_live or not started:
            sys.exit(f"{login} is not live right now (or the info could not be fetched).")
        vod = live_vod(login, started)
        self.record = not vod
        if self.record:
            # No VOD: we record the stream and cut from that. run.py expects a number, so the
            # folder gets a date-based number starting with 9.
            vod = "9" + time.strftime("%Y%m%d%H%M")
            log("No VOD found (the streamer disabled VODs) -> RECORDING MODE: the stream is recorded and cut from there.")
        out = os.path.join(VODS, vod)
        os.makedirs(out, exist_ok=True)
        self.out, self.vod, self.login, self.title = out, vod, login, title
        self.started = started
        old = load_json(os.path.join(out, "clips.json"), []) or []
        self.clips = old
        self.n = max([c["n"] for c in old], default=0)
        self.n0 = self.n          # --max-clips counts the clips cut in THIS run
        if a.resume:
            last = max([c.get("end", c.get("start", 0)) for c in old], default=0)
            a.backfill_from = max(a.backfill_from, int(last))
            a.backfill = a.backfill or 10
            log(f"↻ RESUMING: {len(old)} earlier clip(s), the last one ends at {fmt(last)};"
                f" the history scan starts there.")
        have = {c["n"] for c in old}
        left = [j for j in (load_json(os.path.join(out, "pending.json"), []) or [])
                if abs(j[1]) not in have]
        for j in left:
            self.jobs.append((time.time(), abs(j[1]), j[2], j[3], j[4]))
            self.n = max(self.n, abs(j[1]))
        if left:
            log(f"↺ restored {len(left)} queued clip(s) from the previous run")
        log(f"LIVE: {title!r} | live for {fmt(time.time() - started)} | VOD {vod}")
        log(f"Threshold: chat {a.ratio}x or hype {a.hype_ratio}x (last {a.window} s / 5-min median)."
            f" Ctrl+C to stop.")

        Chat(login, started, self.on_msg).start()
        if self.record:
            self.segdir = os.path.join(out, "_recording")
            Recorder(login, self.segdir).start()
        if a.backfill and self.record:
            log("⏪ History can't be scanned in recording mode (no VOD); only moments from now on will be cut.")
        elif a.backfill:
            threading.Thread(target=self.backfill, daemon=True,
                             args=(a.backfill_from, time.time() - started - 60)).start()
        threading.Thread(target=self.downloader, daemon=True).start()
        threading.Thread(target=self.renderer, daemon=True).start()
        last_check = last_stat = time.time()
        while True:
            time.sleep(BIN)
            cur = int((time.time() - started) // BIN) - 1
            if self.last_bin is None:
                self.last_bin = cur - 1
            # Evaluate every window in between (in case the loop was delayed).
            for b in range(max(self.last_bin + 1, cur - 60), cur + 1):
                hit = self.detect_bin(b)
                if hit and self.n - self.n0 < a.max_clips:
                    peak, ratio, hratio = hit
                    self.last_peak = peak
                    with self.lock:
                        self.n += 1
                        n = self.n
                    start = max(0, peak - a.pre)
                    end = peak + a.window + a.post
                    ready = started + end + (LAT + 2 * SEG if self.record else LAG)
                    self.queue(ready, n, start, end, peak)
                    log(f"🔥 MOMENT #{n}: {fmt(start)}-{fmt(end)}  chat {ratio:.1f}x  hype {hratio:.1f}x  "
                        f"(downloading in ~{max(0, int(ready - time.time()))} s)")
            self.last_bin = cur
            if time.time() - last_stat > 60:
                last_stat = time.time()
                with self.lock:
                    nmsg = len(self.comments)
                if self.status:
                    r, w, b, hr = self.status
                    log(f"   chat: {nmsg} messages | last 30 s score {w:.0f} (normal {b:.0f}) = {r:.1f}x"
                        f" | hype {hr:.1f}x | clips {self.n} | queue {len(self.jobs)} | render {len(self.render_q)}")
                else:
                    log(f"   chat warming up... {nmsg} messages (~2 min of data needed before deciding)")
            if time.time() - last_check > 300:
                last_check = time.time()
                if not stream_info(login)[0]:
                    log("Stream ended. Downloading the queued clips...")
                    self.flush = True
                    while self.jobs or self.busy:
                        time.sleep(3)
                    self.save_chat()
                    while self.render_q:
                        time.sleep(5)
                    log(f"Done. Folder: {out}")
                    return

    def save_jobs(self):
        try:
            with self.jlock:
                jobs = [list(j) for j in self.jobs]
            save_json(os.path.join(self.out, "pending.json"), jobs)
        except Exception:
            pass

    def downloader(self):
        """No error may stop this thread; if it stopped, the queue would pile up and clips would be lost."""
        while True:
            try:
                self._downloader()
            except Exception as e:
                self.busy = False
                log(f"✖ downloader error ({e!r}), continuing in 5 s")
                time.sleep(5)

    def _downloader(self):
        """Downloads and chat saving run in their own thread: the main loop (chat tracking) never blocks."""
        last_save = time.time()
        while True:
            with self.jlock:
                due = sorted(j for j in self.jobs if self.flush or j[0] <= time.time())
                job = due[0] if due else None
                if job:
                    self.jobs.remove(job)
                    self.busy = True
            if not job:
                self.busy = False
                if time.time() - last_save > 300:
                    last_save = time.time()
                    self.save_chat()
                    if self.record:
                        for p in glob.glob(os.path.join(self.segdir, "seg_*.ts")):
                            t = seg_time(p)
                            if t and t < time.time() - KEEP:
                                try:
                                    os.remove(p)
                                except OSError:
                                    pass
                time.sleep(2)
                continue
            try:
                ok = self.download(*job[1:])
            except Exception as e:
                log(f"✖ #{abs(job[1])} download error: {e!r}")
                ok = False
            if not ok and job[1] > 0:
                self.queue(time.time() + 60, -job[1], *job[2:])   # retry once
            self.busy = False
            self.save_jobs()

    def download(self, n, start, end, peak):
        retry = n < 0
        n = abs(n)
        name = f"clip_{n:02d}_{tag(start)}.mp4"
        path = os.path.join(self.out, name)
        log(f"⬇ #{n} downloading: {name}")
        if self.record:
            if not self.cut(start, end, path):
                log(f"✖ #{n} could not be cut from the recording{' (last try)' if retry else ', retrying in 1 min'}")
                return False
            return self.finish(n, start, end, peak, name, path)
        r = subprocess.run([TD, "videodownload", "--id", self.vod, "-b", str(int(start)),
                            "-e", str(int(end)), "-q", self.a.quality, "--collision", "Overwrite",
                            "-o", path], capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0 or not os.path.exists(path):
            err = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["?"]
            log(f"✖ #{n} download failed{' (last try)' if retry else ', retrying in 1 min'}: {err[0]}")
            return False
        return self.finish(n, start, end, peak, name, path)

    def cut(self, start, end, path):
        """Cuts [start, end] (stream seconds) from the recorded segments."""
        w0 = self.started + start + LAT
        w1 = self.started + end + LAT
        segs = sorted((t, p) for p in glob.glob(os.path.join(self.segdir, "seg_*.ts"))
                      if (t := seg_time(p)) is not None)
        sel = [(t, p) for t, p in segs if t <= w1 and t + SEG + 2 >= w0]
        if not sel or sel[-1][0] + SEG < w1:
            return False          # the recording hasn't reached this moment yet / that part is missing
        lst = path + ".txt"
        with open(lst, "w", encoding="utf-8") as f:
            for _, p in sel:
                f.write("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'\n")
        r = subprocess.run([FF, "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                            "-ss", f"{max(0, w0 - sel[0][0]):.2f}", "-t", f"{w1 - w0:.2f}",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac",
                            "-b:a", "192k", path], capture_output=True)
        try:
            os.remove(lst)
        except OSError:
            pass
        return r.returncode == 0 and os.path.exists(path)

    def finish(self, n, start, end, peak, name, path):
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
        path = os.path.join(self.out, "chat.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"streamer": {"name": self.login, "login": self.login},
                       "video": {"title": self.title, "id": self.vod, "no_vod": self.record,
                                 "length": merged[-1]["content_offset_seconds"] if merged else 0},
                       "comments": merged}, f, ensure_ascii=False, separators=(",", ":"))
        for _ in range(20):      # Windows locks the file if run.py is reading it: wait a bit and retry
            try:
                os.replace(tmp, path)    # run.py never reads a half-written file
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            log("   (chat.json is locked right now, it will be written on the next save)")

    def add_highlight(self, n, start, end, peak, name):
        with self.lock:
            msgs = [c["message"]["body"] for c in self.comments
                    if peak <= c["content_offset_seconds"] < peak + self.a.window][:15]
        p = os.path.join(self.out, "highlights.txt")
        head = "" if os.path.exists(p) else (f"{self.login} - {self.title} (LIVE)\n"
                                             f"VOD: https://www.twitch.tv/videos/{self.vod}\n\n")
        with open(p, "a", encoding="utf-8") as f:
            f.write(head + f"#{n}  {fmt(start)} - {fmt(end)}  -> {name}\n"
                    + ("" if self.record else f"    link: https://www.twitch.tv/videos/{self.vod}?t={tag(start)}\n")
                    + "".join(f"      | {m}\n" for m in msgs) + "\n")

    def backfill(self, frm, upto):
        """Downloads the chat between stream seconds 'frm' and 'upto' and queues the best moments."""
        a = self.a
        path = os.path.join(self.out, "chat_past.json")
        log(f"⏪ Downloading past chat ({fmt(frm)} - {fmt(upto)}), may take a few minutes for big chats...")
        base = [TD, "chatdownload", "--id", self.vod, "-b", str(int(frm)), "-e", str(int(upto)),
                "-o", path, "--collision", "Overwrite"]
        r = subprocess.run(base + ["--threads", "12"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:      # older TwitchDownloader without --threads: try plain
            r = subprocess.run(base, capture_output=True, text=True, encoding="utf-8", errors="replace")
        data = load_json(path, None)
        if r.returncode != 0 or not data:
            log(f"✖ Past chat download failed: {(r.stderr or r.stdout or '?').strip().splitlines()[-1:]}")
            return
        past = [{"content_offset_seconds": c.get("content_offset_seconds", 0),
                 "commenter": {"display_name": (c.get("commenter") or {}).get("display_name", "")},
                 "message": {"body": (c.get("message") or {}).get("body", "")}}
                for c in data.get("comments", []) if frm <= c.get("content_offset_seconds", 0) < upto]
        with self.lock:
            have = {(c["content_offset_seconds"], c["message"]["body"]) for c in self.comments}
            self.comments = [c for c in past if (c["content_offset_seconds"], c["message"]["body"])
                             not in have] + self.comments
        # find_highlights counts from 0; shift the range to 0 and add it back
        shifted = [dict(c, content_offset_seconds=c["content_offset_seconds"] - frm) for c in past]
        # find_highlights picks from the highest score down: the top=k result is a subset of top=k+1.
        # So we can take moments in score order and skip the ones that were already cut.
        peaks, seen = [], set()
        for k in range(1, a.backfill * 4 + 1):
            got = set(find_highlights(shifted, upto - frm, a.window, k, a.gap)[0])
            new = got - seen
            if not new:
                break
            seen |= got
            p = new.pop() + frm
            if p + a.window + a.post < upto and not self.overlaps(p) \
                    and all(abs(p - q) >= a.gap for q in peaks):
                peaks.append(p)
            if len(peaks) >= a.backfill:
                break
        log(f"⏪ {len(peaks)} new moment(s) found in {len(past)} past messages: "
            + ", ".join(fmt(p) for p in sorted(peaks)))
        for peak in sorted(peaks):
            with self.lock:
                self.n += 1
                n = self.n
            start = max(0, peak - a.pre)
            end = peak + a.window + a.post
            self.queue(time.time(), n, start, end, peak)

    def renderer(self):
        while True:
            if not self.render_q:
                time.sleep(3)
                continue
            n = self.render_q[0]
            log(f"🎬 #{n} turning into a Short (run.py)...")
            try:
                subprocess.run([sys.executable, os.path.join(TOOLS, "run.py"), self.vod,
                                "--only", str(n), "--no-review"], cwd=TOOLS)
            except Exception as e:
                log(f"✖ #{n} could not start the render: {e!r}")
            self.render_q.pop(0)


def parse_dur(t):
    """'4h', '3h30m', '90m', '1200' -> seconds"""
    t = str(t).strip().lower()
    if t.isdigit():
        return int(t)
    total, num = 0, ""
    for ch in t:
        if ch.isdigit():
            num += ch
        elif ch in "hms":
            total += int(num or 0) * {"h": 3600, "m": 60, "s": 1}[ch]
            num = ""
    return total


def no_quickedit():
    """Clicking a Windows console window enters 'Select' (QuickEdit) mode, which freezes the
    program and chat gets missed. Turns that mode off for this window."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)
    except Exception:
        pass


def main():
    utf8_console()
    no_quickedit()
    ap = argparse.ArgumentParser()
    ap.add_argument("streamer", help="Twitch username (xqc)")
    ap.add_argument("--render", action="store_true", help="turn every clip into a Short right away")
    ap.add_argument("--ratio", type=float, default=2.2, help="spike threshold, multiple of the median (2.2)")
    ap.add_argument("--min-score", type=float, default=15, help="minimum chat score in 30 s (15)")
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--pre", type=int, default=35, help="seconds before the moment (35)")
    ap.add_argument("--post", type=int, default=25, help="seconds after the window (25)")
    ap.add_argument("--gap", type=int, default=150, help="minimum seconds between moments (150)")
    ap.add_argument("--max-clips", type=int, default=30, help="max clips in this run (30)")
    ap.add_argument("--resume", action="store_true",
                    help="continue where the last run stopped: scan history from the end of the last clip")
    ap.add_argument("--quality", default="1080p60")
    ap.add_argument("--backfill", type=int, default=0,
                    help="also cut the best N past moments (0 = off)")
    ap.add_argument("--backfill-from", default="0",
                    help="where the history scan starts: seconds or 4h, 3h30m (default 0)")
    ap.add_argument("--hype-ratio", type=float, default=2.5,
                    help="second threshold for hype words (KEKW, OMEGALUL...) (2.5)")
    ap.add_argument("--test", action="store_true", help="cut 1 clip at the first chance to test downloading")
    a = ap.parse_args()
    a.backfill_from = parse_dur(a.backfill_from)
    try:
        Live(a).main()
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    main()
