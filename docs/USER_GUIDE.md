# User Guide

Everything you need to install, run, steer and troubleshoot the Twitch → Shorts pipeline.

## Contents

1. [How it works](#1-how-it-works)
2. [Installation](#2-installation)
3. [Brains: Claude and Gemini](#3-brains-claude-and-gemini)
4. [Your first run](#4-your-first-run)
5. [Reviewing and steering the edit](#5-reviewing-and-steering-the-edit)
6. [Editing features](#6-editing-features)
7. [Command reference](#7-command-reference)
8. [Configuration (`config.json`)](#8-configuration-configjson)
9. [Folders and output files](#9-folders-and-output-files)
10. [Edit plan reference](#10-edit-plan-reference)
11. [Troubleshooting](#11-troubleshooting)
12. [Good practice and content rules](#12-good-practice-and-content-rules)
13. [Hands-free: live mode and the watcher](#13-hands-free-live-mode-and-the-watcher)

---

## 1. How it works

| Step | Script | What happens |
|---|---|---|
| 1 | `highlights.py` | Downloads the VOD's chat (seconds, a few hundred KB), scores every 30 s window by chat activity, picks the top moments (≥ 2 min apart) and downloads **only those parts** of the video (~90 s each, source quality). |
| 2 | `transcribe.py` | Whisper writes a word-level transcript of each clip (GPU if available). |
| 3 | `frames.py` | Makes contact sheets (one frame every 3 s, timestamped) and one full frame with a 10 % grid so the brain can see the layout. |
| 4 | `brain.py` | Sends frames + transcript + chat + your rules to Claude or Gemini. Gets back an **edit plan** (JSON): keep or skip, cut points, layout, hook, zooms, scenes, stickers, images, sound effects, chat moments, title and description. |
| 5 | You | Approve, skip, or type an idea → the brain revises the plan. |
| 6 | `render.py` | ffmpeg builds the 1080×1920 / 30 fps Short. |
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
   - install `faster-whisper`, `opencv-python`, `numpy` (+ CUDA libraries if an NVIDIA GPU is found)
   - download the latest **TwitchDownloaderCLI** into `tools/TwitchDownloader/`
   - download **FFmpeg** (`ffmpeg.exe`, `ffprobe.exe`) into the same folder
   - create the `vods`, `shorts`, `assets`, `sfx` folders
   - check whether `claude` and/or `gemini` are installed
3. **Install a brain** (section 3).

The first transcription downloads the Whisper model (`medium`, ~1.5 GB) once.

If Windows SmartScreen says *"Windows protected your PC"* when running a `.bat` or `.exe`:
**More info → Run anyway.**

---

## 3. Brains: Claude and Gemini

The "brain" is an AI command-line tool that reads the frames and writes the edit plan. Both
are used through **your own subscription login** – no API keys.

| | Claude Code | Gemini CLI |
|---|---|---|
| Install | `irm https://claude.ai/install.ps1 \| iex` (PowerShell) | `npm install -g @google/gemini-cli` (needs Node.js: `winget install OpenJS.NodeJS.LTS`) |
| Log in once | run `claude`, follow the browser login | run `gemini`, choose *Sign in with Google* |
| Account | Claude Pro / Max (the free plan has no Claude Code) | personal Google account |
| How images are read | Claude uses its `Read` tool on the frame files | frames are attached with `@file` references |

### Switching

In `tools/config.json`:

```json
"brain": "auto",
"order": ["claude", "gemini"]
```

- `"auto"` – tries `order` from left to right. If the first brain hits a usage limit
  (`429`, *rate limit*, *quota*, *usage limit*…) or fails, it **automatically** falls back to
  the next one and prints which brain was used.
- `"claude"` or `"gemini"` – use only that one.
- One-off override: `run.bat 2929768251 --brain gemini`
- Want Gemini first? `"order": ["gemini", "claude"]`
- Specific models: `"claude_model": "sonnet"`, `"gemini_model": "<model name>"` (empty = CLI default).

One brain call is made per clip (plus one per revision you request).

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

### 6.12 Title, description, hashtags

Written by the brain to `meta.txt` (title ≤ 60 characters ending with `#shorts`, two-sentence
description with a question for comments, 5–8 hashtags), together with the score and the
brain's reason. A credit block is added automatically under the description:

```
🎥 Credit: xQc → https://www.twitch.tv/xqc
📺 Full stream: https://www.twitch.tv/videos/2932113307?t=2h22m20s
```

The second link opens the stream at the exact second the clip starts.

---

## 7. Command reference

### `run.bat` / `run.py`

```
run.bat <vod id or link> [options]
```

| Option | Meaning |
|---|---|
| `--brain auto\|claude\|gemini` | override `brain` from `config.json` |
| `--idea "..."` | your editing idea for this run (alias: `--fikir`) |
| `--top N` | number of moments to find when downloading (default 5) |
| `--only 1,3` | process only these clip numbers |
| `--replan` | ignore saved plans, ask the brain again |
| `--redownload` | find and download the clips again |
| `--force-transcribe` | redo the transcription |
| `--no-review` | no questions, fully automatic |
| `--force` | render even if the brain says *skip* or the score is below `min_score` |

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

### `live.bat` / `live.py`

```
live.bat <twitch username> [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--render` | | turn every clip into a Short right away (`run.py --only N --no-review`) |
| `--ratio` | 2.2 | spike threshold: last 30 s chat score ÷ median of the last 5 min |
| `--min-score` | 15 | minimum chat score in the window |
| `--window` / `--pre` / `--post` | 30 / 35 / 25 | same meaning as in `highlights.py` |
| `--gap` | 150 | minimum seconds between two moments |
| `--max-clips` | 30 | stop clipping after this many |
| `--quality` | 1080p60 | video quality |
| `--test` | | force one clip right after warm-up (checks the whole pipeline) |

### `watcher.bat` / `watcher.py`

| Option | Default | Meaning |
|---|---|---|
| `--once` | | one check, then exit (for Windows Task Scheduler) |
| `--list` | | only list the latest VODs per streamer and their status |
| `--every` | 30 | minutes between checks |
| `--last` | 3 | how many latest VODs to look at per streamer |
| `--backlog` | 1 | for a newly added streamer, how many existing VODs to process |
| `--mode full\|highlights` | `watch_mode` | `full` makes Shorts, `highlights` only downloads clips |

---

## 8. Configuration (`config.json`)

`tools/config.json` – anything missing falls back to the default.

| Key | Default | Meaning |
|---|---|---|
| `brain` | `"auto"` | `auto`, `claude` or `gemini` |
| `order` | `["claude","gemini"]` | try order for `auto` |
| `claude_cmd` / `gemini_cmd` | `"claude"` / `"gemini"` | command names or full paths |
| `claude_model` / `gemini_model` | `""` | model override (empty = CLI default) |
| `brain_timeout_sec` | 400 | max seconds per brain call |
| `whisper_model` | `"medium"` | `small` is faster, `large-v3` more accurate (needs more VRAM) |
| `whisper_device` | `"auto"` | `auto` (GPU then CPU), `cuda` or `cpu` |
| `review_plan` / `review_render` | `true` | ask for your review after the plan / after the render |
| `min_score` | 6 | clips scored lower are skipped (or flagged in review) |
| `reason_language` | `"English"` | language of the brain's one-line reason |
| `caption_font` | `"Montserrat ExtraBold"` | any installed font or one in `tools/fonts` |
| `caption_size` | 92 | caption size (px at 1080×1920) |
| `caption_color` / `highlight_color` | `#FFFFFF` / `#FFE600` | caption colours |
| `hook_seconds` | 2.5 | how long the hook stays |
| `auto_chat` | `true` | show chat at the biggest laugh if the brain chose none |
| `auto_whoosh_on_zoom` | `true` | add a whoosh on zooms if the brain chose no sfx |
| `sfx_volume` | 0.8 | master volume for sound effects |
| `chat_lines` | 5 | max lines in the chat box |
| `fps` / `crf` | 30 / 20 | output frame rate / H.264 quality (lower = better) |
| `watch_mode` | `"full"` | watcher default: `full` (Shorts) or `highlights` (clips only) |
| `twitch_client_id` / `twitch_client_secret` | `""` | optional Twitch API app for the watcher (or set `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` env vars to keep them out of git) |

---

## 9. Folders and output files

```
repo/
├─ run.bat  highlights.bat  revise.bat  setup.bat  live.bat  watcher.bat
├─ tools/
│  ├─ run.py brain.py render.py frames.py transcribe.py highlights.py shorts_common.py
│  ├─ live.py watcher.py
│  ├─ streamers.txt      streamers for the watcher (one per line)
│  ├─ watcher_state.json VODs the watcher already handled
│  ├─ config.json        settings
│  ├─ editor_notes.txt   your standing rules
│  ├─ layouts.json       saved camera boxes per streamer
│  ├─ fonts/             Montserrat ExtraBold (OFL)
│  └─ TwitchDownloader/  TwitchDownloaderCLI.exe, ffmpeg.exe, ffprobe.exe (from setup)
├─ assets/               your images
├─ sfx/                  sound effects + sfx.json
├─ vods/<vod id>/        chat.json, highlights.txt, clips.json, clip_NN_*.mp4
│  └─ work/clip_NN/      words.json, sheet_*.jpg, layout.jpg, frames.json, plan.json
└─ shorts/<date>/<streamer>_<vod>_<nn>/
   ├─ short.mp4          the Short
   ├─ meta.txt           title, description, streamer credit, hashtags, score, reason
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
  "score": 8,
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
| `live.py`: "is not live right now" | The streamer is offline, or yt-dlp is outdated: `.venv\Scripts\pip install -U yt-dlp`. |
| `live.py`: "No VOD found for the live stream" | The streamer has VODs disabled – live mode needs the live VOD. |
| `live.py` never finds a moment | Big chats rarely reach 2.2×. Watch the per-minute `= 1.0x` status lines and lower `--ratio` (1.6–1.8 for very large chats). |
| Watcher keeps saying "waiting" | Without the Twitch API the watcher needs two checks with the same VOD length to know a stream ended. Add API keys to skip the wait. |

---

## 12. Good practice and content rules

- **Ask permission / respect creators.** Many streamers welcome clip channels, some don't.
  The streamer credit is added to `meta.txt` automatically – keep it in the description.
- **Prefer authorised clipping.** Many streamers and brands run paid clipping campaigns
  (e.g. on Whop Content Rewards or Vyro) where clipping is explicitly allowed and you are paid
  per view. Read each campaign's brief (required tags, platforms, length) before posting.
- **Transform the content.** Hook, captions, zooms, stickers and commentary make a Short more
  than a re-upload. YouTube may limit monetization of *reused content* that adds nothing.
- **Use assets and sounds you have the rights to.** The bundled sound effects and font are free
  to use; your own `assets/` and added sounds are your responsibility.
- **Never commit secrets.** Cookie files and `.env` are git-ignored – keep it that way.
- **Check before uploading.** Look at `check.jpg` or watch the Short; the brain can be wrong
  about timing or context.

---

## 13. Hands-free: live mode and the watcher

Typing VOD ids does not scale: every stream of every streamer has a new one. Two tools find
them for you.

### 13.1 Live mode – clip while the stream is still running

```
live.bat xqc                 # clips only
live.bat xqc --render        # clips + a Short for each, while the stream goes on
live.bat xqc --ratio 1.7     # catch more moments (very large chats)
```

1. yt-dlp checks that the channel is live and finds the stream's start time and **live VOD id**
   (Twitch creates the archive VOD as soon as the stream starts).
2. The script joins the Twitch chat **anonymously** (read-only IRC, no account) and scores every
   message with the same hype weights as `highlights.py`.
3. Every 5 s it compares the last 30 s with the median of the last 5 minutes. When the ratio
   passes `--ratio` it keeps watching 30 s more and keeps the strongest window.
4. About 1.5 minutes later (Twitch needs a moment to write the VOD) it downloads **only that
   part** of the live VOD with TwitchDownloader – typically in under 30 seconds.
5. `chat.json`, `clips.json` and `highlights.txt` are written in the normal format, so
   `run.bat <vod id>` works on them later as well.

A status line is printed every minute:

```
chat: 2078 msgs total | last 30 s score 407 (normal 391) = 1.0x | clips 0
🔥 MOMENT #1: 2:22:20-2:23:50  chat 2.6x  (download in ~114 s)
✔ #1 ready: ...\vods\2932113307\clip_01_2h22m20s.mp4
```

Use the `= 1.0x` numbers to tune `--ratio`: small chats 2.5+, medium 2.0–2.2, huge 1.6–1.8.
Open one window per streamer. When the stream ends the script downloads any pending clips and
exits.

### 13.2 Watcher – process finished streams automatically

1. Put Twitch usernames in `tools/streamers.txt` (one per line, `#` for comments). The file is
   created on the first run.
2. Run `watcher.bat --list` once to see what it finds.
3. Leave `watcher.bat` running (or schedule `watcher.bat --once` with Task Scheduler).

Every 30 minutes it lists each streamer's latest archive VODs and runs `run.py <vod> --no-review`
on each new **finished** stream (at least 20 minutes long). It skips:

- streams that are still live (they are handled once they end),
- VODs you already processed by hand (clips in `vods/<id>` or a Short in `shorts/`),
- older VODs of a newly added streamer (only the latest `--backlog` ones are processed),
- VODs it already handled (`tools/watcher_state.json`; delete an entry to redo it).

**VOD source.** By default it uses yt-dlp, no setup needed; a stream counts as finished when
its VOD length is unchanged between two checks. For instant detection create a free app at
[dev.twitch.tv/console](https://dev.twitch.tv/console) and set its Client ID and Secret
(config.json or the `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` environment variables).

`--mode highlights` (or `"watch_mode": "highlights"`) only downloads the clips – no brain calls,
no tokens – so you can review them later with `run.bat`.

> Automatic modes render without asking. Always look at `check.jpg` before uploading.

