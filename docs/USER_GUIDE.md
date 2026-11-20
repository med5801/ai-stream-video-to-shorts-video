# User Guide

Everything you need to install, run, steer and troubleshoot the Twitch → Shorts pipeline.

## Contents

1. [How it works](#1-how-it-works)
2. [Installation](#2-installation)
3. [Brains: Gemini and Claude](#3-brains-gemini-and-claude)
4. [Your first run](#4-your-first-run)
5. [Reviewing and steering the edit](#5-reviewing-and-steering-the-edit)
6. [Editing features](#6-editing-features)
7. [Command reference](#7-command-reference)
8. [Configuration (`config.json`)](#8-configuration-configjson)
9. [Folders and output files](#9-folders-and-output-files)
10. [Edit plan reference](#10-edit-plan-reference)
11. [Troubleshooting](#11-troubleshooting)
12. [Good practice and content rules](#12-good-practice-and-content-rules)
13. [Automatic modes: live, watcher, resume](#13-automatic-modes-live-watcher-resume)

---

## 1. How it works

| Step | Script | What happens |
|---|---|---|
| 1 | `highlights.py` | Downloads the VOD's chat (seconds, a few hundred KB), scores every 30 s window by chat activity, picks the top moments (≥ 2 min apart) and downloads **only those parts** of the video (~90 s each, source quality). |
| 2 | `transcribe.py` | Whisper writes a word-level transcript of each clip (GPU if available). |
| 3 | `frames.py` | Makes contact sheets (one frame every 3 s, timestamped) and one full frame with a 10 % grid so the brain can see the layout. |
| 4 | `brain.py` | Sends frames + transcript + chat + your rules to Gemini (default) or Claude. Gets back an **edit plan** (JSON): keep or skip, cut points, layout, hook, zooms, scenes, stickers, images, sound effects, chat moments, title and description. |
| 5 | You | Approve, skip, or type an idea → the brain revises the plan. |
| 6 | `render.py` | ffmpeg builds the 1080×1920 / 30 fps Short. Clips scoring below `min_score` (7.5) are skipped; swear words are masked in the captions and bleeped in the audio. |
| 7 | You | The video opens. Approve, or type an idea → re-plan and re-render. |

`run.py` drives the whole chain; `run.bat` is the double-click launcher.

**Why chat?** Chat usually reacts 2–6 seconds after something happens. Scoring chat instead of
the video lets the tool scan a 6-hour stream in seconds and download only the parts worth
looking at.

---

## 2. Installation

### Requirements

- Windows 10 or 11
- Python 3.10 – 3.13 (`winget install Python.Python.3.13`)
- ~3 GB free disk space (FFmpeg, Whisper model, clips)
- Optional: an NVIDIA GPU for faster transcription

### Steps

1. **Get the code.** Clone the repository or download the ZIP from GitHub and extract it.
   Prefer a path **without non-English characters** (e.g. `C:\shorts`). Folders like
   `Desktop` in some languages contain characters that a few libraries dislike; the pipeline
   works around them, but a plain path avoids surprises.
2. **Run `setup.bat`** (double-click). It will:
   - create a Python virtual environment in `.venv`
   - install `faster-whisper`, `opencv-python`, `numpy`, `yt-dlp` (+ CUDA libraries if an NVIDIA GPU is found)
   - download the latest **TwitchDownloaderCLI** into `tools/TwitchDownloader/`
   - download **FFmpeg** (`ffmpeg.exe`, `ffprobe.exe`) into the same folder
   - create the `vods`, `shorts`, `assets`, `sfx` folders
   - check whether `agy`, `claude` and/or `gemini` are installed
3. **Install a brain** (section 3).

The first transcription downloads the Whisper model (`medium`, ~1.5 GB) once.

If Windows SmartScreen says *"Windows protected your PC"* when running a `.bat` or `.exe`:
**More info → Run anyway.**

---

## 3. Brains: Gemini and Claude

The "brain" is an AI command-line tool that reads the frames and writes the edit plan. All
of them are used through **your own subscription login** – no API keys.

| | Gemini via Antigravity CLI (**default**) | Claude Code | Gemini CLI (older) |
|---|---|---|---|
| `brain` value | `gemini` (aliases `antigravity`, `agy`) | `claude` | `gemini_cli` |
| Install | install Google's Antigravity CLI so that `agy` works in a terminal | `irm https://claude.ai/install.ps1 \| iex` (PowerShell) | `npm install -g @google/gemini-cli` |
| Log in once | run `agy`, sign in with Google | run `claude`, follow the browser login | run `gemini`, *Sign in with Google* |
| Model | `agy_model` (default `gemini-4-hydrogen-medium`) | `claude_model` | `gemini_model` |
| How images are read | the prompt is written to `agy_prompt.md`, agy opens it and the frame files | `Read` tool on the frame files | `@file` references |

### Switching

In `tools/config.json`:

```json
"brain": "gemini",
"agy_cmd": "agy",
"agy_model": "gemini-4-hydrogen-medium"
```

- `"gemini"` – Gemini only (default). If `agy` is not on your `PATH`, put its full path in
  `agy_cmd` (e.g. `C:\\Users\\you\\AppData\\Local\\agy\\bin\\agy.exe`).
- `"claude"` – Claude only.
- `"auto"` – tries `order` (default `["gemini", "claude"]`) from left to right; on a usage
  limit (`429`, *rate limit*, *quota*…) or an error it falls back to the next one.
- One-off override: `run.bat 2929768251 --brain claude`

One brain call is made per clip (plus one per revision you request).

### Score threshold

The brain scores every clip 1–10 **with one decimal** and is told to be strict (average funny
moments get 5–7). Only clips scoring at least `min_score` (default **7.5**) become Shorts; in
automatic modes the rest are skipped, in review mode you're asked. Want more videos? Lower it
to 7. Fewer? Raise it to 8. `--force` renders a clip anyway.

---

## 4. Your first run

1. Find a VOD: open a streamer's *Videos* tab on Twitch, open a past broadcast; the URL is
   `twitch.tv/videos/2929768251` → the id is `2929768251`. (The link works too.)
2. Double-click `run.bat`, paste the id, press Enter.
3. Watch it go:

```
[1] Finding hype moments and downloading them...
[2/3] blau | 2:57:57 | 3973 messages, analysing...
#1  0:12:55 - 0:14:25  (41 messages / 30 s)  -> clip_01_0h12m55s.mp4
...
=== clip_01  (stream time 0:12:55, 90 s) ===
  transcribing...
  whisper: medium / cuda
  extracting frames...
  brain: claude is thinking...

  ┌─ clip_01  [claude]
  │ Verdict: KEEP   Score: 8/10   Streamer can't believe the chat's take.
  │ Cut  : 31.5-58.0 s (27 s)   Layout: stacked
  │ Hook : HE DID NOT SEE THAT COMING
  │ Zoom : 44.2s game x1.25
  │ Scenes: 47.0s stacked [camera 55%] → 50.5s stacked
  │ Sticker: 45.0s 'BRO WHAT'
  │ SFX  : 44.1s whoosh, 45.0s boom
  │ Chat : 49.0s (4 s)
  │ Title: He did NOT see that coming 💀 #shorts
  └─
  [Enter] approve  [s] skip  [k] frames  [a] open assets folder  or TYPE YOUR IDEA:
```

4. Press Enter to approve → it renders → the video opens → Enter to accept.
5. Your Short is in `shorts/<date>/<streamer>_<vod>_<nn>/short.mp4`, with the title,
   description and hashtags in `meta.txt`.

Clips, transcripts, frames and plans are cached in `vods/<id>/`, so running the same VOD again
is fast and does not re-download anything.

---

## 5. Reviewing and steering the edit

You can give the editor ideas in **four places**. Ideas can be written in any language.

### 5.1 Standing rules – `tools/editor_notes.txt`

Every non-`#` line is sent to the brain for **every** clip as rules from the channel owner.

```
Hooks must be 4 words or fewer.
Never pick a cut that starts with swearing.
Zoom into the gameplay during police chases.
```

### 5.2 An idea for one run – `--idea`

```
run.bat 2929768251 --idea "prefer funny fails over clutch plays, use the boom sfx a lot"
```

### 5.3 After each plan (interactive)

| Key | Action |
|---|---|
| `Enter` | approve the plan and render |
| `s` | skip this clip |
| `k` | open the frame sheets the brain saw |
| `a` | open the `assets` folder (drop an image in, then type your idea) |
| *any text* | your idea → the brain revises the plan and shows it again |

If the brain recommends skipping: `Enter` = skip, `r` = render anyway, or type an idea.

Examples:
- *"start 3 seconds earlier, the setup is missing"*
- *"make the camera big when he screams at 41 s"*
- *"put pro_player_jersey.png when he says he's a pro"*
- *"no scene changes, keep it calm"*

### 5.4 After the render (interactive)

The finished video opens automatically. `Enter` accepts it; typing an idea re-plans and
re-renders the same clip (overwriting it).

### 5.5 Later – `revise.bat`

Asks for the VOD id, the clip number and your idea, then re-plans and re-renders that clip.
Same as `run.bat <vod> --only <n> --replan --idea "..."`.

Turn the questions off for unattended runs with `--no-review` (or `review_plan` /
`review_render` in `config.json`). Without review, clips the brain rejects or scores below
`min_score` are skipped.

---

## 6. Editing features

### 6.1 Cut

The brain picks a 15–45 s cut from the ~90 s raw clip: start just before the setup, end right
after the payoff, never mid-sentence.

### 6.2 Layouts

| Layout | Looks like | Used for |
|---|---|---|
| `stacked` | facecam on top, gameplay below | gameplay with a facecam box |
| `fullcam` | camera fills the screen | Just Chatting, big reactions |
| `game` | gameplay square in the middle, blurred copy behind | gameplay without a facecam |
| `game_full` | gameplay fills the vertical screen (narrow slice) | following action in one spot |

Box fields (fractions 0–1 of the source frame): `cam = [x, y, w, h]` (facecam box),
`game = [x, y, w, h]` (gameplay area), `focus_x` / `focus_y` (centre of interest),
`zoom` (1.0–2.5 static zoom into the gameplay around the focus), `cam_ratio` (0.2–0.6 share of
the screen height for the camera in `stacked`).

When you approve a `stacked` plan, the camera box is saved per streamer in
`tools/layouts.json` and offered to the brain next time. Edit or delete entries there if a
streamer changes their overlay.

### 6.3 Scenes (layout changes inside a clip)

Up to 4 hard cuts that change layout, camera size, focus or zoom mid-clip – e.g. enlarge the
camera when the streamer reacts, jump to `game_full` zoomed on a crash on the left, then go
back. Fields not given in a scene are inherited from the base layout. The brain is told not to
change more often than every ~3 s and to pair changes with a whoosh.

### 6.4 Punch-in zooms

Up to 3 short zooms (0.6–2 s, ×1.1–1.4) on `game`, `cam` or `all`, eased in and out.

### 6.5 Hook

2–6 words in ALL CAPS, shown for `hook_seconds` (default 2.5 s) in a yellow box. It must tease
the payoff without spoiling it or lying.

### 6.6 Captions

Two words at a time (breaks on punctuation), **static** (no bouncing), the word being spoken in
`highlight_color`, emphasis words always highlighted and slightly bigger. Font: Montserrat
ExtraBold (bundled). Captions stay out of the bottom ~20 % where YouTube's UI sits.

### 6.7 Stickers

Short pop-in text (1–4 words) such as `PRO PLAYER?` or `BRO WHAT`, tilted, in yellow / red /
white / green, at top / center / bottom, for 0.5–3 s. They are drawn by the renderer – the AI
designs them, no image generation is needed.

### 6.8 Image overlays (`assets/`)

Put images in `assets/` with descriptive file names. The brain sees the list of file names and
can place up to 2 images per Short (position top / center / bottom, 20–100 % of the width,
1–3 s, with a fade). The folder is re-read on every brain call, so you can add images while a
run is waiting for your review. If you ask for an image that does not exist, the brain uses a
sticker instead and says so.

> The brains used here cannot generate images. Use your own images or ones you are allowed to
> use.

### 6.9 Sound effects (`sfx/`)

The brain places 2–6 effects per Short at zooms, punchlines, stickers and the hook. If it
places none, a whoosh is added on every zoom (`auto_whoosh_on_zoom`). Effects are mixed under
the loudness-normalised voice and limited to avoid clipping. Volume: `sfx_volume`.

| Name | Description |
|---|---|
| `whoosh` | fast air whoosh, for zooms, cuts and transitions |
| `swoosh_up` | rising swoosh, for text/sticker or image appearing |
| `pop` | short bubbly pop, for stickers, emphasis words, small accents |
| `ding` | bright bell ding, for a correct guess, realization or 'ding ding' moment |
| `boom` | deep dramatic meme boom (vine-boom style), for shocking or awkward punchlines |
| `bass_drop` | long bass drop, for a big reveal or hype moment |
| `riser` | 2 s tension riser that builds up, put it right before the payoff |
| `click` | tiny UI click, for subtle accents |
| `camera_shutter` | camera shutter snap, for freeze frames or 'caught in 4K' moments |
| `glitch` | digital glitch stutter, for 'brain broken' / error / confusion moments |
| `buzzer` | wrong-answer buzzer, for fails, bad takes, 'nope' moments |
| `record_scratch` | record scratch stop, for 'wait, what?' / sudden stop moments |
| `heartbeat` | two heartbeat thumps, for tense / suspense moments |
| `dramatic_hit` | orchestral-style dramatic hit, for epic or over-dramatic moments |
| `sad_trombone` | sad descending 'wah wah wah' notes, for fails |

**Adding your own:** drop a `.wav` / `.mp3` / `.ogg` / `.m4a` into `sfx/` with a descriptive
name (`bruh.mp3`). Optionally describe it in `sfx/sfx.json`
(`"bruh": "deadpan 'bruh' voice, for dumb moments"`) – good descriptions help the brain choose.
Regenerate the built-in set any time with `python tools/make_sfx.py sfx`.

### 6.10 On-screen chat

A Twitch-style chat box (coloured usernames, messages appearing one by one) shown for 2–5 s
when chat reacts. The brain picks up to 2 moments; reaction messages (KEKW, LUL, LMAO, 😂…)
and short messages are preferred. If the brain shows none, the 4 s with the most laughing
inside the cut is used automatically when it has at least 4 such messages (`auto_chat`).
Emoji that the caption font cannot draw are removed; long messages are shortened.

### 6.11 Audio

The stream audio is loudness-normalised to −14 LUFS (YouTube's reference), sound effects are
mixed on top and a limiter keeps peaks below 0 dBFS.

### 6.11a Profanity censoring

Rule-based (no AI involved), from Whisper's word timestamps (`tools/censor.py`):

- **Captions:** only the swear word is masked – `FUCKING` → `F******`.
- **Audio:** the same span is muted and a 1 kHz bleep plays (`"censor_mode": "mute"` = silence only).
- The hook, stickers, title/description and the on-screen chat box are masked too.
- Built-in list: fuck, shit, bitch, ass, dick, cock, pussy, cunt, the n-word, slurs, etc.
  Innocent look-alikes (*class, assassin, cockpit, Dickens*…) are left alone.
- Add your own words to `tools/profanity_list.txt` (one per line).
- The render prints `(censored 2 word(s) at 3.9s, 10.8s)` and `plan.json` gets a `censored` list.
- Turn it off with `"censor": false`.

### 6.12 Title, description, hashtags

Written by the brain to `meta.txt` (title ≤ 60 characters ending with `#shorts`, two-sentence
description with a question for comments, 5–8 hashtags), together with the score and the
brain's reason.

---

## 7. Command reference

### `run.bat` / `run.py`

```
run.bat <vod id or link> [options]
```

| Option | Meaning |
|---|---|
| `--brain gemini\|claude\|auto\|gemini_cli` | override `brain` from `config.json` |
| `--idea "..."` | your editing idea for this run (alias: `--fikir`) |
| `--top N` | number of moments to find when downloading (default 5) |
| `--only 1,3` | process only these clip numbers |
| `--replan` | ignore saved plans, ask the brain again |
| `--redownload` | find and download the clips again |
| `--force-transcribe` | redo the transcription |
| `--no-review` | no questions, fully automatic |
| `--force` | render even if the brain says skip / the score is below `min_score` |

Passing `--idea` also forces a re-plan.

### `highlights.bat` / `highlights.py`

Use it alone to explore a VOD before editing.

| Option | Default | Meaning |
|---|---|---|
| `--top` | 5 | number of moments |
| `--window` | 30 | chat counting window (s) |
| `--pre` | 35 | seconds kept before the peak window |
| `--post` | 25 | seconds kept after the peak window |
| `--gap` | 120 | minimum seconds between two moments |
| `--quality` | 1080p60 | video quality (falls back to the best available) |
| `--dry-run` | | list the moments without downloading video |
| `--chat FILE` | | use an existing chat JSON |

`vods/<id>/highlights.txt` lists every moment with a Twitch link and the chat messages – useful
for picking clips by hand.

### `revise.bat`

Interactive re-edit of one clip (see 5.5).

### `live.bat` / `watcher.bat` / `resume.bat`

See section 13.

---

## 8. Configuration (`config.json`)

`tools/config.json` – anything missing falls back to the default.

| Key | Default | Meaning |
|---|---|---|
| `brain` | `"gemini"` | `gemini` (Antigravity CLI), `claude`, `gemini_cli` or `auto` |
| `order` | `["gemini","claude"]` | try order for `auto` |
| `agy_cmd` / `agy_model` / `agy_effort` | `"agy"` / `"gemini-4-hydrogen-medium"` / `""` | Antigravity CLI command (or full path), model, effort |
| `claude_cmd` / `gemini_cmd` | `"claude"` / `"gemini"` | command names or full paths |
| `claude_model` / `gemini_model` | `""` | model override (empty = CLI default) |
| `brain_timeout_sec` | 400 | max seconds per brain call |
| `whisper_model` | `"medium"` | `small` is faster, `large-v3` more accurate (needs more VRAM) |
| `whisper_device` | `"auto"` | `auto` (GPU then CPU), `cuda` or `cpu` |
| `review_plan` / `review_render` | `true` | ask for your review after the plan / after the render |
| `min_score` | 7.5 | clips scored lower are skipped (or flagged in review) |
| `reason_language` | `"English"` | language of the brain's one-line reason |
| `caption_font` | `"Montserrat ExtraBold"` | any installed font or one in `tools/fonts` |
| `caption_size` | 92 | caption size (px at 1080×1920) |
| `caption_color` / `highlight_color` | `#FFFFFF` / `#FFE600` | caption colours |
| `hook_seconds` | 2.5 | how long the hook stays |
| `auto_chat` | `true` | show chat at the biggest laugh if the brain chose none |
| `auto_whoosh_on_zoom` | `true` | add a whoosh on zooms if the brain chose no sfx |
| `sfx_volume` | 0.8 | master volume for sound effects |
| `chat_lines` | 5 | max lines in the chat box |
| `censor` | `true` | mask swear words in captions and bleep them in the audio |
| `censor_mode` | `"bleep"` | `bleep` (1 kHz tone) or `mute` |
| `censor_bleep_volume` / `censor_pad` | 0.22 / 0.08 | bleep loudness / seconds added around each word |
| `watch_mode` | `"full"` | watcher: `full` (Shorts) or `highlights` (clips only) |
| `twitch_client_id` / `twitch_client_secret` | `""` | optional Twitch API keys for the watcher |
| `fps` / `crf` | 30 / 20 | output frame rate / H.264 quality (lower = better) |

---

## 9. Folders and output files

```
repo/
├─ run.bat  highlights.bat  revise.bat  setup.bat  live.bat  watcher.bat  resume.bat
├─ tools/
│  ├─ run.py brain.py render.py frames.py transcribe.py highlights.py shorts_common.py
│  ├─ live.py watcher.py resume.py censor.py
│  ├─ config.json        settings
│  ├─ editor_notes.txt   your standing rules
│  ├─ profanity_list.txt extra words to censor
│  ├─ streamers.txt      streamers for the watcher
│  ├─ watcher_state.json processed VODs (created by the watcher)
│  ├─ layouts.json       saved camera boxes per streamer
│  ├─ fonts/             Montserrat ExtraBold (OFL)
│  └─ TwitchDownloader/  TwitchDownloaderCLI.exe, ffmpeg.exe, ffprobe.exe (from setup)
├─ assets/               your images
├─ sfx/                  sound effects + sfx.json
├─ vods/<vod id>/        chat.json, highlights.txt, clips.json, clip_NN_*.mp4
│  └─ work/clip_NN/      words.json, sheet_*.jpg, layout.jpg, frames.json, plan.json
└─ shorts/<date>/<streamer>_<vod>_<nn>/
   ├─ short.mp4          the Short
   ├─ meta.txt           title, description, hashtags, score, reason
   ├─ check.jpg          3×3 frame grid for a quick visual check
   ├─ plan.json          the plan that was rendered
   └─ subs.ass           captions / hook / stickers / chat (editable in Aegisub)
```

`vods/` can get large – delete old VOD folders whenever you like.

---

## 10. Edit plan reference

The brain returns this JSON (times are seconds from the start of the raw clip). Everything
is validated and clamped before rendering; unknown images or sound names are dropped.

```json
{
  "keep": true,
  "score": 8.1,
  "reason": "one sentence: why",
  "cut": {"start": 31.5, "end": 58.0},
  "layout": "stacked",
  "cam": [0.70, 0.05, 0.28, 0.30],
  "game": [0.0, 0.0, 1.0, 1.0],
  "focus_x": 0.5, "focus_y": 0.5, "zoom": 1.0, "cam_ratio": 0.32,
  "scenes": [
    {"t": 47.0, "cam_ratio": 0.55},
    {"t": 50.5, "layout": "game_full", "focus_x": 0.3, "focus_y": 0.6, "zoom": 1.6},
    {"t": 53.0, "layout": "stacked"}
  ],
  "hook": "HE DID NOT SEE THAT COMING",
  "zooms": [{"t": 44.2, "dur": 1.2, "target": "game", "scale": 1.25}],
  "emphasis": ["NO", "WAY"],
  "overlays": [{"t": 45.0, "dur": 2.0, "image": "pro_player_jersey.png", "pos": "top", "size": 0.6}],
  "stickers": [{"t": 45.0, "dur": 1.5, "text": "BRO WHAT", "pos": "center", "color": "yellow"}],
  "sfx": [{"t": 44.1, "name": "whoosh", "vol": 0.6}, {"t": 45.0, "name": "boom", "vol": 0.9}],
  "chat_show": [{"t": 49.0, "dur": 4.0}],
  "title": "He did NOT see that coming #shorts",
  "description": "Two sentences. A question for the comments?",
  "hashtags": ["#gta6", "#shorts"]
}
```

You can edit `vods/<id>/work/clip_NN/plan.json` by hand (the `plan` object) and run
`run.bat <id> --only NN` to render your edited plan.

---

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| Window closes immediately | Open a terminal in the folder (type `cmd` in Explorer's address bar) and run the `.bat` there to see the error. |
| `'claude' command not found` / `'gemini' ...` | Install the brain (section 3) and open a **new** terminal. Or set the full path in `claude_cmd` / `gemini_cmd`. |
| `[LIMIT] ... usage limit reached` | Normal on heavy days. `auto` switches brains; otherwise wait for the reset or use `--brain`. |
| `The model did not return JSON` | Rare. Run again with `--only N --replan`. |
| Gemini ignores the frames | Make sure your Gemini CLI is up to date (`npm update -g @google/gemini-cli`). Without frames it still decides from transcript and chat. |
| `whisper: medium / cpu` although you have a GPU | Re-run `setup.bat` (installs the CUDA libraries) and start via `run.bat` (it adds them to `PATH`). |
| Out of GPU memory | `"whisper_model": "small"` |
| Captions in a different font | Check that `tools/fonts/Montserrat-ExtraBold.ttf` exists. |
| Wrong camera box | Type an idea in the review (*"the camera is bottom-left"*) or fix/delete the streamer in `tools/layouts.json`. |
| VOD not found | The VOD was deleted, is sub-only, or the streamer disabled VODs. Twitch keeps VODs only 7–60 days. |
| `av` / `metadata_errors` error | `.venv\Scripts\pip install "av==16.0.1"` |
| `'agy' command not found` | Install/sign in to the Antigravity CLI, or put its full path in `agy_cmd`. |
| A swear word wasn't bleeped | Whisper spelled it differently – add that spelling to `tools/profanity_list.txt` and re-render with `run.bat <id> --only N`. |
| Almost no Shorts are made | `min_score` 7.5 is strict on purpose; lower it to 7 in `config.json`. |
| Live mode says "not live" | The streamer is offline, or yt-dlp is outdated: `.venv\Scripts\pip install -U yt-dlp`. |

---

## 12. Good practice and content rules

- **Ask permission / respect creators.** Many streamers welcome clip channels, some don't.
  Credit the streamer in the description.
- **Transform the content.** Hook, captions, zooms, stickers and commentary make a Short more
  than a re-upload. YouTube may limit monetization of *reused content* that adds nothing.
- **Use assets and sounds you have the rights to.** The bundled sound effects and font are free
  to use; your own `assets/` and added sounds are your responsibility.
- **Never commit secrets.** Cookie files and `.env` are git-ignored – keep it that way.
- **Check before uploading.** Look at `check.jpg` or watch the Short; the brain can be wrong
  about timing or context.

---

## 13. Automatic modes: live, watcher, resume

All three run without questions (`--no-review`); only clips above `min_score` become Shorts.
Check `check.jpg` before uploading.

### 13.1 Live mode – `live.bat`

Listens to the chat while the stream is running and cuts a moment ~2 minutes after chat
explodes (Twitch needs that long to write it to the VOD).

```
live.bat xqc                      clips only
live.bat xqc --render             also a Short from every clip
live.bat xqc --ratio 1.7          catch more moments (huge chats)
live.bat xqc --backfill 5         also the best 5 moments since the stream started
live.bat xqc --render --resume    continue after the last clip of a previous run
```

| Option | Default | Meaning |
|---|---|---|
| `--ratio` | 2.2 | spike threshold (× the 5-min median). Huge chats 1.6–1.8, medium 2.0–2.2, small 2.5+ |
| `--hype-ratio` | 2.5 | second threshold that only counts hype words (KEKW, OMEGALUL…) |
| `--backfill N` / `--backfill-from 4h` | 0 / 0 | also scan the stream's past chat for the best N moments |
| `--resume` | | start the history scan where the last clip of this VOD ended |
| `--max-clips` | 30 | max clips **in this run** (earlier clips don't count) |
| `--gap` | 150 | minimum seconds between moments |

One window per streamer. Every minute it prints a status line
(`last 30 s score 407 (normal 391) = 1.0x`). If the streamer has VODs disabled it switches to
**recording mode**: the stream is recorded in 10 s segments and clips are cut from there
(the past can't be scanned in this mode).

### 13.2 Watcher – `watcher.bat`

Checks the streamers in `tools/streamers.txt` every 30 minutes and processes each newly
finished stream automatically. `--list` only lists, `--once` checks once, `--mode highlights`
downloads clips without the brain. Processed VODs are stored in `tools/watcher_state.json`
(delete an entry to process it again). Optional Twitch API keys in `config.json` make it
notice the end of a stream immediately.

### 13.3 Resume – `resume.bat`

For half-finished streams (window closed, PC went to sleep, `--max-clips` reached…):

```
resume.bat                 every half-finished VOD of the last 4 days
resume.bat 2932113307      only this VOD
resume.bat --list          only shows the status
resume.bat --top 8         at most 8 new moments per VOD (default: 1 per 15 min left)
resume.bat --no-render     only download
```

- **Stream ended:** downloads the chat from the end of the last clip to the end of the VOD,
  finds the best moments, downloads them (numbering continues) and makes Shorts.
- **Still live:** opens `live.py <streamer> --render --resume` in a new window.
- Clips that were downloaded but never processed are queued too.
- Recording-mode folders (ids starting with 9, no VOD) can't go back in time; only their
  unprocessed clips are done.
