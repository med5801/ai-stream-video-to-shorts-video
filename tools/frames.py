"""Frames for the brain: timestamped contact sheets + one full frame with a grid."""
import os
import subprocess
import sys

import cv2
import numpy as np

from shorts_common import FFMPEG, probe

TW, TH = 480, 270  # tile size on a contact sheet
COLS, ROWS = 4, 4


def _write(path, img):
    # cv2.imwrite fails on non-ASCII paths on Windows -> imencode + tofile
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    buf.tofile(path)


def _grab(path, fps, w, h):
    r = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-vf",
                        f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    n = a.size // (w * h * 3)
    return a[: n * w * h * 3].reshape(n, h, w, 3)


def _label(img, text, org, scale=0.8):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 255, 255), 2, cv2.LINE_AA)


def make_frames(clip_path, out_dir, step=3.0):
    os.makedirs(out_dir, exist_ok=True)
    dur = probe(clip_path)["duration"]
    frames = _grab(clip_path, 1.0 / step, TW, TH)
    per = COLS * ROWS
    sheets = []
    for si in range(0, len(frames), per):
        sheet = np.zeros((ROWS * TH, COLS * TW, 3), np.uint8)
        for k, fr in enumerate(frames[si:si + per]):
            img = fr.copy()
            _label(img, f"{(si + k) * step:.0f}s", (8, 30))
            r, c = divmod(k, COLS)
            sheet[r * TH:(r + 1) * TH, c * TW:(c + 1) * TW] = img
        name = f"sheet_{len(sheets) + 1}.jpg"
        _write(os.path.join(out_dir, name), sheet)
        sheets.append(name)

    # One full frame from the middle with a 10% grid, for measuring the layout
    mid = dur / 2
    r = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{mid:.2f}", "-i", clip_path,
                        "-frames:v", "1", "-vf", "scale=1280:720", "-f", "rawvideo",
                        "-pix_fmt", "bgr24", "-"], capture_output=True)
    img = np.frombuffer(r.stdout, np.uint8)[:1280 * 720 * 3].reshape(720, 1280, 3).copy()
    for p in range(1, 10):
        x, y = int(1280 * p / 10), int(720 * p / 10)
        cv2.line(img, (x, 0), (x, 719), (0, 255, 255), 1)
        cv2.line(img, (0, y), (1279, y), (0, 255, 255), 1)
        _label(img, f"{p * 10}", (x + 3, 22), 0.6)
        _label(img, f"{p * 10}", (3, y - 4), 0.6)
    _write(os.path.join(out_dir, "layout.jpg"), img)
    return {"sheets": sheets, "layout": "layout.jpg", "layout_t": round(mid, 1),
            "step": step, "duration": dur}


if __name__ == "__main__":
    print(make_frames(sys.argv[1], sys.argv[2]))
