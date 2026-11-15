"""Shared helpers: paths, settings, ffmpeg calls."""
import json
import os
import shutil
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
TD_DIR = os.path.join(TOOLS, "TwitchDownloader")
VODS = os.path.join(ROOT, "vods")
SHORTS = os.path.join(ROOT, "shorts")
ASSETS = os.path.join(ROOT, "assets")   # your images (png/jpg/webp)
SFX = os.path.join(ROOT, "sfx")         # sound effects (wav/mp3/ogg)

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
SND_EXT = (".wav", ".mp3", ".ogg", ".m4a")


def list_files(folder, exts):
    """Re-reads the folder on EVERY call (files added mid-run show up too)."""
    try:
        return sorted(f for f in os.listdir(folder) if f.lower().endswith(exts))
    except FileNotFoundError:
        return []


def sfx_catalog():
    """{name: description}. Descriptions come from sfx/sfx.json, else from the file name."""
    desc = load_json(os.path.join(SFX, "sfx.json"), {}) or {}
    out = {}
    for f in list_files(SFX, SND_EXT):
        name = os.path.splitext(f)[0]
        out[name] = desc.get(name, name.replace("_", " "))
    return out


def sfx_path(name):
    for f in list_files(SFX, SND_EXT):
        if os.path.splitext(f)[0].lower() == str(name).lower():
            return os.path.join(SFX, f)
    return None


def _tool(name):
    local = os.path.join(TD_DIR, name + ".exe")
    if os.path.exists(local):
        return local
    return shutil.which(name) or name


FFMPEG = _tool("ffmpeg")
FFPROBE = _tool("ffprobe")

DEFAULTS = {
    "brain": "auto",
    "order": ["claude", "gemini"],
    "claude_cmd": "claude",
    "gemini_cmd": "gemini",
    "claude_model": "",
    "gemini_model": "",
    "brain_timeout_sec": 400,
    "whisper_model": "medium",
    "whisper_device": "auto",
    "review_plan": True,
    "review_render": True,
    "min_score": 6,
    "reason_language": "English",
    "caption_font": "Montserrat ExtraBold",
    "caption_size": 92,
    "caption_color": "#FFFFFF",
    "highlight_color": "#FFE600",
    "hook_seconds": 2.5,
    "auto_chat": True,          # if the brain shows no chat, show it at the biggest laugh
    "auto_whoosh_on_zoom": True,  # if the brain adds no sfx, add a whoosh on zooms
    "sfx_volume": 0.8,
    "chat_lines": 5,
    "fps": 30,
    "crf": 20,
}


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def config():
    c = dict(DEFAULTS)
    c.update(load_json(os.path.join(TOOLS, "config.json"), {}) or {})
    return c


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def probe(path):
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8")
    d = json.loads(r.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    return {"width": int(st.get("width", 1920)), "height": int(st.get("height", 1080)),
            "duration": float((d.get("format") or {}).get("duration", 0) or 0)}


def ffmpeg(args, cwd=None):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + args
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + " ".join(args[:6]) + " ...")


def fmt(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
