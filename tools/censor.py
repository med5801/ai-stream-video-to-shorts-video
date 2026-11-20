"""Profanity censoring (rule-based, no AI needed).

Finds swear words using Whisper's word timestamps:
  - in the captions only that word is masked:  FUCKING -> F******
  - in the audio the same span is muted + bleeped (applied by render.py)
Swear words in the hook / stickers / title / on-screen chat are masked too.

Word list: ROOTS/EXACT below + tools/profanity_list.txt (one word per line, '#' = comment).
Settings (config.json): "censor": true, "censor_mode": "bleep" | "mute",
                        "censor_bleep_volume": 0.22, "censor_pad": 0.08
"""
import os
import re

from shorts_common import TOOLS, read_text

# Roots -> any word containing them is caught (fucking, motherfucker, bullshit...)
ROOTS = ["fuck", "fuk", "fck", "motherf", "shit", "bitch", "cunt", "pussy", "nigg", "niga",
         "faggot", "fagot", "retard", "whore", "slut", "twat", "wank", "cock", "dick",
         "bastard", "asshole", "dumbass", "jackass", "dipshit", "goddamn", "jizz", "cum"]
# Whole-word matches only (their root appears in innocent words: "class", "cumulative", "dickens"...)
EXACT = {"ass", "asses", "fag", "fags", "cum", "cums", "dick", "dicks", "cock", "cocks",
         "tit", "tits", "titty", "titties", "hoe", "hoes", "nig", "damnit"}
# Look like a root but are innocent
SAFE = {"cockpit", "cocktail", "cockroach", "peacock", "hancock", "dickens", "shitake",
        "cumulative", "cucumber", "swank", "swanky", "document", "circumstance", "scunthorpe", "assassin",
        "assassins", "assassinate", "bassist", "class", "pass", "glass", "grass", "mass",
        "massive", "embassy", "cumin", "succumb", "accumulate"}

TOKEN = re.compile(r"[a-z0-9*@$!]+")


def _extra():
    raw = read_text(os.path.join(TOOLS, "profanity_list.txt"))
    return {l.strip().lower() for l in raw.splitlines() if l.strip() and not l.lstrip().startswith("#")}


_EXTRA = None


def is_bad(word):
    global _EXTRA
    if _EXTRA is None:
        _EXTRA = _extra()
    w = re.sub(r"[^a-z0-9*@$!]", "", str(word).lower())
    if not w:
        return False
    # Whisper sometimes stars them itself: f***, s**t, f*cking
    if "*" in w and re.fullmatch(r"[a-z]\**[a-z*]*", w) and w.count("*") >= 1 and len(w) >= 3:
        return True
    letters = re.sub(r"[^a-z]", "", w.replace("@", "a").replace("$", "s").replace("!", "i"))
    if not letters or letters in SAFE:
        return False
    if letters in EXACT or letters in _EXTRA:
        return True
    if any(letters.startswith(e) for e in _EXTRA if len(e) >= 4):
        return True
    for r in ROOTS:
        if r in ("cock", "dick", "cum"):       # these only as whole words (EXACT)
            continue
        if r in letters:
            return True
    return False


def mask_word(word):
    """'Fucking,' -> 'F******,'  (punctuation kept)"""
    m = re.match(r"^(\W*)(.*?)(\W*)$", str(word), re.S)
    pre, core, post = m.groups() if m else ("", str(word), "")
    if not core:
        return word
    return pre + core[0] + "*" * max(2, len(core) - 1) + post


def mask_text(text):
    """Masks swear words in free text (hook, title, chat)."""
    if not text:
        return text
    return re.sub(r"[A-Za-z0-9*@$!']+", lambda m: mask_word(m.group(0)) if is_bad(m.group(0)) else m.group(0),
                  str(text))


def find(words, start, end, pad=0.08):
    """Swear words in the cut [start, end]: (masked_words, [(a, b) seconds from cut start])."""
    out_words, spans = [], []
    for w in words:
        if is_bad(w["w"]):
            out_words.append(dict(w, w=mask_word(w["w"]), censored=True))
            if w["e"] >= start and w["s"] <= end:
                a = max(0.0, w["s"] - start - pad)
                b = min(end - start, w["e"] - start + pad * 0.6)
                if b - a < 0.18:                 # very short timestamp: bleep at least ~0.2 s
                    b = a + 0.2
                spans.append((a, b))
        else:
            out_words.append(w)
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return out_words, [(round(a, 2), round(b, 2)) for a, b in merged]


def enable_expr(spans):
    return "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in spans)
