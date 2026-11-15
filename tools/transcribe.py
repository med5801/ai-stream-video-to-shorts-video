"""Word-level transcription of a clip (faster-whisper)."""
import subprocess
import sys

import numpy as np

from shorts_common import FFMPEG, config, save_json, load_json, utf8_console

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from faster_whisper import WhisperModel
    c = config()
    name, dev = c["whisper_model"], c["whisper_device"]
    tries = [("cuda", "float16"), ("cpu", "int8")] if dev == "auto" else [
        (dev, "float16" if dev == "cuda" else "int8")]
    last = None
    for d, ct in tries:
        try:
            _MODEL = WhisperModel(name, device=d, compute_type=ct)
            print(f"  whisper: {name} / {d}")
            return _MODEL
        except Exception as e:  # no GPU -> fall back to CPU
            last = e
    raise RuntimeError(f"Could not load Whisper: {last}")


def load_audio(path):
    # Decode to 16 kHz mono PCM with ffmpeg instead of handing the path to Whisper
    # (avoids problems with non-ASCII folder names on Windows).
    r = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("Could not read audio: " + r.stderr.decode(errors="replace")[:300])
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def transcribe(clip_path, out_json, force=False):
    cached = load_json(out_json)
    if cached is not None and not force:
        return cached
    segs, info = _model().transcribe(load_audio(clip_path), word_timestamps=True,
                                     vad_filter=True, beam_size=5)
    words = []
    for s in segs:
        for w in s.words or []:
            t = w.word.strip()
            if t:
                words.append({"w": t, "s": round(w.start, 2), "e": round(w.end, 2)})
    data = {"language": info.language, "words": words}
    save_json(out_json, data)
    return data


if __name__ == "__main__":
    utf8_console()
    d = transcribe(sys.argv[1], sys.argv[2], force=True)
    print(d["language"], len(d["words"]), "words")
    print(" ".join(w["w"] for w in d["words"])[:500])
