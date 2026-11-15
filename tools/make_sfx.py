"""Generates a royalty-free, fully synthetic sound-effect library (numpy).

Usage: python make_sfx.py <output_folder>
"""
import json
import os
import sys
import wave

import numpy as np

SR = 48000
rng = np.random.default_rng(7)
out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)


def t_(d):
    return np.arange(int(SR * d)) / SR


def env(n, a, r, curve=3.0):
    x = np.ones(n)
    na, nr = int(SR * a), int(SR * r)
    if na:
        x[:na] = np.linspace(0, 1, na) ** 2
    if nr:
        x[-nr:] *= np.linspace(1, 0, nr) ** curve
    return x


def svf(x, f, q=0.7, mode="bp"):
    """Time-varying state-variable filter. f: Hz array or constant."""
    f = np.broadcast_to(np.asarray(f, float), x.shape)
    low = band = 0.0
    y = np.empty_like(x)
    k = 1.0 / q
    for i in range(len(x)):
        g = 2 * np.sin(np.pi * min(f[i], SR / 6) / SR)
        high = x[i] - low - k * band
        band += g * high
        low += g * band
        y[i] = band if mode == "bp" else (low if mode == "lp" else high)
    return y


def sine_sweep(f0, f1, d, curve="exp"):
    tt = t_(d)
    if curve == "exp":
        f = f0 * (f1 / f0) ** (tt / d)
    else:
        f = f0 + (f1 - f0) * tt / d
    return np.sin(2 * np.pi * np.cumsum(f) / SR), f


def norm(x, peak=0.9):
    m = np.max(np.abs(x)) or 1
    return x / m * peak


