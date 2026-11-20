"""Edit engine: applies the edit plan -> 1080x1920 Short (ffmpeg only)."""
import os
import re

import censor
from shorts_common import ASSETS, TOOLS, config, ffmpeg, probe, save_json, sfx_path

OW, OH = 1080, 1920


def even(v):
    return int(round(v / 2.0)) * 2


def ass_color(hexstr):
    h = hexstr.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def zoom_expr(zooms):
    ex = "1"
    for z in zooms:
        a, b = z["t"], z["t"] + z["dur"]
        ex += f"+{z['scale'] - 1:.3f}*clip((t-{a:.2f})/0.15,0,1)*clip(({b:.2f}-t)/0.15,0,1)"
    return ex


def zoom_filter(zooms, w, h):
    if not zooms:
        return ""
    return (f",scale=w='trunc({w}*({zoom_expr(zooms)})/2)*2':h=-2:eval=frame,"
            f"crop={w}:{h}:(iw-ow)/2:(ih-oh)/2")


def region(rect, W, H, ow, oh, focus_x=None):
    """Crops rect (fractions) from the source and scales it to cover ow x oh."""
    x, y, w, h = rect
    px, py = even(x * W), even(y * H)
    pw, ph = max(16, even(w * W)), max(16, even(h * H))
    pw, ph = min(pw, W - px), min(ph, H - py)
    fx = 0.5 if focus_x is None else min(1, max(0, (focus_x * W - px) / pw))
    if pw / ph > ow / oh:
        sw, sh = even(pw * oh / ph), oh
        cx = int(min(sw - ow, max(0, fx * sw - ow / 2)))
        cy = 0
    else:
        sw, sh = ow, even(ph * ow / pw)
        cx, cy = 0, (sh - oh) // 2
    sw, sh = max(sw, ow), max(sh, oh)
    return f"crop={pw}:{ph}:{px}:{py},scale={sw}:{sh},crop={ow}:{oh}:{cx}:{cy},setsar=1"


