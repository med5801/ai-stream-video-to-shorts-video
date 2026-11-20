"""
Streamer watcher: finds NEWLY finished streams of the streamers in streamers.txt,
grabs the VOD id itself and hands it to run.py (or only to highlights.py).

Usage:
    python watcher.py                 checks every 30 min, runs forever
    python watcher.py --once          one check, then exits (for Task Scheduler)
    python watcher.py --list          only lists the streamers' latest VODs, downloads nothing
    python watcher.py --every 15      check every 15 min
    python watcher.py --mode highlights   only find moments + download clips (no brain/render)

Gets the VOD list in one of two ways:
  1) the official Twitch API (Helix) - if you add "twitch_client_id" and "twitch_client_secret"
     to config.json. Most reliable: it says directly whether a stream is live.
  2) yt-dlp - without API keys. Tells a live stream's VOD apart by checking whether its
     duration stayed the same between two checks.

State file: tools/watcher_state.json  (processed VODs are kept here; to process a VOD
again, delete its entry from this file.)
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from shorts_common import SHORTS, TOOLS, VODS, config, load_json, save_json, utf8_console

STREAMERS = os.path.join(TOOLS, "streamers.txt")
STATE = os.path.join(TOOLS, "watcher_state.json")
LOG = os.path.join(TOOLS, "watcher_log.txt")

MIN_LEN = 20 * 60     # skip VODs shorter than this (s)
MAX_TRIES = 2         # how many times to retry a failing VOD


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def log(msg):
    line = f"[{now()}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_streamers():
    if not os.path.exists(STREAMERS):
        with open(STREAMERS, "w", encoding="utf-8") as f:
            f.write("# One Twitch username per line (twitch.tv/THIS). Lines starting with # are comments.\n"
                    "ohnepixel\n")
        log(f"streamers.txt created, put your streamers there: {STREAMERS}")
    out = []
    with open(STREAMERS, encoding="utf-8") as f:
        for line in f:
            s = line.split("#")[0].strip().lower()
            if s:
                s = s.rstrip("/").split("/")[-1]
                if s not in out:
                    out.append(s)
    return out


# ---------- 1) Twitch Helix API ----------

class Helix:
    def __init__(self, cid, secret, state):
        self.cid, self.secret, self.state = cid, secret, state

    def _token(self, force=False):
        tok = self.state.get("_helix_token") or {}
        if not force and tok.get("value") and tok.get("exp", 0) > time.time() + 300:
            return tok["value"]
        q = urllib.parse.urlencode({"client_id": self.cid, "client_secret": self.secret,
                                    "grant_type": "client_credentials"})
        req = urllib.request.Request("https://id.twitch.tv/oauth2/token?" + q, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        self.state["_helix_token"] = {"value": d["access_token"],
                                      "exp": time.time() + int(d.get("expires_in", 3600))}
        return d["access_token"]

    def get(self, path, **params):
        url = "https://api.twitch.tv/helix/" + path + "?" + urllib.parse.urlencode(params, doseq=True)
        for attempt in (0, 1):
            req = urllib.request.Request(url, headers={
                "Client-Id": self.cid, "Authorization": "Bearer " + self._token(force=attempt == 1)})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.load(r).get("data", [])
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    continue
                raise

    def vods(self, logins, n):
        users = {u["login"]: u for u in self.get("users", login=logins)}
        live = {s["user_login"].lower(): s for s in self.get("streams", user_login=logins)}
        out = {}
        for login in logins:
            u = users.get(login)
            if not u:
                out[login] = None
                continue
            vids = self.get("videos", user_id=u["id"], type="archive", sort="time", first=n)
            stream = live.get(login)
            items = []
            for i, v in enumerate(vids):
                is_live = False
                if stream and i == 0:
                    # The live stream's archive: the newest VOD, created after the stream started.
                    vs = _iso(v["created_at"]); ss = _iso(stream["started_at"])
                    is_live = vs is not None and ss is not None and vs >= ss - 600
                items.append({"id": v["id"], "title": v.get("title", ""),
                              "duration": _hms(v.get("duration", "")), "live": is_live})
            out[login] = items
        return out


def _iso(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _hms(s):
    """'3h12m5s' -> seconds"""
    total, num = 0, ""
    for ch in s or "":
        if ch.isdigit():
            num += ch
        else:
            total += int(num or 0) * {"h": 3600, "m": 60, "s": 1}.get(ch, 0)
            num = ""
    return total or None


# ---------- 2) yt-dlp ----------

def ytdlp_cmd():
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        return None


def ytdlp_vods(logins, n):
    cmd = ytdlp_cmd()
    if not cmd:
        raise RuntimeError("yt-dlp not found. Install: pip install yt-dlp  "
                           "(or add twitch_client_id/secret to config.json)")
    out = {}
    for login in logins:
        url = f"https://www.twitch.tv/{login}/videos?filter=archives&sort=time"
        r = subprocess.run(cmd + ["--flat-playlist", "-J", "--playlist-end", str(n), url],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            err = (r.stderr or "").strip().splitlines()
            log(f"  {login}: yt-dlp failed: {err[-1] if err else '?'}")
            out[login] = None
            continue
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            out[login] = None
            continue
        items = []
        for e in (d.get("entries") or [])[:n]:
            vid = str(e.get("id", "")).lstrip("v")
            if vid.isdigit():
                items.append({"id": vid, "title": e.get("title") or "",
                              "duration": int(e["duration"]) if e.get("duration") else None,
                              "live": None})  # None = unknown, decided by the duration check
        out[login] = items
    return out


# ---------- decide + run ----------

def fmt_len(s):
    return "?" if not s else f"{s // 3600}:{s % 3600 // 60:02d}"


def already_made(vod_id):
    """Was this VOD already processed by hand? (clips in vods/<id> or <id> in shorts)"""
    d = os.path.join(VODS, vod_id)
    if os.path.isdir(d) and any(f.startswith("clip_") and f.endswith(".mp4") for f in os.listdir(d)):
        return True
    for root, dirs, _ in os.walk(SHORTS):
        if any(f"_{vod_id}_" in x for x in dirs):
            return True
    return False


def decide(vod, rec):
    """Is the VOD ready to process? (ready, reason)"""
    if "status" not in rec and already_made(vod["id"]):
        rec["status"] = "done"
        return False, "already processed"
    if rec.get("status") in ("done", "skip"):
        return False, rec["status"]
    if rec.get("status") == "failed" and rec.get("tries", 0) >= MAX_TRIES:
        return False, "failed"
    if vod["live"]:
        return False, "still live"
    dur = vod["duration"]
    if dur is not None and dur < MIN_LEN:
        return False, "too short"
    if vod["live"] is None:
        # yt-dlp mode: if the duration equals the previous check, the stream has ended.
        if dur is None or rec.get("last_duration") != dur:
            return False, "waiting (checking whether the duration still grows)"
    return True, "ready"


def process(vod_id, mode):
    script = "run.py" if mode == "full" else "highlights.py"
    cmd = [sys.executable, os.path.join(TOOLS, script), vod_id]
    if mode == "full":
        cmd.append("--no-review")
    log(f"  > {script} {vod_id} starting...")
    r = subprocess.run(cmd, cwd=TOOLS)
    return r.returncode == 0


def check(args, c, state):
    logins = read_streamers()
    if not logins:
        log("streamers.txt is empty.")
        return
    cid, secret = c.get("twitch_client_id", ""), c.get("twitch_client_secret", "")
    try:
        if cid and secret:
            found = Helix(cid, secret, state).vods(logins, args.last)
            src = "Helix API"
        else:
            found = ytdlp_vods(logins, args.last)
            src = "yt-dlp"
    except Exception as e:
        log(f"could not get the VOD list: {e}")
        return
    log(f"Check ({src}): {len(logins)} streamer(s)")

    queue = []
    vods_state = state.setdefault("vods", {})
    for login in logins:
        items = found.get(login)
        if items is None:
            log(f"  {login}: not found / error")
            continue
        first_time = login not in state.setdefault("seen_streamers", [])
        for i, v in enumerate(items):
            rec = vods_state.setdefault(v["id"], {"streamer": login, "title": v["title"]})
            if first_time and i >= args.backlog and "status" not in rec:
                rec["status"] = "skip"  # on the first run, don't process old streams
            ok, why = decide(v, rec)
            rec["last_duration"] = v["duration"]
            mark = "→ PROCESSING" if ok else f"({why})"
            log(f"  {login:<18} {v['id']}  {fmt_len(v['duration']):>8}  {v['title'][:45]!r} {mark}")
            if ok:
                queue.append((login, v))
        if first_time:
            state["seen_streamers"].append(login)
    save_json(STATE, state)

    if args.list:
        return
    for login, v in queue:
        rec = vods_state[v["id"]]
        ok = process(v["id"], args.mode)
        rec["status"] = "done" if ok else "failed"
        rec["tries"] = rec.get("tries", 0) + (0 if ok else 1)
        rec["at"] = now()
        save_json(STATE, state)
        log(f"  {'✔' if ok else '✖'} {login} {v['id']} {'done' if ok else 'failed'}")


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--list", action="store_true", help="list only, process nothing")
    ap.add_argument("--every", type=int, default=30, help="check every N minutes (30)")
    ap.add_argument("--last", type=int, default=3, help="how many latest VODs per streamer (3)")
    ap.add_argument("--backlog", type=int, default=1,
                    help="how many old VODs of a newly added streamer to process (1)")
    ap.add_argument("--mode", choices=["full", "highlights"], default=None,
                    help="full = run.py (makes Shorts), highlights = only downloads clips")
    args = ap.parse_args()
    c = config()
    args.mode = args.mode or c.get("watch_mode", "full")

    state = load_json(STATE, {}) or {}
    if args.list or args.once:
        check(args, c, state)
        return
    log(f"Watcher started: every {args.every} min, mode={args.mode}. Ctrl+C to stop.")
    while True:
        try:
            check(args, c, state)
        except Exception as e:
            log(f"Unexpected error: {e}")
        log(f"Next check in {args.every} min.")
        try:
            time.sleep(args.every * 60)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