def save(name, x):
    x = np.asarray(x, float)
    act = x[np.abs(x) > 0.02 * np.max(np.abs(x))]  # ignore the silent tail
    rms = np.sqrt(np.mean(act ** 2)) or 1
    x = x / rms * 10 ** (-16 / 20)
    pk = np.max(np.abs(x))
    if pk > 0.95:
        x = x * 0.95 / pk
    fade = min(len(x), int(SR * 0.005))
    x[-fade:] *= np.linspace(1, 0, fade)
    st = np.stack([x, x], 1)
    with wave.open(os.path.join(out_dir, name + ".wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((st * 32767).astype(np.int16).tobytes())


desc = {}

# whoosh: band-pass noise with a sweeping centre frequency
d = 0.55
n = rng.standard_normal(int(SR * d))
tt = t_(d)
f = 300 + 3500 * np.sin(np.pi * tt / d) ** 2
save("whoosh", svf(n, f, 1.2) * np.sin(np.pi * tt / d) ** 1.5)
desc["whoosh"] = "fast air whoosh, for zooms, cuts and transitions"

# swoosh_up: short rising transition
d = 0.7
tt = t_(d)
n = rng.standard_normal(len(tt))
save("swoosh_up", svf(n, 400 * (12 ** (tt / d)), 2.0) * env(len(tt), 0.55, 0.12))
desc["swoosh_up"] = "rising swoosh, for text/sticker or image appearing"

# pop
d = 0.12
s, _ = sine_sweep(900, 180, d)
save("pop", s * env(len(s), 0.002, 0.09, 2) + 0.3 * rng.standard_normal(len(s)) * env(len(s), 0.0, 0.11, 8))
desc["pop"] = "short bubbly pop, for stickers, emphasis words, small accents"

# ding
d = 1.4
tt = t_(d)
x = sum(a * np.sin(2 * np.pi * fr * tt) * np.exp(-tt * dk) for fr, a, dk in
        [(1318, 1, 3.0), (2636, 0.45, 5), (3954, 0.25, 7), (1975, 0.2, 4)])
save("ding", x * env(len(tt), 0.002, 0.2))
desc["ding"] = "bright bell ding, for a correct guess, realization or 'ding ding' moment"

# boom (vine-boom style)
d = 1.3
s, _ = sine_sweep(140, 42, d)
tt = t_(d)
x = np.tanh(2.5 * s * np.exp(-tt * 2.8)) + 0.5 * svf(rng.standard_normal(len(tt)), 180, 0.8, "lp") * np.exp(-tt * 18)
save("boom", x)
desc["boom"] = "deep dramatic meme boom (vine-boom style), for shocking or awkward punchlines"

# bass_drop
d = 1.6
s, _ = sine_sweep(160, 32, d)
tt = t_(d)
save("bass_drop", np.tanh(1.8 * s) * env(len(tt), 0.01, 0.6, 1.5))
desc["bass_drop"] = "long bass drop, for a big reveal or hype moment"

# riser
d = 2.0
tt = t_(d)
saw = 2 * ((np.cumsum(180 * (6 ** (tt / d))) / SR) % 1) - 1
n = svf(rng.standard_normal(len(tt)), 600 * (10 ** (tt / d)), 1.5)
save("riser", (0.5 * saw + n) * (tt / d) ** 2)
desc["riser"] = "2 s tension riser that builds up, put it right before the payoff"

# click
d = 0.03
tt = t_(d)
save("click", rng.standard_normal(len(tt)) * np.exp(-tt * 300))
desc["click"] = "tiny UI click, for subtle accents"

# camera_shutter
d = 0.25
tt = t_(d)
x = rng.standard_normal(len(tt)) * (np.exp(-tt * 120) + 0.7 * np.exp(-np.maximum(0, tt - 0.09) * 140) * (tt > 0.09))
save("camera_shutter", svf(x, 3000, 0.9))
desc["camera_shutter"] = "camera shutter snap, for freeze frames or 'caught in 4K' moments"

# glitch
d = 0.45
tt = t_(d)
x = np.zeros(len(tt))
pos = 0
while pos < len(tt):
    ln = int(SR * rng.uniform(0.01, 0.05))
    fr = rng.choice([220, 440, 880, 1760, 3520])
    seg = np.sign(np.sin(2 * np.pi * fr * tt[: min(ln, len(tt) - pos)]))
    x[pos:pos + len(seg)] = seg * rng.uniform(0.2, 1) * (rng.random() > 0.25)
    pos += ln
save("glitch", x * 0.6)
desc["glitch"] = "digital glitch stutter, for 'brain broken' / error / confusion moments"

# buzzer (wrong answer)
d = 0.65
tt = t_(d)
x = np.sign(np.sin(2 * np.pi * 140 * tt)) + np.sign(np.sin(2 * np.pi * 147 * tt))
save("buzzer", svf(x, 1200, 0.7, "lp") * env(len(tt), 0.005, 0.08))
desc["buzzer"] = "wrong-answer buzzer, for fails, bad takes, 'nope' moments"

# record_scratch
d = 0.55
tt = t_(d)
rate = 1 + 0.9 * np.sin(2 * np.pi * 7 * tt) * np.exp(-tt * 3)
n = rng.standard_normal(len(tt))
save("record_scratch", svf(n, 900 * rate + 200, 3.0) * env(len(tt), 0.01, 0.15))
desc["record_scratch"] = "record scratch stop, for 'wait, what?' / sudden stop moments"

# heartbeat
d = 1.1
tt = t_(d)
x = np.zeros(len(tt))
for st in (0.0, 0.28):
    m = tt >= st
    u = tt[m] - st
    x[m] += np.sin(2 * np.pi * 55 * u) * np.exp(-u * 18)
save("heartbeat", np.tanh(2 * x))
desc["heartbeat"] = "two heartbeat thumps, for tense / suspense moments"

# dramatic_hit
d = 1.2
tt = t_(d)
x = sum(2 * ((fr * tt) % 1) - 1 for fr in (110, 138.6, 164.8, 220))
x = svf(x, 2500 * np.exp(-tt * 3) + 200, 0.8, "lp") * np.exp(-tt * 3.5)
save("dramatic_hit", x + 0.4 * np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 4))
desc["dramatic_hit"] = "orchestral-style dramatic hit, for epic or over-dramatic moments"

# "sad trombone" style descending notes
notes = [(392, 0.35), (370, 0.35), (349, 0.35), (330, 0.9)]
parts = []
for fr, dd in notes:
    tt = t_(dd)
    vib = 1 + (0.012 * np.sin(2 * np.pi * 5.5 * tt) if dd > 0.5 else 0)
    ph = np.cumsum(fr * vib) / SR
    w = sum((1 / k) * np.sin(2 * np.pi * k * ph) for k in range(1, 7))
    parts.append(w * env(len(tt), 0.02, 0.08 if dd < 0.5 else 0.4, 1.5))
save("sad_trombone", svf(np.concatenate(parts), 1800, 0.7, "lp"))
desc["sad_trombone"] = "sad descending 'wah wah wah' notes, for fails"

with open(os.path.join(out_dir, "sfx.json"), "w", encoding="utf-8") as f:
    json.dump(desc, f, indent=2)
print(len(desc), "effects")