def group_words(words, start, end):
    ws = [w for w in words if w["s"] >= start - 0.15 and w["e"] <= end + 0.3]
    groups, cur = [], []
    for w in ws:
        cur.append(w)
        if len(cur) == 2 or re.search(r"[.!?,]$", w["w"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def clean(word):
    w = re.sub(r"[\\{}]", "", word).upper()
    return re.sub(r"^[\"'(\[]+|[\"'.,;:)\]]+$", "", w)


LAUGH = re.compile(r"kekw|lul|lmao|lmfao|omegalul|icant|lol|haha|xd|😂|🤣|💀|pepelaugh|dead|what|wtf|bro", re.I)
PALETTE = ["&H004040FF", "&H00FFB040", "&H0040FF40", "&H00FF40FF", "&H0040FFFF", "&H00FF8080", "&H0000A5FF"]


def strip_text(t):
    t = "".join(ch for ch in str(t) if ord(ch) < 0x10000 and not 0xFE00 <= ord(ch) <= 0xFE0F)
    t = re.sub(r"[\\{}]", "", t).strip()
    return t[:42] + ("…" if len(t) > 42 else "")


def user_color(name, hexcol):
    if hexcol and re.fullmatch(r"#?[0-9a-fA-F]{6}", hexcol):
        return ass_color(hexcol if hexcol.startswith("#") else "#" + hexcol)
    return PALETTE[sum(map(ord, name or "?")) % len(PALETTE)]


def auto_chat(plan, chat, dur_clip):
    """If the brain showed no chat: find the 4 s inside the cut where chat laughs most."""
    s, e = plan["cut"]["start"], plan["cut"]["end"]
    best, best_t = 0, None
    t = s + 1
    while t < e - 3:
        n = sum(1 for m in chat if t <= m["t"] < t + 4 and LAUGH.search(m["body"]))
        if n > best:
            best, best_t = n, t
        t += 0.5
    if best >= 4:
        return [{"t": best_t, "dur": 3.5}]
    return []


def chat_events(plan, info, c, dur):
    chat = info.get("chat_msgs") or []
    cz = censor.mask_text if c["censor"] else (lambda x: x)
    s = plan["cut"]["start"]
    x, y, an = info["chat"]
    out = []
    for cs in plan.get("chat_show", []):
        a = cs["t"] - s
        if a < 0 or a >= dur:
            continue
        b = min(dur, a + cs["dur"])
        # Messages from 4 s before the moment; short reaction messages first
        pool = [m for m in chat if cs["t"] - 4 <= m["t"] <= cs["t"] + cs["dur"] and strip_text(m["body"])]
        pool.sort(key=lambda m: (not LAUGH.search(m["body"]), len(m["body"])))
        msgs = sorted(pool[: c["chat_lines"]], key=lambda m: m["t"])
        if not msgs:
            continue
        step = (b - a) / (len(msgs) + 1)
        for k in range(len(msgs)):
            shown = msgs[max(0, k + 1 - c["chat_lines"]): k + 1]
            text = "\\N".join(f"{{\\c{user_color(m['user'], m.get('color'))}}}{strip_text(m['user'])}"
                                f"{{\\c&H00FFFFFF&}}: {strip_text(cz(m['body']))}" for m in shown)
            t0 = a + k * step
            t1 = a + (k + 1) * step if k + 1 < len(msgs) else b
            fade = "\\fad(120,0)" if k == 0 else ""
            fade = "\\fad(0,180)" if k + 1 == len(msgs) else fade
            out.append(f"Dialogue: 2,{ts(t0)},{ts(t1)},Chat,,0,0,0,,{{\\an{an}\\pos({x},{y}){fade}}}{text}")
    return out


def ts(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def write_ass(path, plan, words, layout_info, c):
    s, e = plan["cut"]["start"], plan["cut"]["end"]
    dur = e - s
    font, size = c["caption_font"], c["caption_size"]
    white, hl = ass_color(c["caption_color"]), ass_color(c["highlight_color"])
    cap_y, hook_y = layout_info["cap_y"], layout_info["hook_y"]
    out = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {OW}", f"PlayResY: {OH}",
        "ScaledBorderAndShadow: yes", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font},{size},{white},{white},&H00000000,&H90000000,-1,0,0,0,100,100,1,0,1,7,2,5,60,60,0,1",
        f"Style: Hook,{font},{int(size * 0.82)},&H00000000,&H00000000,{hl},&H90000000,-1,0,0,0,100,100,1,0,3,16,0,5,70,70,0,1",
        f"Style: Sticker,{font},118,{hl},{hl},&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,9,4,5,40,40,0,1",
        f"Style: Chat,{font},48,&H00FFFFFF,&H00FFFFFF,&H55101010,&H00000000,-1,0,0,0,100,100,0,0,3,12,0,7,40,40,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if plan.get("hook"):
        hook = re.sub(r"[\\{}]", "", plan["hook"])
        out.append(f"Dialogue: 1,{ts(0)},{ts(min(dur, c['hook_seconds']))},Hook,,0,0,0,,"
                   f"{{\\pos({OW // 2},{hook_y})\\fad(80,150)}}{hook}")
    emph = {clean(w) for w in plan.get("emphasis", [])}
    groups = group_words(words, s, e)
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["s"] - s if gi + 1 < len(groups) else dur
        g_end = min(nxt, g[-1]["e"] - s + 0.6, dur)
        toks = [clean(w["w"]) for w in g]
        for wi, w in enumerate(g):
            a = w["s"] - s
            b = g[wi + 1]["s"] - s if wi + 1 < len(g) else g_end
            if b <= a:
                continue
            parts = []
            for k, t in enumerate(toks):
                if not t:
                    continue
                big = "\\fscx112\\fscy112" if t in emph else ""
                col = hl if (k == wi or t in emph) else white
                parts.append(f"{{\\c{col}{big}}}{t}{{\\fscx100\\fscy100}}")
            if parts:
                out.append(f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,"
                           f"{{\\pos({OW // 2},{cap_y})}}" + " ".join(parts))
    colors = {"yellow": hl, "red": "&H003C3CFF", "white": "&H00FFFFFF", "green": "&H0040FF7A"}
    for st in plan.get("stickers", []):
        a = st["t"] - s
        if a < 0 or a >= dur:
            continue
        y = layout_info["pos"][st["pos"]]
        txt = re.sub(r"[\\{}]", "", st["text"])
        out.append(f"Dialogue: 3,{ts(a)},{ts(min(dur, a + st['dur']))},Sticker,,0,0,0,,"
                   f"{{\\pos({OW // 2},{y})\\frz-7\\c{colors[st['color']]}"
                   f"\\fscx55\\fscy55\\t(0,110,\\fscx112\\fscy112)\\t(110,190,\\fscx100\\fscy100)"
                   f"\\fad(0,150)}}{txt}")
    out += chat_events(plan, layout_info, c, dur)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(out) + "\n")


def subrect(rect, zoom, fx, fy):
    """Sub-area of rect, centred on (fx, fy) (full-frame fractions), zoomed in by `zoom`."""
    x, y, w, h = rect
    if zoom <= 1.001:
        return rect
    nw, nh = w / zoom, h / zoom
    nx = min(x + w - nw, max(x, fx - nw / 2))
    ny = min(y + h - nh, max(y, fy - nh / 2))
    return [nx, ny, nw, nh]


def compose(spec, W, H, src, out, zs):
    """One scene layout -> 1080x1920. src: input label, out: output label."""
    pick = lambda *targets: [z for z in zs if z["target"] in targets]
    lay = spec["layout"]
    fx, fy = spec.get("focus_x", 0.5), spec.get("focus_y", 0.5)
    game = subrect(spec["game"], spec.get("zoom", 1.0), fx, fy)
    tag = out.strip("[]")
    if lay == "stacked":
        cx, cy, cw, ch = spec["cam"]
        if spec.get("cam_ratio"):
            cam_h = even(min(1150, max(400, OH * spec["cam_ratio"])))
        else:
            cam_h = even(min(840, max(540, OW * (ch * H) / (cw * W))))
        game_h = OH - cam_h
        g = (f"{src}split=2[{tag}c][{tag}g];"
             f"[{tag}c]{region(spec['cam'], W, H, OW, cam_h)}{zoom_filter(pick('cam', 'all'), OW, cam_h)}[{tag}cc];"
             f"[{tag}g]{region(game, W, H, OW, game_h, fx)}{zoom_filter(pick('game', 'all'), OW, game_h)}[{tag}gg];"
             f"[{tag}cc][{tag}gg]vstack=2{out}")
        info = {"cap_y": cam_h + 260, "hook_y": cam_h, "chat": (40, 1490, 1),
                "pos": {"top": cam_h - 130, "center": cam_h + game_h // 2, "bottom": 1420}}
    elif lay == "game":
        g = (f"{src}split=2[{tag}b][{tag}f];"
             f"[{tag}b]{region([0, 0, 1, 1], W, H, OW, OH)},boxblur=24:2,eq=brightness=-0.12[{tag}bb];"
             f"[{tag}f]{region(game, W, H, OW, OW, fx)}{zoom_filter(pick('game', 'all'), OW, OW)}[{tag}ff];"
             f"[{tag}bb][{tag}ff]overlay=0:{(OH - OW) // 2}{out}")
        info = {"cap_y": (OH + OW) // 2 - 170, "hook_y": (OH - OW) // 2 - 110,
                "chat": (40, (OH - OW) // 2 + 30, 7),
                "pos": {"top": 560, "center": OH // 2, "bottom": 1380}}
    elif lay == "game_full":
        g = f"{src}{region(game, W, H, OW, OH, fx)}{zoom_filter(pick('game', 'all'), OW, OH)}{out}"
        info = {"cap_y": 1250, "hook_y": 380, "chat": (40, 520, 7),
                "pos": {"top": 300, "center": 960, "bottom": 1450}}
    else:  # fullcam: fills the screen with the cam box if known, else the (zoomed) game/full frame
        rect = spec["cam"] if spec.get("cam") else game
        g = f"{src}{region(rect, W, H, OW, OH, fx)}{zoom_filter(pick('cam', 'game', 'all'), OW, OH)}{out}"
        info = {"cap_y": 1250, "hook_y": 380, "chat": (40, 520, 7),
                "pos": {"top": 300, "center": 960, "bottom": 1450}}
    return g, info


def build_graph(plan, W, H):
    s, e = plan["cut"]["start"], plan["cut"]["end"]
    zs = [dict(z, t=z["t"] - s) for z in plan.get("zooms", [])]
    base = {k: plan.get(k) for k in ("layout", "cam", "game", "focus_x", "focus_y", "zoom", "cam_ratio")}
    scenes = [sc for sc in plan.get("scenes", []) if s < sc["t"] < e]
    if not scenes:
        return compose(base, W, H, "[0:v]", "[base]", zs)
    # Each scene is built in its own branch and overlaid during its time range (hard cut)
    n = len(scenes) + 1
    g = f"[0:v]split={n}" + "".join(f"[sv{i}]" for i in range(n)) + ";"
    part, info = compose(base, W, H, "[sv0]", "[sc0]", zs)
    g += part
    last = "sc0"
    for i, sc in enumerate(scenes, 1):
        a = sc["t"] - s
        b = (scenes[i]["t"] - s) if i < len(scenes) else (e - s + 1)
        part, _ = compose(sc, W, H, f"[sv{i}]", f"[sc{i}]", zs)
        g += f";{part};[{last}][sc{i}]overlay=0:0:enable='gte(t,{a:.2f})*lt(t,{b:.2f})'[sm{i}]"
        last = f"sm{i}"
    g += f";[{last}]null[base]"
    return g, info


def rel(path):
    try:
        return os.path.relpath(path, TOOLS).replace("\\", "/")
    except ValueError:  # different drive
        return path.replace("\\", "/").replace(":", "\\:")


def render(clip, plan, words, out_dir, chat=None):
    """chat: [{"t": seconds in clip, "user", "body", "color"}] (for the on-screen chat box)."""
    c = config()
    os.makedirs(out_dir, exist_ok=True)
    plan = dict(plan)
    src = probe(clip)
    W, H = src["width"], src["height"]
    s, e = plan["cut"]["start"], plan["cut"]["end"]
    dur = e - s
    chat = chat or []
    if c["auto_chat"] and not plan.get("chat_show") and chat:
        plan["chat_show"] = auto_chat(plan, chat, dur)
        if plan["chat_show"]:
            print(f"  (auto chat at {plan['chat_show'][0]['t']:.1f} s)")
    if c["auto_whoosh_on_zoom"] and not plan.get("sfx") and sfx_path("whoosh"):
        plan["sfx"] = [{"t": max(0, z["t"] - 0.12), "name": "whoosh", "vol": 0.6} for z in plan.get("zooms", [])]

    spans = []
    if c["censor"]:
        # Profanity: only the word is masked in the captions; that span is bleeped/muted in the audio
        words, spans = censor.find(words, s, e, c["censor_pad"])
        for k in ("hook", "title", "description"):
            plan[k] = censor.mask_text(plan.get(k, ""))
        plan["stickers"] = [dict(x, text=censor.mask_text(x["text"])) for x in plan.get("stickers", [])]
        plan["emphasis"] = [censor.mask_word(w) if censor.is_bad(w) else w for w in plan.get("emphasis", [])]
        plan["censored"] = [list(x) for x in spans]
        if spans:
            print(f"  (censored {len(spans)} word(s) at " + ", ".join(f"{a:.1f}s" for a, _ in spans) + ")")

    graph, info = build_graph(plan, W, H)
    info["chat_msgs"] = chat
    ass = os.path.join(out_dir, "subs.ass")
    write_ass(ass, plan, words, info, c)

    inputs = ["-ss", f"{s:.2f}", "-t", f"{dur:.2f}", "-i", clip]
    idx, last = 1, "base"
    for k, ov in enumerate(plan.get("overlays", [])):
        path = os.path.join(ASSETS, ov["image"])
        a = ov["t"] - s
        if not os.path.exists(path) or a < 0 or a >= dur:
            continue
        b = min(dur, a + ov["dur"])
        w = even(OW * ov["size"])
        y = info["pos"][ov["pos"]]
        inputs += ["-loop", "1", "-t", f"{dur:.2f}", "-i", path]
        graph += (f";[{idx}:v]scale={w}:-2,format=rgba,"
                  f"fade=t=in:st={a:.2f}:d=0.15:alpha=1,fade=t=out:st={max(a, b - 0.2):.2f}:d=0.2:alpha=1[ov{k}];"
                  f"[{last}][ov{k}]overlay=x=(W-w)/2:y={y}-h/2:enable='between(t,{a:.2f},{b:.2f})'[ob{k}]")
        last, idx = f"ob{k}", idx + 1
    graph += (f";[{last}]fps={c['fps']},ass='{rel(ass)}':fontsdir=fonts,format=yuv420p[v];"
              f"[0:a]aresample=48000,aformat=channel_layouts=stereo")
    if spans:
        # Mute BEFORE loudnorm: loudnorm buffers frames, so timestamps drift after it
        ex = censor.enable_expr(spans)
        graph += f",volume=0:enable='{ex}'"
    graph += ",loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a0]"
    mix = ["[a0]"]
    if spans and c["censor_mode"] == "bleep":
        # classic 1 kHz TV bleep, only inside the censored spans
        graph += (f";aevalsrc='{c['censor_bleep_volume']:.2f}*sin(2*PI*1000*t)*gt({ex},0)'"
                  f":s=48000:d={dur:.2f},aformat=channel_layouts=stereo[bleep]")
        mix.append("[bleep]")
    for k, fx in enumerate(plan.get("sfx", [])):
        path = sfx_path(fx["name"])
        a = fx["t"] - s
        if not path or a < 0 or a >= dur:
            continue
        inputs += ["-i", path]
        graph += (f";[{idx}:a]aresample=48000,volume={fx['vol'] * c['sfx_volume']:.2f},"
                  f"adelay={int(a * 1000)}:all=1[fx{k}]")
        mix.append(f"[fx{k}]")
        idx += 1
    if len(mix) > 1:
        graph += f";{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95[a]"
    else:
        graph += ";[a0]anull[a]"

    out = os.path.join(out_dir, "short.mp4")
    ffmpeg(inputs + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", str(c["crf"]),
                     "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], cwd=TOOLS)
    n = 9
    ffmpeg(["-i", out, "-vf", f"fps={n / max(1.0, e - s):.4f},scale=360:-2,tile=3x3",
            "-frames:v", "1", os.path.join(out_dir, "check.jpg")])
    with open(os.path.join(out_dir, "meta.txt"), "w", encoding="utf-8") as f:
        credit = f"{plan['credit']}\n\n" if plan.get("credit") else ""
        f.write(f"TITLE:\n{plan.get('title', '')}\n\nDESCRIPTION:\n{plan.get('description', '')}\n\n"
                f"{credit}{' '.join(plan.get('hashtags', []))}\n\n---\nScore: {plan.get('score')} | "
                f"{plan.get('reason', '')}\n")
    save_json(os.path.join(out_dir, "plan.json"), plan)
    return out
