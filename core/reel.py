#!/usr/bin/env python3
"""Video engine v3 — one engine for every format, from a 60-second Pulse to a 40-minute Masterclass.

FORMATS (engine/formats.json)
  pulse        45-90 s    4:5 + 9:16                 LinkedIn, Instagram Reels, YouTube Shorts, X
  brief        3-5 min    16:9 + 4:5 (+ 9:16 clips)  YouTube, LinkedIn native, X, Reels/Shorts teaser
  deepdive     15-20 min  16:9 (+ 4:5/9:16 clips)    YouTube; clips everywhere else
  masterclass  30-40 min  16:9 (+ 4:5/9:16 clips)    YouTube + website; clip series everywhere else

COMMANDS (run inside ~/Movies/video-pipeline/.venv)
  reel.py new <format> [audience] "<title>"   next numbered project + draft.json skeleton (audience: everyday, business,
                                          boardroom, tech, founders, leadership, academy — default tech)
  reel.py scaffold <project>              draft.json -> project.json, scenes/*, posts/*   (never overwrites media)
  reel.py preview <project> [aspect]      one still per scene -> build/sheet_<aspect>.jpg
  reel.py render  <project> [--only 16x9,4x5,clips] [--force]
  reel.py thumbs  <project>               thumbnails / covers only
  reel.py check   <project>               durations vs format, loudness, chapters, captions, posts, pending clips

PROJECT LAYOUT (projects/<NNN-slug>/)
  draft.json  project.json  posts/<platform>.md
  scenes/scene-NN-name/   scene.json · vo_script.md · prompt.md · take_a/b.mp3 · audio.mp3
                          video_<aspect>.mp4 (rendered)  or  video.mp4 (Flow clip) + video_edit_<aspect>.mp4
  build/words/<scene>.json  word timings (subs.py) -> cue-timed reveals + .srt captions
  build/<variant>/          video.mp4, voice.wav, watermark.mov, timeline.json (Resolve)
  final/  <slug>_<aspect>.mp4 · <slug>_<aspect>.srt · clips/<slug>_<clip>_<aspect>.mp4 · thumbnail_16x9.jpg ·
          cover_4x5.jpg · cover_9x16.jpg · chapters.txt · posts/*.md · PUBLISH.md (what to upload where)

DRAFT (draft.json)
  {"format": "deepdive", "title": "...", "slug": "...", "intro": "standard", "outro": "standard",
   "thumbnail": {"title": "3-5 punchy words", "accent": "green line", "photo": "refs/v2-16x9/03_closeup.jpg"},
   "cutdowns": [{"name": "clip1", "title": "...", "scenes": ["hook-1", "ch2-diagram", "cta-clip"]}],
   "scenes": [ {"name": "...", "type": "...", "vo": "[tone] ElevenLabs text", "brief": "...", ...fields} ]}
  Any scene may set "min" (seconds), "cues" (one phrase per item: the item appears when that phrase is spoken),
  "cutdown_only": true (used only in cut-downs, e.g. a clip-specific hook or CTA).
SCENE TYPES
  hook     lines[], strike, accent, sub            title     kicker, title, accent, label, caption
  flow     label, nodes[], active, new[], legacy, caption
  cards    title, items[{h,s,cue}]                 compare   title, left{h,items[]}, right{h,items[]}
  stat     number, label, source                   tip       kicker, text, sub
  cta      question, line, follow                  chapter   title, sub (number automatic)
  bullets  kicker, title, items[str|{t,cue}]       quote     text, by, role
  diagram  title, nodes[{id,label,x,y,kind,cue}] (x,y 0-1; kind new|legacy|ext|focus), edges[[from,to,label]], caption
  timeline title, steps[{h,s,cue}]                 image     src (png/jpg, e.g. a Flow still), title, label, caption, zoom
  clip     source | (none = pending), flow_prompt, line, angle, lower_third{h,s}   — Flow @me clip with its own voice
  broll    shots[{src, cue, label, in, zoom, speed, loop, y}], label, caption — real-life footage under the narration;
           src = screen:<name> (recorded screen) · lib:<id> (assets/broll/lib, see broll.py) · path to video/image
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import cairo, json, math, os, re, shutil, subprocess, sys, random, glob, hashlib, time
from multiprocessing import Pool

ENGINE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.environ.get("VIDEO_PIPELINE", os.path.expanduser("~/Movies/video-pipeline"))   # old-layout paths, served by compat_links.py
BRAND = json.load(open(os.path.join(ENGINE, "brand.json")))
FORMATS = json.load(open(os.path.join(ENGINE, "formats.json")))
AUDIENCES = json.load(open(os.path.join(ENGINE, "audiences.json")))
BROLL = os.path.join(PIPE, "assets", "broll")
FPS = BRAND["fps"]
ASPECTS = {"4x5": (1080, 1350), "9x16": (1080, 1920), "16x9": (1920, 1080)}
THUMB_SIZE = {"16x9": (1280, 720), "4x5": (1080, 1350), "9x16": (1080, 1920)}
BG = (0.043, 0.071, 0.125); INK = (0.96, 0.97, 0.99); MUTED = (0.60, 0.66, 0.76)
GREEN = (0.24, 0.86, 0.59); AMBER = (0.95, 0.65, 0.25); CARD = (0.08, 0.12, 0.19)
LINE = (0.22, 0.28, 0.38); DEEPG = (0.07, 0.20, 0.16)
FONT = os.environ.get("VIDEO_FONT", BRAND.get("font", "Inter"))
LEAD, TAIL, FADE = 0.35, 0.55, 0.35
P = 1.5  # period (s) of every looping animation, so held frames can be cached and repeated
DEFAULT_MIN = {"hook": 5.0, "title": 6.0, "flow": 6.0, "cards": 7.0, "compare": 7.0, "stat": 5.5, "tip": 6.5, "cta": 5.0,
               "chapter": 4.5, "bullets": 6.0, "quote": 6.0, "diagram": 7.0, "timeline": 7.0, "image": 5.0, "broll": 4.0}
ANIMATED = {"title", "flow", "cta", "diagram"}
pj = os.path.join


# ---------- helpers ----------
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease(x):
    x = clamp(x); return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2
def outback(x):
    x = clamp(x); c1 = 1.4; c3 = c1 + 1; return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2
def p(t, a, b): return ease((t - a) / (b - a))
def lerp(a, b, k): return a + (b - a) * k
def mix(c1, c2, k): return tuple(lerp(a, b, k) for a, b in zip(c1, c2))
def setc(ctx, c, a=1.0): ctx.set_source_rgba(c[0], c[1], c[2], a)
def cyc(t, k=1, off=0.0): return (t / P * k + off) % 1
def pulse(t): return 0.5 + 0.5 * math.sin(2 * math.pi * t / P)


def font(ctx, size, weight="bold"):
    ctx.select_font_face(FONT, cairo.FONT_SLANT_NORMAL,
                         cairo.FONT_WEIGHT_BOLD if weight == "bold" else cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)


def tw(ctx, s, size, weight="bold"):
    font(ctx, size, weight); return ctx.text_extents(s).x_advance


def text(ctx, s, x, y, size, color=INK, a=1.0, weight="bold", align="left", spacing=0):
    if a <= 0 or not s: return 0
    font(ctx, size, weight)
    if spacing:
        ws = [ctx.text_extents(ch).x_advance + spacing for ch in s]; w = sum(ws) - spacing
    else:
        w = ctx.text_extents(s).x_advance
    if align == "center": x -= w / 2
    elif align == "right": x -= w
    setc(ctx, color, a)
    if spacing:
        cx = x
        for ch, wd in zip(s, ws): ctx.move_to(cx, y); ctx.show_text(ch); cx += wd
    else:
        ctx.move_to(x, y); ctx.show_text(s)
    return w


def wrap(ctx, s, size, maxw, weight="bold"):
    font(ctx, size, weight); lines, cur = [], ""
    for w_ in (s or "").split():
        t = (cur + " " + w_).strip()
        if ctx.text_extents(t).x_advance > maxw and cur: lines.append(cur); cur = w_
        else: cur = t
    if cur: lines.append(cur)
    return lines


def fit(ctx, s, size, maxw, max_lines, weight="bold", minsize=24):
    """Shrink font until text fits in max_lines."""
    while size > minsize and len(wrap(ctx, s, size, maxw, weight)) > max_lines: size -= 2
    return size


def para(ctx, s, x, y, size, maxw, color=INK, a=1.0, weight="bold", lh=1.22, align="left"):
    lines = wrap(ctx, s, size, maxw, weight)
    for i, ln in enumerate(lines): text(ctx, ln, x, y + i * size * lh, size, color, a, weight, align)
    return len(lines) * size * lh


def rrect(ctx, x, y, w, h, r):
    r = min(r, w / 2, h / 2); ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0); ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi); ctx.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    ctx.close_path()


def box(ctx, x, y, w, h, r, fill=CARD, stroke=None, a=1.0, lw=3, dash=None):
    if a <= 0: return
    rrect(ctx, x, y, w, h, r)
    if fill is not None: setc(ctx, fill, a); ctx.fill_preserve()
    if stroke is not None:
        setc(ctx, stroke, a); ctx.set_line_width(lw)
        if dash: ctx.set_dash(dash)
        ctx.stroke(); ctx.set_dash([])
    else: ctx.new_path()


def arrowhead(ctx, x, y, ang, size, color, a):
    setc(ctx, color, a); ctx.move_to(x, y)
    ctx.line_to(x - size * math.cos(ang - 0.45), y - size * math.sin(ang - 0.45))
    ctx.line_to(x - size * math.cos(ang + 0.45), y - size * math.sin(ang + 0.45)); ctx.close_path(); ctx.fill()


class Lay:
    """Canvas for one aspect. Vertical aspects share one 1080x1350 content box (centred on 9:16)."""
    def __init__(self, aspect):
        self.aspect = aspect; self.W, self.H = ASPECTS[aspect]; self.land = aspect == "16x9"
        if self.land:
            self.CW, self.CH, self.oy, self.M = 1920, 1080, 0, 110
            self.cap_y, self.cap_w = 905, 980
            self.bar = (110, 1080 - 34, 1700)
        else:
            self.CW, self.CH, self.M = 1080, 1350, 72
            self.oy = (self.H - 1350) // 2
            self.cap_y, self.cap_w = 1005, 936
            self.bar = (72, self.oy + 1350 - 58, 936)
        self.cx = self.CW / 2


def caption(ctx, t, L, label, body, start=0.3):
    k = p(t, start, start + 0.5); y = L.cap_y + 24 * (1 - k)  # sits above / left of the signature badge
    if label: text(ctx, label, L.M, y, 26, GREEN, k, spacing=3); y += 52 if L.land else 58
    size = fit(ctx, body, 42 if L.land else 46, L.cap_w, 2)
    para(ctx, body, L.M, y, size, L.cap_w, INK, k)


# ---------- scenes: draw(ctx, t, sc, L, S) in content coordinates ----------
def s_hook(ctx, t, sc, L, S):
    M = L.M; maxw = L.CW - 2 * M
    y = 330 if L.land else 470; size = 112 if L.land else 96; lines = sc.get("lines", [])
    for ln in lines + [sc.get("accent", "")]:
        while size > 56 and tw(ctx, ln, size) > maxw: size -= 4
    for i, ln in enumerate(lines):
        a = p(t, 0.2 + i * 0.5, 0.8 + i * 0.5)
        text(ctx, ln, M, y + 30 * (1 - a), size, INK, a)
        st = sc.get("strike")
        if st and st in ln:
            s = p(t, 1.4, 1.9)
            if s > 0:
                x0 = M + tw(ctx, ln[: ln.index(st)], size); x1 = x0 + tw(ctx, st, size)
                setc(ctx, AMBER); ctx.set_line_width(9)
                ctx.move_to(x0 - 6, y - size * 0.31); ctx.line_to(lerp(x0 - 6, x1 + 6, s), y - size * 0.31); ctx.stroke()
        y += size * 1.15
    if sc.get("accent"):
        a = p(t, 1.9, 2.5); y += size * 0.5
        text(ctx, sc["accent"], M, y + 30 * (1 - a), size, GREEN, a); y += size * 0.8
    if sc.get("sub"):
        para(ctx, sc["sub"], M, y + 60, 44 if L.land else 40, 1300 if L.land else 900, MUTED, p(t, 2.9, 3.5), "normal")


def network(ctx, t, cx, cy, rad, a, seed=7):
    rnd = random.Random(seed); n = 14
    pts = [(cx + rad * 0.95 * math.cos(2 * math.pi * i / n + rnd.uniform(-0.2, 0.2)) * rnd.uniform(0.35, 1),
            cy + rad * 0.8 * math.sin(2 * math.pi * i / n + rnd.uniform(-0.2, 0.2)) * rnd.uniform(0.35, 1)) for i in range(n)]
    pts.append((cx, cy))
    edges = [(n, i) for i in range(n)] + [(i, (i + 1) % n) for i in range(0, n, 2)]
    for k, (i, j) in enumerate(edges):
        g = p(t, 0.6 + k * 0.06, 1.2 + k * 0.06)
        if g <= 0: continue
        (x0, y0), (x1, y1) = pts[i], pts[j]
        setc(ctx, LINE, a); ctx.set_line_width(3)
        ctx.move_to(x0, y0); ctx.line_to(lerp(x0, x1, g), lerp(y0, y1, g)); ctx.stroke()
        if g >= 1 and k % 3 == 0:
            q = cyc(t, 1, k * 0.13)
            setc(ctx, GREEN, a * math.sin(q * math.pi)); ctx.arc(lerp(x0, x1, q), lerp(y0, y1, q), 6, 0, 2 * math.pi); ctx.fill()
    for k, (x, y) in enumerate(pts):
        s = outback((t - 0.4 - k * 0.07) / 0.5)
        if s <= 0: continue
        r = (34 if k == n else 16) * s
        setc(ctx, DEEPG if k != n else GREEN, a); ctx.arc(x, y, r, 0, 2 * math.pi); ctx.fill()
        setc(ctx, GREEN, a); ctx.set_line_width(3); ctx.arc(x, y, r, 0, 2 * math.pi); ctx.stroke()


def s_title(ctx, t, sc, L, S):
    a = p(t, 0.1, 0.6); title = sc.get("title", "")
    if L.land: x, al, ky, ty, maxw, size, net = L.M, "left", 330, 450, 1000, 104, (1450, 500, 300)
    else: x, al, ky, ty, maxw, size, net = L.cx, "center", 205, 290, L.CW - 144, 76, (L.cx, 680, 300)
    if sc.get("kicker"): text(ctx, sc["kicker"], x, ky, 26, GREEN, a, align=al, spacing=3)
    while size > 50 and max(tw(ctx, title, size), tw(ctx, sc.get("accent", ""), size)) > maxw: size -= 4
    text(ctx, title, x, ty, size, INK, a, align=al)
    if sc.get("accent"): text(ctx, sc["accent"], x, ty + size * 1.05, size, GREEN, a, align=al)
    network(ctx, t, *net, a, seed=len(title))
    if sc.get("caption"): caption(ctx, t, L, sc.get("label", ""), sc["caption"], 0.9)


def node_box(ctx, t, x, y, w, h, label, s, on, isnew, legacy, size=38):
    if s <= 0: return
    cx, cy = x + w / 2, y + h / 2
    ctx.save(); ctx.translate(cx, cy); ctx.scale(s, s); ctx.translate(-cx, -cy)
    fill = DEEPG if (on or isnew) else CARD; stroke = GREEN if (on or isnew) else (AMBER if legacy else LINE)
    box(ctx, x, y, w, h, 20, fill, stroke, clamp(s), 4 if on else 3)
    size = fit(ctx, label, size, w - 40, 2); lines = wrap(ctx, label, size, w - 40)
    y0 = cy + size * 0.35 - (len(lines) - 1) * size * 0.6
    for k, ln in enumerate(lines): text(ctx, ln, cx, y0 + k * size * 1.2, size, INK, clamp(s), align="center")
    ctx.restore()
    if on: box(ctx, x - 8, y - 8, w + 16, h + 16, 26, None, GREEN, 0.25 * pulse(t) * clamp(s), 3)


def s_flow(ctx, t, sc, L, S):
    nodes = sc.get("nodes", [])[:6]; n = len(nodes); act = sc.get("active", -1); new = set(sc.get("new", []))
    if not n: return
    ts = [S.at(i, n, 0.2 + i * 0.35) for i in range(n)]
    if L.land:
        cy = 470; gap = 90; bw = min(380, (L.CW - 2 * L.M - (n - 1) * gap) / n); bh = 170
        x0 = (L.CW - (n * bw + (n - 1) * gap)) / 2; xs = [x0 + i * (bw + gap) for i in range(n)]
        for i in range(n - 1):
            g = p(t, ts[i + 1] - 0.05, ts[i + 1] + 0.35)
            if g <= 0: continue
            a0, a1 = xs[i] + bw, xs[i + 1]
            setc(ctx, LINE); ctx.set_line_width(4); ctx.move_to(a0, cy); ctx.line_to(lerp(a0, a1, g), cy); ctx.stroke()
            if g >= 1:
                arrowhead(ctx, a1 - 2, cy, 0, 16, LINE, 1)
                for j in range(2):
                    q = cyc(t, 1, j / 2 + i * 0.2)
                    setc(ctx, GREEN, math.sin(q * math.pi)); ctx.arc(lerp(a0, a1, q), cy, 7, 0, 2 * math.pi); ctx.fill()
        for i, lab in enumerate(nodes):
            node_box(ctx, t, xs[i], cy - bh / 2, bw, bh, lab, outback((t - ts[i]) / 0.5), i == act, i in new,
                     sc.get("legacy") == i, 36)
    else:
        top, bot = 190, 960; bh = min(110, (bot - top - (n - 1) * 46) / n); gap = (bot - top - n * bh) / max(n - 1, 1)
        bw = 720; x = (L.CW - bw) / 2; ys = [top + i * (bh + gap) for i in range(n)]; cx = L.cx
        for i in range(n - 1):
            g = p(t, ts[i + 1] - 0.05, ts[i + 1] + 0.35)
            if g <= 0: continue
            y0, y1 = ys[i] + bh, ys[i + 1]
            setc(ctx, LINE); ctx.set_line_width(4); ctx.move_to(cx, y0); ctx.line_to(cx, lerp(y0, y1, g)); ctx.stroke()
            if g >= 1:
                setc(ctx, LINE); ctx.move_to(cx - 12, y1 - 14); ctx.line_to(cx, y1 - 2); ctx.line_to(cx + 12, y1 - 14); ctx.stroke()
                for j in range(2):
                    q = cyc(t, 1, j / 2 + i * 0.2)
                    setc(ctx, GREEN, math.sin(q * math.pi)); ctx.arc(cx, lerp(y0, y1, q), 7, 0, 2 * math.pi); ctx.fill()
        for i, lab in enumerate(nodes):
            node_box(ctx, t, x, ys[i], bw, bh, lab, outback((t - ts[i]) / 0.5), i == act, i in new, sc.get("legacy") == i, 38)
    if sc.get("caption"): caption(ctx, t, L, sc.get("label", ""), sc["caption"], 0.3)


def s_cards(ctx, t, sc, L, S):
    a = p(t, 0.1, 0.6); M = L.M
    items = sc.get("items", [])[:4]; n = len(items); ts = [S.at(i, n) for i in range(n)]
    if L.land:
        text(ctx, sc.get("title", ""), M, 230, 72, INK, a)
        if n <= 3:
            cw = (L.CW - 2 * M - (n - 1) * 40) / max(n, 1); ch = 470
            for i, it in enumerate(items):
                k = p(t, ts[i], ts[i] + 0.6); x = M + i * (cw + 40); y = 300 + 30 * (1 - k)
                box(ctx, x, y, cw, ch, 26, CARD, LINE, k, 2)
                text(ctx, f"{i+1:02d}", x + 40, y + 100, 64, GREEN, k)
                hs = fit(ctx, it.get("h", ""), 46, cw - 80, 2); hh = para(ctx, it.get("h", ""), x + 40, y + 190, hs, cw - 80, INK, k)
                if it.get("s"): para(ctx, it["s"], x + 40, y + 190 + hh + 20, 36, cw - 80, MUTED, k, "normal", 1.3)
        else:
            cw = (L.CW - 2 * M - 40) / 2; ch = 250
            for i, it in enumerate(items):
                k = p(t, ts[i], ts[i] + 0.6); x = M + (i % 2) * (cw + 40); y = 290 + (i // 2) * (ch + 30) + 30 * (1 - k)
                box(ctx, x, y, cw, ch, 26, CARD, LINE, k, 2)
                text(ctx, f"{i+1:02d}", x + 40, y + 95, 56, GREEN, k)
                hs = fit(ctx, it.get("h", ""), 42, cw - 200, 1); text(ctx, it.get("h", ""), x + 150, y + 90, hs, INK, k)
                if it.get("s"): para(ctx, it["s"], x + 150, y + 150, 30, cw - 200, MUTED, k, "normal", 1.3)
    else:
        text(ctx, sc.get("title", ""), M, 260, 76, INK, a)
        ch = 210 if n <= 3 else 170; gap = 40 if n <= 3 else 30
        for i, it in enumerate(items):
            k = p(t, ts[i], ts[i] + 0.6); y = 340 + i * (ch + gap) + 30 * (1 - k)
            box(ctx, M, y, L.CW - 2 * M, ch, 26, CARD, LINE, k, 2)
            text(ctx, f"{i+1:02d}", M + 40, y + ch / 2 + 2, 54, GREEN, k)
            hs = fit(ctx, it.get("h", ""), 44, L.CW - 360, 1)
            text(ctx, it.get("h", ""), M + 158, y + ch / 2 - 6, hs, INK, k)
            if it.get("s"): text(ctx, it["s"], M + 158, y + ch / 2 + 52, fit(ctx, it["s"], 34, L.CW - 360, 1, "normal"), MUTED, k, "normal")


def s_compare(ctx, t, sc, L, S):
    a = p(t, 0.1, 0.6); M = L.M
    if L.land: ty, by, hy, iy, fs, maxh, tsz = 230, 290, 380, 470, 42, 590, 76
    else: ty, by, hy, iy, fs, maxh, tsz = 250, 330, 415, 495, 38, 780, 70
    text(ctx, sc.get("title", ""), M, ty, tsz, INK, a)
    colw = (L.CW - 2 * M - (60 if L.land else 30)) / 2
    def col_h(c):
        return 150 + sum(len(wrap(ctx, it, fs, colw - 100, "normal")) * fs * 1.25 + 34 for it in c.get("items", [])[:5])
    bh = max(400, min(maxh, max(col_h(sc.get("left", {})), col_h(sc.get("right", {})))))
    for side, col, x, d in (("left", AMBER, M, 0.6), ("right", GREEN, L.CW - M - colw, 1.6)):
        c = sc.get(side, {}); k = p(t, d, d + 0.6)
        box(ctx, x, by, colw, bh, 26, CARD, col, k, 3)
        text(ctx, c.get("h", ""), x + 36, hy, fit(ctx, c.get("h", ""), 46, colw - 72, 1), col, k)
        y = iy
        for j, it in enumerate(c.get("items", [])[:5]):
            kk = p(t, d + 0.4 + j * 0.3, d + 0.8 + j * 0.3)
            setc(ctx, col, kk); ctx.arc(x + 46, y - 13, 8, 0, 2 * math.pi); ctx.fill()
            y += para(ctx, it, x + 70, y, fs, colw - 100, INK, kk, "normal", 1.25) + 34


def s_stat(ctx, t, sc, L, S):
    k = max(0.01, outback((t - 0.2) / 0.7)); a = p(t, 0.2, 0.7); num = sc.get("number", "")
    ny, ly, sy, lw_ = (520, 650, 820, 1400) if L.land else (620, 780, 1120, L.CW - 200)
    size = 260 if L.land else 240
    while size > 120 and tw(ctx, num, size) > L.CW - 2 * L.M: size -= 10
    ctx.save(); ctx.translate(L.cx, ny - 60); ctx.scale(k, k); ctx.translate(-L.cx, -(ny - 60))
    text(ctx, num, L.cx, ny, size, GREEN, a, align="center"); ctx.restore()
    para(ctx, sc.get("label", ""), L.cx, ly, 52, lw_, INK, p(t, 0.9, 1.4), "bold", 1.2, "center")
    if sc.get("source"): text(ctx, sc["source"], L.cx, sy, 28, MUTED, p(t, 1.4, 1.9), "normal", "center")


def s_tip(ctx, t, sc, L, S):
    a = p(t, 0.1, 0.6); M = L.M
    if L.land: bx, by, bw, bh, ky, ty, sy, lines = M, 220, L.CW - 2 * M, 590, 310, 420, 720, 3
    else: bx, by, bw, bh, ky, ty, sy, lines = 72, 300, L.CW - 144, 640, 390, 490, 820, 4
    box(ctx, bx, by, bw, bh, 30, DEEPG, GREEN, a, 3)
    text(ctx, sc.get("kicker", "ARCHITECT'S TIP"), bx + 50, ky, 26, GREEN, a, spacing=3)
    b = p(t, 0.6, 1.2); size = fit(ctx, sc.get("text", ""), 84 if L.land else 60, bw - 100, lines)
    para(ctx, sc.get("text", ""), bx + 50, ty + 20 * (1 - b), size, bw - 100, INK, b, "bold", 1.2)
    if sc.get("sub"): para(ctx, sc["sub"], bx + 50, sy, 38, bw - 100, MUTED, p(t, 2.4, 3.0), "normal", 1.3)


def s_cta(ctx, t, sc, L, S):
    a = p(t, 0.1, 0.7); M = L.M; maxw = 1500 if L.land else L.CW - 2 * M; y0 = 360 if L.land else 470
    size = fit(ctx, sc.get("question", ""), 84 if L.land else 80, maxw, 3 if L.land else 4)
    h = para(ctx, sc.get("question", ""), M, y0 + 30 * (1 - a), size, maxw, INK, a, "bold", 1.18)
    y = y0 + h + 40; b = p(t, 1.3, 1.9)
    text(ctx, sc.get("line", ""), M, y, 44, GREEN, b)
    text(ctx, sc.get("follow", ""), M, y + 70, 34, MUTED, p(t, 2.0, 2.6), "normal")
    if b > 0 and not L.land:
        yy = y + 180 + 12 * math.sin(2 * math.pi * t / P)
        setc(ctx, GREEN, b); ctx.set_line_width(7); ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.move_to(M + 28, yy); ctx.line_to(M + 28, yy + 70); ctx.stroke()
        ctx.move_to(M + 6, yy + 48); ctx.line_to(M + 28, yy + 72); ctx.line_to(M + 50, yy + 48); ctx.stroke()
        ctx.set_line_cap(cairo.LINE_CAP_BUTT)


def s_chapter(ctx, t, sc, L, S):
    M = L.M; no = sc.get("number") or (S.chap + 1); title = sc.get("title", ""); sub = sc.get("sub", "")
    a = p(t, 0.1, 0.6); k = max(0.01, outback((t - 0.15) / 0.6)); b = p(t, 0.5, 1.0)
    ky, ny, ty, maxw, nsize = (300, 540, 650, 900, 230) if L.land else (330, 560, 690, L.CW - 2 * M, 220)
    text(ctx, "CHAPTER", M, ky, 26, GREEN, a, spacing=4)
    ctx.save(); ctx.translate(M, ny); ctx.scale(k, k); ctx.translate(-M, -ny)
    text(ctx, f"{no:02d}", M, ny, nsize, GREEN, a); ctx.restore()
    ts = fit(ctx, title, 70 if L.land else 72, maxw, 3); h = para(ctx, title, M, ty + 20 * (1 - b), ts, maxw, INK, b)
    if sub: para(ctx, sub, M, ty + h + 30, 34, maxw, MUTED, p(t, 0.9, 1.4), "normal", 1.3)
    if L.land and S.chapters:
        x = 1150; text(ctx, "IN THIS VIDEO", x, 230, 22, MUTED, a, spacing=3)
        for j, ct in enumerate(S.chapters[:10]):
            kk = p(t, 0.4 + j * 0.08, 0.8 + j * 0.08); yy = 300 + j * 60; cur = j == S.chap
            if cur: box(ctx, x - 22, yy - 41, 680, 58, 10, DEEPG, GREEN, kk, 2)
            al = kk * (1 if cur else (0.5 if j < S.chap else 0.85))
            text(ctx, f"{j+1:02d}", x, yy, 26, GREEN if cur else MUTED, al)
            text(ctx, ct, x + 62, yy, fit(ctx, ct, 32, 570, 1, minsize=18), INK, al)


def item_text(it): return it if isinstance(it, str) else (it.get("t") or it.get("h") or "")


def s_bullets(ctx, t, sc, L, S):
    M = L.M; items = [item_text(it) for it in sc.get("items", [])][:7]; n = len(items)
    a = p(t, 0.1, 0.6); y = 250 if L.land else 260
    if sc.get("kicker"): text(ctx, sc["kicker"], M, y - 80, 26, GREEN, a, spacing=3)
    tmaxw = 1500 if L.land else L.CW - 2 * M
    tsz = fit(ctx, sc.get("title", ""), 84 if L.land else 68, tmaxw, 2)
    th = para(ctx, sc.get("title", ""), M, y, tsz, tmaxw, INK, a)
    y0 = y + th + (40 if L.land else 50); bottom = 860 if L.land else 960
    fs = 56 if L.land else 50; maxw = 1450 if L.land else L.CW - 2 * M - 50
    while fs > 26:
        tot = sum(len(wrap(ctx, it, fs, maxw)) * fs * 1.25 + fs * 0.75 for it in items)
        if y0 + tot <= bottom: break
        fs -= 2
    ts = [S.at(i, n) for i in range(n)]; hl = sc.get("highlight", True); yy = y0 + fs * 0.6
    for i, it in enumerate(items):
        k = p(t, ts[i], ts[i] + 0.45)
        dim = p(t, ts[i + 1], ts[i + 1] + 0.4) if (hl and i + 1 < n) else 0.0
        col = mix(INK, MUTED, dim * 0.75)
        setc(ctx, mix(GREEN, LINE, dim), k); ctx.arc(M + 12, yy - fs * 0.33, 9, 0, 2 * math.pi); ctx.fill()
        yy += para(ctx, it, M + 46 + 12 * (1 - k), yy, fs, maxw, col, k, "bold", 1.25) + fs * 0.75


def s_quote(ctx, t, sc, L, S):
    M = L.M; a = p(t, 0.1, 0.6); b = p(t, 0.5, 1.1)
    qy, ty, maxw, lines, size = (360, 440, 1500, 3, 66) if L.land else (430, 520, L.CW - 2 * M, 5, 60)
    text(ctx, "“", M - 6, qy, 240, GREEN, a)
    size = fit(ctx, sc.get("text", ""), size, maxw, lines)
    h = para(ctx, sc.get("text", ""), M, ty + 20 * (1 - b), size, maxw, INK, b, "bold", 1.22)
    c = p(t, 1.2, 1.7)
    if sc.get("by"): text(ctx, "— " + sc["by"], M, ty + h + 40, 36, GREEN, c)
    if sc.get("role"): text(ctx, sc["role"], M + 34, ty + h + 86, 28, MUTED, c, "normal")


KIND = {"new": (DEEPG, GREEN), "focus": (DEEPG, GREEN), "legacy": (CARD, AMBER), "ext": (BG, MUTED)}


def s_diagram(ctx, t, sc, L, S):
    M = L.M; a = p(t, 0.1, 0.6); nodes = sc.get("nodes", [])[:12]; n = len(nodes)
    text(ctx, sc.get("title", ""), M, 230 if L.land else 250, fit(ctx, sc.get("title", ""), 64, L.CW - 2 * M, 1), INK, a)
    if L.land: ax, aw, ay, ah, fs, maxbw, bh = M + 200, L.CW - 2 * M - 400, 350, 420, 36, 460, 96
    else: ax, aw, ay, ah, fs, maxbw, bh = M + 125, L.CW - 2 * M - 250, 380, 520, 34, 310, 90
    flip = not L.land and not sc.get("keep_layout")  # left-to-right diagrams read top-to-bottom in portrait
    ts = [S.at(i, n) for i in range(n)]; pos = {}
    for i, nd in enumerate(nodes):
        lab = nd.get("label", nd.get("id", "")); size = fit(ctx, lab, fs, maxbw - 50, 1, minsize=18)
        w = clamp(tw(ctx, lab, size) + 56, 150, maxbw); h = bh
        nx, ny = (nd.get("y", 0.5), nd.get("x", 0.5)) if flip else (nd.get("x", 0.5), nd.get("y", 0.5))
        pos[nd.get("id", str(i))] = (ax + nx * aw, ay + ny * ah, w / 2, h / 2, ts[i], lab, size, nd.get("kind"))
    for k, e in enumerate(sc.get("edges", [])):
        if e[0] not in pos or e[1] not in pos: continue
        A, B = pos[e[0]], pos[e[1]]; te = max(A[4], B[4]) + 0.25; g = p(t, te, te + 0.5)
        if g <= 0: continue
        dx, dy = B[0] - A[0], B[1] - A[1]; dist = math.hypot(dx, dy) or 1

        def border(N, sx, sy):
            s = min(N[2] / abs(sx) if sx else 1e9, N[3] / abs(sy) if sy else 1e9); return N[0] + sx * s, N[1] + sy * s
        x0, y0 = border(A, dx, dy); x1, y1 = border(B, -dx, -dy)
        x0 += dx / dist * 6; y0 += dy / dist * 6; x1 -= dx / dist * 8; y1 -= dy / dist * 8
        ex, ey = lerp(x0, x1, g), lerp(y0, y1, g)
        setc(ctx, LINE); ctx.set_line_width(3.5); ctx.move_to(x0, y0); ctx.line_to(ex, ey); ctx.stroke()
        if g >= 1:
            arrowhead(ctx, x1, y1, math.atan2(dy, dx), 15, LINE, 1)
            q = cyc(t, 1, k * 0.29); setc(ctx, GREEN, math.sin(q * math.pi))
            ctx.arc(lerp(x0, x1, q), lerp(y0, y1, q), 6, 0, 2 * math.pi); ctx.fill()
            if len(e) > 2 and e[2]:
                mx, my = (x0 + x1) / 2, (y0 + y1) / 2; lw = tw(ctx, e[2], 22, "normal") + 20
                box(ctx, mx - lw / 2, my - 20, lw, 32, 8, BG, None, 0.9)
                text(ctx, e[2], mx, my + 4, 22, MUTED, 1, "normal", "center")
    for key, (cx, cy, hw, hh, ti, lab, size, kind) in pos.items():
        s = outback((t - ti) / 0.5)
        if s <= 0: continue
        fill, stroke = KIND.get(kind, (CARD, LINE))
        ctx.save(); ctx.translate(cx, cy); ctx.scale(s, s); ctx.translate(-cx, -cy)
        box(ctx, cx - hw, cy - hh, hw * 2, hh * 2, 18, fill, stroke, clamp(s), 4 if kind == "focus" else 3,
            [10, 7] if kind == "ext" else None)
        text(ctx, lab, cx, cy + size * 0.36, size, INK, clamp(s), align="center"); ctx.restore()
        if kind == "focus" and s >= 1: box(ctx, cx - hw - 8, cy - hh - 8, hw * 2 + 16, hh * 2 + 16, 24, None, GREEN, 0.3 * pulse(t), 3)
    if sc.get("caption"): caption(ctx, t, L, sc.get("label", ""), sc["caption"], 0.6)


def s_timeline(ctx, t, sc, L, S):
    M = L.M; steps = sc.get("steps", [])[:6]; n = len(steps); a = p(t, 0.1, 0.6)
    text(ctx, sc.get("title", ""), M, 240 if L.land else 250, fit(ctx, sc.get("title", ""), 64, L.CW - 2 * M, 1), INK, a)
    ts = [S.at(i, n) for i in range(n)]
    cur = max([i for i in range(n) if t >= ts[i]], default=-1)
    if L.land:
        y = 450; x0, x1 = M + 80, L.CW - M - 80
        xs = [lerp(x0, x1, i / (n - 1)) if n > 1 else L.cx for i in range(n)]; cw = (x1 - x0) / max(n - 1, 1) - 30 if n > 1 else 600
        setc(ctx, LINE, a); ctx.set_line_width(4); ctx.move_to(x0, y); ctx.line_to(x1, y); ctx.stroke()
        if cur >= 0:
            g = p(t, ts[cur], ts[cur] + 0.5); xe = lerp(xs[cur - 1] if cur > 0 else x0, xs[cur], g)
            setc(ctx, GREEN); ctx.set_line_width(6); ctx.move_to(x0, y); ctx.line_to(xe, y); ctx.stroke()
        for i, st in enumerate(steps):
            k = outback((t - ts[i]) / 0.5)
            if k <= 0: continue
            setc(ctx, DEEPG); ctx.arc(xs[i], y, 22 * k, 0, 2 * math.pi); ctx.fill()
            setc(ctx, GREEN); ctx.set_line_width(4); ctx.arc(xs[i], y, 22 * k, 0, 2 * math.pi); ctx.stroke()
            kk = clamp(k); hs = fit(ctx, st.get("h", ""), 44, min(cw, 400), 2)
            hh = 0
            for j, ln in enumerate(wrap(ctx, st.get("h", ""), hs, min(cw, 400))):
                text(ctx, ln, xs[i], y + 90 + j * hs * 1.2, hs, INK, kk, align="center"); hh = (j + 1) * hs * 1.2
            for j, ln in enumerate(wrap(ctx, st.get("s", ""), 32, min(cw, 400), "normal")[:3]):
                text(ctx, ln, xs[i], y + 100 + hh + j * 42, 32, MUTED, kk, "normal", "center")
    else:
        x = M + 30; y0, y1 = 360, 940; ys = [lerp(y0, y1, i / (n - 1)) if n > 1 else y0 for i in range(n)]
        setc(ctx, LINE, a); ctx.set_line_width(4); ctx.move_to(x, y0); ctx.line_to(x, y1); ctx.stroke()
        if cur >= 0:
            g = p(t, ts[cur], ts[cur] + 0.5); ye = lerp(ys[cur - 1] if cur > 0 else y0, ys[cur], g)
            setc(ctx, GREEN); ctx.set_line_width(6); ctx.move_to(x, y0); ctx.line_to(x, ye); ctx.stroke()
        for i, st in enumerate(steps):
            k = outback((t - ts[i]) / 0.5)
            if k <= 0: continue
            setc(ctx, DEEPG); ctx.arc(x, ys[i], 20 * k, 0, 2 * math.pi); ctx.fill()
            setc(ctx, GREEN); ctx.set_line_width(4); ctx.arc(x, ys[i], 20 * k, 0, 2 * math.pi); ctx.stroke()
            kk = clamp(k); hs = fit(ctx, st.get("h", ""), 50, L.CW - 2 * M - 90, 1)
            text(ctx, st.get("h", ""), x + 60, ys[i] + 16, hs, INK, kk)
            if st.get("s"): text(ctx, st["s"], x + 60, ys[i] + 64, fit(ctx, st["s"], 36, L.CW - 2 * M - 90, 1, "normal"), MUTED, kk, "normal")


_IMG = {}


def resolve_path(src, root=None):
    src = os.path.expanduser(src)
    if os.path.isabs(src): return src
    for base in ([root] if root else []) + [PIPE]:
        if os.path.exists(pj(base, src)): return pj(base, src)
    return pj(PIPE, src)


def cover_png(src, w, h, bias=0.3, bias_x=0.5):
    os.makedirs(pj(os.environ.get("VIDEO_RUNTIME", os.path.expanduser("~/VideoStudioRuntime")), "reel-cache"), exist_ok=True)
    key = hashlib.md5(f"{src}|{os.path.getmtime(src)}|{w}x{h}|{bias}|{bias_x}".encode()).hexdigest()[:16]
    out = pj(os.environ.get("VIDEO_RUNTIME", os.path.expanduser("~/VideoStudioRuntime")), "reel-cache", key + ".png")
    if not os.path.exists(out):
        tmp = out + f".{os.getpid()}.png"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf",
             f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}:(iw-{w})*{bias_x}:(ih-{h})*{bias}",
             "-frames:v", "1", tmp])
        os.replace(tmp, out)
    return out


def s_image(ctx, t, sc, L, S):
    z = sc.get("zoom", 1.08); key = (sc["src"], L.W, L.H)
    if key not in _IMG:
        _IMG[key] = cairo.ImageSurface.create_from_png(cover_png(resolve_path(sc["src"], S.root), int(L.W * z), int(L.H * z)))
    img = _IMG[key]; k = (1 / z) * lerp(1, z, clamp(t / max(S.dur, 0.1)))
    ctx.save(); ctx.translate(0, -L.oy)
    iw, ih = img.get_width() * k, img.get_height() * k
    ctx.translate((L.W - iw) / 2, (L.H - ih) / 2); ctx.scale(k, k); ctx.set_source_surface(img, 0, 0)
    ctx.get_source().set_filter(cairo.FILTER_GOOD); ctx.paint(); ctx.restore()
    ctx.save(); ctx.translate(0, -L.oy)
    g = cairo.LinearGradient(0, 0, 0, 220 + L.oy); g.add_color_stop_rgba(0, *BG, 0.75); g.add_color_stop_rgba(1, *BG, 0)
    ctx.set_source(g); ctx.rectangle(0, 0, L.W, 220 + L.oy); ctx.fill()
    y0 = L.oy + L.CH * (0.45 if L.land else 0.48)
    g = cairo.LinearGradient(0, y0, 0, L.H); g.add_color_stop_rgba(0, *BG, 0); g.add_color_stop_rgba(1, *BG, 0.92)
    ctx.set_source(g); ctx.rectangle(0, y0, L.W, L.H - y0); ctx.fill(); ctx.restore()
    if sc.get("title"):
        a = p(t, 0.3, 0.9); sz = fit(ctx, sc["title"], 64, L.CW - 2 * L.M, 2)
        para(ctx, sc["title"], L.M, (L.cap_y - 140) if L.land else (L.cap_y - 150), sz, L.CW - 2 * L.M, INK, a)
    if sc.get("caption"): caption(ctx, t, L, sc.get("label", ""), sc["caption"], 0.5)


def s_pending(ctx, t, sc, L, S):
    M = L.M; box(ctx, M, 300, L.CW - 2 * M, 420 if L.land else 560, 26, CARD, AMBER, 1, 3)
    text(ctx, "GOOGLE FLOW CLIP PENDING", M + 40, 380, 28, AMBER, spacing=3)
    para(ctx, sc.get("line") or sc.get("name", ""), M + 40, 460, 44, L.CW - 2 * M - 80, INK, 1)


# ---------- B-roll: real-life footage, recorded screens and stills, cut on spoken cues ----------
def shot_src(src, aspect, root=None):
    """screen:<name> (recorded HTML screen) · lib:<id> (assets/broll/lib) · a path to a video or image."""
    if src.startswith("screen:"):
        import screens
        return screens.record(src[7:], aspect), "screen"
    if src.startswith("lib:"):
        d = pj(BROLL, "lib", src[4:]); m = json.load(open(pj(d, "meta.json")))
        return pj(d, m["file"]), ("image" if m.get("kind") == "photo" else "video")
    p = resolve_path(src, root)
    return p, ("image" if p.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) else "video")


def broll_overlays(L, sc, path_caption, shots, b, tag):
    """Caption panel (whole scene) and one keyword chip per shot, as transparent PNGs."""
    out = {}
    if sc.get("caption"):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, L.W, L.H); c = cairo.Context(s)
        y0 = L.oy + L.cap_y - (130 if L.land else 150)
        g = cairo.LinearGradient(0, y0, 0, L.H); g.add_color_stop_rgba(0, *BG, 0); g.add_color_stop_rgba(0.45, *BG, 0.78); g.add_color_stop_rgba(1, *BG, 0.92)
        c.set_source(g); c.rectangle(0, y0, L.W, L.H - y0); c.fill()
        c.translate(0, L.oy); caption(c, 10, L, sc.get("label", ""), sc["caption"]); s.write_to_png(path_caption); out["caption"] = path_caption
    chips = []
    for k, sh in enumerate(shots):
        if not sh.get("label"): chips.append(None); continue
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, L.W, L.H); c = cairo.Context(s)
        txt = sh["label"].upper(); size = 24 if L.land else 26; w = tw(c, txt, size) + len(txt) * 2 + 44
        x, y = L.M, 120 if L.land else (1080 if L.aspect == "4x5" else 1370)  # vertical: just above the watermark
        box(c, x, y, w, 50, 25, (0.02, 0.04, 0.08), GREEN, 0.82, 2); text(c, txt, x + 22, y + 34, size, INK, 1, spacing=2)
        p_ = pj(b, f"{tag}_chip{k}.png"); s.write_to_png(p_); chips.append(p_)
    out["chips"] = chips
    return out


def render_broll(pr, i, L, force=False):
    d = pr.dirs[i]; sc = pr.scenes[i]; out = pj(d, f"video_{L.aspect}.mp4"); shots = sc.get("shots", [])
    if not shots: raise SystemExit(f"{pr.names[i]}: broll scene has no shots")
    srcs = [shot_src(s_["src"], L.aspect, pr.root) for s_ in shots]
    deps = [pj(d, "scene.json"), pj(d, "audio.mp3"), pj(pr.root, "build", "words", pr.names[i] + ".json"), os.path.abspath(__file__)] + [p_ for p_, _ in srcs]
    if not force and fresh(out, deps): return out
    S = Info(pr, i); n = int(round(pr.durs[i] * FPS)); m = len(shots)
    starts = [0.0] + [S.at(k, m) for k in range(1, m)]
    fb = [0]
    for t_ in starts[1:]: fb.append(min(n - 1, max(fb[-1] + 15, int(round(t_ * FPS)))))
    fb.append(n)
    b = pj(pr.root, "build", "broll"); os.makedirs(b, exist_ok=True); tag = f"{pr.names[i]}_{L.aspect}"; W, H = L.W, L.H; segs = []
    for k, (sh, (p_, kind)) in enumerate(zip(shots, srcs)):
        nk = fb[k + 1] - fb[k]
        if nk <= 0: continue
        seg = pj(b, f"{tag}_{k}.mp4")
        if kind == "image":
            z = sh.get("zoom", 1.12)
            vf = (f"scale={2*W}:{2*H}:force_original_aspect_ratio=increase:flags=lanczos,crop={2*W}:{2*H},"
                  f"zoompan=z='1+{z-1:.3f}*on/{nk}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},format=yuv420p")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", p_, "-vf", vf]
        else:
            loop = sh.get("loop", kind != "screen"); sp = sh.get("speed", 1.0)
            inp = (["-stream_loop", "-1"] if loop else []) + ["-ss", f"{sh.get('in', 0)}", "-i", p_]
            vf = (f"setpts=PTS/{sp}," if sp != 1 else "") + f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}:(iw-{W})/2:(ih-{H})*{sh.get('y', 0.5)},setsar=1,fps={FPS}"
            vf += ("" if loop else ",tpad=stop_mode=clone:stop_duration=120") + ",format=yuv420p"
            cmd = ["ffmpeg", "-y", "-loglevel", "error", *inp, "-an", "-vf", vf]
        run(cmd + ["-frames:v", str(nk), *enc(), seg]); segs.append(seg)
    lst = pj(b, f"{tag}.txt"); open(lst, "w").write("".join(f"file '{x}'\n" for x in segs))
    raw = pj(b, f"{tag}_raw.mp4"); run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", raw])
    ov = broll_overlays(L, sc, pj(b, f"{tag}_cap.png"), shots, b, tag)
    ins = ["-i", raw]; fc = f"[0:v]fade=t=in:st=0:d={FADE}:color=0x0B1220,fade=t=out:st={n / FPS - FADE:.3f}:d={FADE}:color=0x0B1220[v0]"; last = "[v0]"; j = 1
    if ov.get("caption"):
        ins += ["-loop", "1", "-framerate", str(FPS), "-i", ov["caption"]]
        fc += f";[{j}:v]format=rgba,fade=t=in:st=0.3:d=0.5:alpha=1[c{j}];{last}[c{j}]overlay=0:0:shortest=1[v{j}]"; last = f"[v{j}]"; j += 1
    for k, cp in enumerate(ov["chips"]):
        if not cp or k + 1 >= len(fb): continue
        a_, b_ = fb[k] / FPS, fb[k + 1] / FPS
        ins += ["-loop", "1", "-framerate", str(FPS), "-i", cp]
        fc += f";{last}[{j}:v]overlay=0:0:shortest=1:enable='between(t,{a_ + 0.15:.2f},{b_ - 0.1:.2f})'[v{j}]"; last = f"[v{j}]"; j += 1
    fc += f";{last}format=yuv420p[out]"
    run(["ffmpeg", "-y", "-loglevel", "error", *ins, "-filter_complex", fc, "-map", "[out]", "-frames:v", str(n), *enc(), out + ".part.mp4"])
    os.replace(out + ".part.mp4", out)
    return out


def broll_card(ctx, t, sc, L, S):
    M = L.M; box(ctx, M, 260, L.CW - 2 * M, 560, 26, CARD, LINE, 1, 2)
    text(ctx, "B-ROLL", M + 40, 330, 26, GREEN, spacing=3)
    for k, sh in enumerate(sc.get("shots", [])[:8]):
        text(ctx, f"{k+1}. {sh.get('label') or sh['src']}", M + 40, 400 + k * 52, 32, INK, 1, "normal")


DRAW = {"hook": s_hook, "title": s_title, "flow": s_flow, "cards": s_cards, "compare": s_compare, "stat": s_stat,
        "tip": s_tip, "cta": s_cta, "chapter": s_chapter, "bullets": s_bullets, "quote": s_quote, "diagram": s_diagram,
        "timeline": s_timeline, "image": s_image, "pending": s_pending, "broll": broll_card}
ITEM_KEYS = {"bullets": "items", "cards": "items", "diagram": "nodes", "timeline": "steps", "flow": "nodes", "broll": "shots"}


# ---------- project ----------
def dur(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]))


def dims(path):
    o = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                 "-of", "csv=p=0", path]).decode().strip().split(",")
    return int(o[0]), int(o[1])


def run(cmd): subprocess.run(cmd, check=True)
def strip_tags(s): return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", " ", s or "")).strip()
def slugify(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
def norm(s): return re.sub(r"[^a-z0-9' ]", "", (s or "").lower().replace("’", "'"))
def fq(x): return round(x * FPS) / FPS  # snap to whole frames so every variant stays in sync


def fresh(out, deps):
    if not os.path.exists(out): return False
    m = os.path.getmtime(out)
    return all(not os.path.exists(d) or os.path.getmtime(d) <= m for d in deps)


class Info:
    """What a drawer needs to know about its scene beyond scene.json."""
    def __init__(self, pr, i):
        self.dur = pr.durs[i]; self.len = pr.lens[i]; self.cues = pr.cue_times(i); self.root = pr.root
        self.chapters = pr.chapter_titles; self.chap = pr.chap_of[i]

    def at(self, k, n, fixed=None):
        if self.cues and k < len(self.cues) and self.cues[k] is not None: return self.cues[k]
        if fixed is not None: return fixed
        span = self.len if self.len > 0 else max(1.0, self.dur - LEAD - TAIL)
        return LEAD + 0.3 + 0.85 * span * k / max(n, 1)


class Project:
    def __init__(self, root):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.meta = json.load(open(pj(self.root, "project.json")))
        self.fmt = FORMATS[self.meta.setdefault("format", "pulse")]  # v2 projects have no format: they are Pulse reels
        self.meta.setdefault("aspects", self.fmt["aspects"]); self.meta.setdefault("primary", self.fmt["primary"])
        self.dirs = sorted(glob.glob(pj(self.root, "scenes", "scene-*")))
        self.names = [os.path.basename(d) for d in self.dirs]
        self.scenes = [json.load(open(pj(d, "scene.json"))) for d in self.dirs]
        self.lens, self.durs, self.words = [], [], []
        for d, sc in zip(self.dirs, self.scenes):
            if sc["type"] == "clip":
                v = pj(d, "video.mp4")
                L = dur(v) if os.path.exists(v) else 0.0
                self.lens.append(L); self.durs.append(fq(L) if L else fq(sc.get("min", 8.0)))
            else:
                a = pj(d, "audio.mp3"); L = dur(a) if os.path.exists(a) else 0.0
                self.lens.append(L); self.durs.append(fq(max(sc.get("min", DEFAULT_MIN.get(sc["type"], 6.0)), LEAD + L + TAIL)))
            wf = pj(self.root, "build", "words", os.path.basename(d) + ".json")
            self.words.append(json.load(open(wf))["words"] if os.path.exists(wf) else None)
        self.chapter_idx = [i for i, sc in enumerate(self.scenes) if sc["type"] == "chapter"]
        self.chapter_titles = [self.scenes[i].get("title", "") for i in self.chapter_idx]
        self.chap_of, c = [], -1
        for i in range(len(self.scenes)):
            if i in self.chapter_idx: c = self.chapter_idx.index(i)
            self.chap_of.append(c)

    def spoken(self, i):
        f = pj(self.dirs[i], "vo_script.md")
        return open(f).read().split("## Spoken words (for checking)")[-1].strip() if os.path.exists(f) else ""

    def find(self, ref):
        if isinstance(ref, int): return ref
        for i, n in enumerate(self.names):
            if n == ref or re.sub(r"^scene-\d+-", "", n) == slugify(ref): return i
        raise SystemExit(f"cut-down refers to unknown scene '{ref}'")

    def cue_times(self, i):
        sc = self.scenes[i]; key = ITEM_KEYS.get(sc["type"])
        if not key: return None
        items = sc.get(key, [])
        cues = list(sc.get("cues", [])) + [None] * len(items)
        cues = [c or (it.get("cue") if isinstance(it, dict) else None) for c, it in zip(cues, items)]
        if not any(cues) or not self.words[i]: return None
        w = [(norm(x[0]).strip(), x[1]) for x in self.words[i]]; out, j = [], 0
        for c in cues:
            if not c: out.append(None); continue
            toks = norm(c).split(); hit = None
            for k in range(j, len(w)):
                if toks and w[k][0].startswith(toks[0][:max(3, len(toks[0]) - 2)]):
                    if all(k + m < len(w) and w[k + m][0].startswith(toks[m][:3]) for m in range(1, min(len(toks), 3))):
                        hit = k; break
            if hit is None: out.append(None); continue
            out.append(LEAD + max(0.0, w[hit][1] - 0.1)); j = hit + 1
        return out


def settle(sc, S, n_items):
    base = {"hook": 3.6, "title": 3.5, "compare": 4.2, "stat": 2.2, "tip": 3.2, "cta": 2.8, "chapter": 2.0,
            "quote": 2.0, "pending": 1.0}.get(sc["type"], 1.6)
    last = max([S.at(k, n_items, 0.2 + k * 0.35 if sc["type"] == "flow" else None) for k in range(n_items)], default=0)
    return max(base, last + 1.0, 1.6)


def frame(pr, i, t, L, S):
    sc = pr.scenes[i]; d = pr.durs[i]; typ = "pending" if sc["type"] == "clip" else sc["type"]
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, L.W, L.H); ctx = cairo.Context(surf)
    setc(ctx, BG); ctx.paint()
    setc(ctx, (1, 1, 1), 0.025); ctx.set_line_width(1)
    for gx in range(0, L.W, 60): ctx.move_to(gx + .5, 0); ctx.line_to(gx + .5, L.H)
    for gy in range(0, L.H, 60): ctx.move_to(0, gy + .5); ctx.line_to(L.W, gy + .5)
    ctx.stroke()
    ctx.save(); ctx.translate(0, L.oy); DRAW[typ](ctx, t, sc, L, S); ctx.restore()
    ov = max(1 - clamp(t / FADE), 1 - clamp((d - t) / FADE))
    if ov > 0: setc(ctx, BG, ov); ctx.paint()
    hy = 84 if L.land else L.oy + 92
    if pr.meta.get("series"): text(ctx, pr.meta["series"], L.M, hy, 22, GREEN, 1, spacing=3)
    if L.land and S.chap >= 0 and typ not in ("chapter", "image"):
        text(ctx, f"{S.chap + 1:02d}  {pr.chapter_titles[S.chap].upper()}", L.W - L.M, hy, 20, MUTED, 1, align="right", spacing=2)
    surf.flush(); return surf


def enc(): return ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(FPS)]


def _render_scene(args):
    root, i, aspect, force = args; pr = Project(root); L = Lay(aspect); d = pr.dirs[i]; sc = pr.scenes[i]
    if sc["type"] == "clip" and os.path.exists(pj(d, "video.mp4")): return render_clip(pr, i, L, force)
    if sc["type"] == "broll": return render_broll(pr, i, L, force)
    out = pj(d, f"video_{aspect}.mp4")
    deps = [pj(d, "scene.json"), pj(d, "audio.mp3"), pj(pr.root, "build", "words", pr.names[i] + ".json"),
            os.path.abspath(__file__), pj(pr.root, "project.json")]
    if sc["type"] == "image": deps.append(resolve_path(sc.get("src", ""), pr.root))
    if not force and fresh(out, deps): return out
    S = Info(pr, i); n = int(round(pr.durs[i] * FPS)); tmp = out + ".part.mp4"
    key = ITEM_KEYS.get(sc["type"]); n_items = len(sc.get(key, [])) if key else 0
    s0 = math.ceil(settle(sc, S, n_items) * FPS); fe = n - int(FADE * FPS) - 2
    loop = int(P * FPS) if sc["type"] in ANIMATED else 1; cache = {}
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{L.W}x{L.H}",
                             "-r", str(FPS), "-i", "-", *enc(), tmp], stdin=subprocess.PIPE)
    for f in range(n):
        if sc["type"] != "image" and s0 <= f < fe:
            k = (f - s0) % loop
            if k not in cache: cache[k] = bytes(frame(pr, i, f / FPS, L, S).get_data())
            proc.stdin.write(cache[k])
        else:
            proc.stdin.write(bytes(frame(pr, i, f / FPS, L, S).get_data()))
    proc.stdin.close()
    if proc.wait() == 0: os.replace(tmp, out)
    return out


def lower_third_png(L, lt, path):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, L.W, L.H); c = cairo.Context(s)
    h, sub = lt.get("h", ""), lt.get("s", "")
    x, y, maxw = (L.M, 820, 960) if L.land else (L.M, L.oy + 1000, 900)
    size = fit(c, sub, 40, maxw - 70, 1)
    w = max(tw(c, h, 24) + len(h) * 3, tw(c, sub, size)) + 70
    box(c, x, y, w, 120, 16, (0.02, 0.04, 0.08), None, 0.82)
    setc(c, GREEN); c.rectangle(x, y + 18, 6, 84); c.fill()
    text(c, h, x + 32, y + 48, 24, GREEN, 1, spacing=3); text(c, sub, x + 32, y + 96, size, INK, 1)
    s.write_to_png(path)


def clip_vf(sw, sh, W, H):
    if sw / sh < (W / H) / 1.3:  # much taller than the canvas (9:16 clip on 16:9): blurred pillarbox
        return (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:4,eq=brightness=-0.12[bg];"
                f"[b]scale=-2:{H}[fg];[bg][fg]overlay=(W-w)/2:0")
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}:(iw-{W})/2:(ih-{H})*0.2"


def render_clip(pr, i, L, force=False):
    d = pr.dirs[i]; sc = pr.scenes[i]; src = pj(d, "video.mp4"); b = pj(pr.root, "build", "clips"); os.makedirs(b, exist_ok=True)
    wav = pj(b, f"clip_{i:02d}.wav")
    if force or not fresh(wav, [src]):
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-af", "aresample=48000,aformat=channel_layouts=stereo",
             "-c:a", "pcm_s16le", wav])
    out = pj(b, f"clip_{i:02d}_{L.aspect}.mp4")
    if not force and fresh(out, [src, pj(d, "scene.json"), os.path.abspath(__file__)]): return out
    n = int(round(pr.durs[i] * FPS)); sw, sh = dims(src); vf = clip_vf(sw, sh, L.W, L.H)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src]
    if sc.get("lower_third"):
        lt = pj(b, f"lt_{i:02d}_{L.aspect}.png"); lower_third_png(L, sc["lower_third"], lt)
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", lt, "-filter_complex",
                f"[0:v]{vf},fps={FPS},tpad=stop_mode=clone:stop_duration=1[v0];[1:v]format=rgba,fade=t=in:st=0.5:d=0.4:alpha=1,"
                f"fade=t=out:st={max(1.5, min(5.0, pr.durs[i] - 1.2)):.2f}:d=0.5:alpha=1[l];[v0][l]overlay=0:0,format=yuv420p[v]",
                "-map", "[v]"]
    else:
        cmd += ["-vf", f"{vf},fps={FPS},tpad=stop_mode=clone:stop_duration=1,format=yuv420p"]
    run(cmd + ["-an", "-frames:v", str(n), *enc(), out + ".part.mp4"]); os.replace(out + ".part.mp4", out)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", out, "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
         "-b:a", "192k", "-t", f"{pr.durs[i]:.3f}", pj(d, f"video_edit_{L.aspect}.mp4")])
    return out


def watermark(aspect):
    path = pj(PIPE, "assets", f"watermark_{aspect}.png"); badge = pj(PIPE, "assets", "watermark_badge.png")
    if os.path.exists(path) and os.path.getmtime(path) >= max(os.path.getmtime(badge), os.path.getmtime(os.path.abspath(__file__))): return path
    L = Lay(aspect); wm = BRAND["watermark"]; b = cairo.ImageSurface.create_from_png(badge)
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, L.W, L.H); c = cairo.Context(s)
    k = 1.3 if L.land else 1.0; bw, bh = b.get_width() * k, b.get_height() * k
    if L.land: x, y = L.W - 40 - bw, L.H - 56 - bh
    else: x, y = L.W - wm["margin_right"] - bw, L.oy + 1350 - wm["margin_bottom"] - bh
    c.translate(x, y); c.scale(k, k); c.set_source_surface(b, 0, 0); c.get_source().set_filter(cairo.FILTER_BEST)
    c.paint_with_alpha(wm.get("opacity", 0.95))
    s.write_to_png(path + ".tmp.png"); os.replace(path + ".tmp.png", path)
    return path


def variants(pr):
    slug = pr.meta["slug"]; n = len(pr.scenes); fin = pj(pr.root, "final")
    main = [i for i in range(n) if not pr.scenes[i].get("cutdown_only")]
    vs = [dict(name=a, aspect=a, idx=main, kind="full", out=pj(fin, f"{slug}_{a}.mp4")) for a in pr.meta["aspects"]]
    for c in pr.meta.get("cutdowns", []):
        idx = [pr.find(s) for s in c["scenes"]]
        for a in c.get("aspects") or pr.fmt["cutdown_aspects"] or pr.meta["aspects"]:
            vs.append(dict(name=f"{c['name']}_{a}", aspect=a, idx=idx, kind="cutdown", title=c.get("title", ""),
                           out=pj(fin, "clips", f"{slug}_{c['name']}_{a}.mp4")))
    return vs


def tc(sec, ms=True):
    h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    if ms: return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"
    return f"{int(h)}:{int(m):02d}:{int(s):02d}" if h else f"{int(m):02d}:{int(s):02d}"


EMPH_WORDS = {"YOUR", "ONE", "NOT", "THE", "ALL", "NOW", "YOU", "AND", "BUT", "ONLY", "MOST", "NEVER", "ANY", "EVERY"}


def caption_word(w):
    """Emphasis CAPS in the voice script (SEVEN, YOUR) read badly in captions; keep real acronyms (AI, ERP, CTOs)."""
    core = re.sub(r"[^A-Za-z]", "", w)
    if len(core) >= 2 and core.isupper() and (len(core) > 4 or core in EMPH_WORDS): return w.lower()
    return w


def srt_cues(pr, i):
    w = pr.words[i]
    if not w: return []
    if pr.scenes[i]["type"] == "clip": toks, off = [(x[0].strip(), x[1], x[2]) for x in w], 0.0
    else:
        script = [caption_word(x) for x in pr.spoken(i).split()]; off = LEAD
        script = [x[0].upper() + x[1:] if k == 0 or re.search(r"[.?!]$", script[k - 1]) else x for k, x in enumerate(script)]
        toks = ([(s, w[round(k * (len(w) - 1) / max(len(script) - 1, 1))][1], w[round(k * (len(w) - 1) / max(len(script) - 1, 1))][2])
                 for k, s in enumerate(script)] if len(script) > 1 and len(w) > 1 else [(x[0].strip(), x[1], x[2]) for x in w])
    out, cur = [], []
    for k, tk in enumerate(toks):
        cur.append(tk); txt = " ".join(x[0] for x in cur)
        if len(cur) >= 8 or len(txt) > 38 or (re.search(r"[.?!]$", tk[0]) and len(cur) >= 3) or k == len(toks) - 1:
            out.append((cur[0][1] + off, cur[-1][2] + off + 0.15, txt)); cur = []
    return out


def write_srt(pr, idx, starts, path):
    rows = []
    for k, i in enumerate(idx):
        end = starts[k] + pr.durs[i]
        for a, b, txt in srt_cues(pr, i): rows.append((starts[k] + a, min(starts[k] + b, end), txt))
    rows = [(a, max(b, a + 0.4), t) for a, b, t in rows]
    rows = [(a, min(b, rows[j + 1][0] - 0.02) if j + 1 < len(rows) else b, t) for j, (a, b, t) in enumerate(rows)]
    if not rows: return None
    open(path, "w").write("\n".join(f"{j+1}\n{tc(a)} --> {tc(b)}\n{t}\n" for j, (a, b, t) in enumerate(rows)))
    return path


def chapter_list(pr, idx, starts):
    ch = []
    for k, i in enumerate(idx):
        if pr.scenes[i]["type"] != "chapter": continue
        s = starts[k - 1] if k > 1 and pr.scenes[idx[k - 1]]["type"] == "clip" else starts[k]
        ch.append([s, pr.scenes[i].get("title", "")])
    if not ch: return []
    if ch[0][0] > 1.0: ch.insert(0, [0.0, "Intro"])
    else: ch[0][0] = 0.0
    return ch


def compose(pr, v, paths):
    L = Lay(v["aspect"]); b = pj(pr.root, "build", v["name"]); os.makedirs(b, exist_ok=True)
    os.makedirs(os.path.dirname(v["out"]), exist_ok=True)
    idx = v["idx"]; durs = [pr.durs[i] for i in idx]; starts = [sum(durs[:k]) for k in range(len(idx))]; total = sum(durs)
    with open(pj(b, "list.txt"), "w") as f:
        for i in idx: f.write(f"file '{paths[(i, v['aspect'])]}'\n")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", pj(b, "list.txt"), "-c", "copy", pj(b, "video.mp4")])
    ins, filt, labs, events = [], [], [], []
    for k, i in enumerate(idx):
        d, sc = pr.dirs[i], pr.scenes[i]
        if sc["type"] == "clip": a = pj(pr.root, "build", "clips", f"clip_{i:02d}.wav"); off = starts[k]; trim = f"atrim=0:{durs[k]:.3f},"
        else: a = pj(d, "audio.mp3"); off = starts[k] + LEAD; trim = ""
        if not os.path.exists(a) or pr.lens[i] == 0: continue
        j = len(labs); ins += ["-i", a]; ms = int(round(off * 1000))
        filt.append(f"[{j}:a]{trim}aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{j}]"); labs.append(f"[v{j}]")
        events.append({"scene": pr.names[i], "audio": a, "offset": round(off, 3)})
    voice = pj(b, "voice.wav")
    if labs:
        filt.append("".join(labs) + f"amix=inputs={len(labs)}:normalize=0,apad,atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[o]")
        run(["ffmpeg", "-y", "-loglevel", "error", *ins, "-filter_complex", ";".join(filt), "-map", "[o]", "-ar", "48000", "-c:a", "pcm_s16le", voice])
    else:
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={total:.3f}", voice])
    long_bed = pj(PIPE, "assets", "music_bed_long.m4a")
    bed = long_bed if total > 590 and os.path.exists(long_bed) else pj(PIPE, "assets", "music_bed.m4a")
    os.makedirs(pj(pr.root, "music"), exist_ok=True); ducked = pj(pr.root, "music", f"music_ducked_{v['name']}.wav")
    vol = pr.fmt.get("music_volume", 0.55)
    run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", bed, "-i", voice, "-filter_complex",
         f"[0:a]atrim=0:{total:.3f},asetpts=N/SR/TB,afade=t=in:d=2,afade=t=out:st={max(0, total - 2.5):.3f}:d=2.5,volume={vol},"
         "aformat=sample_rates=48000:channel_layouts=stereo[m];[m][1:a]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[o]",
         "-map", "[o]", "-c:a", "pcm_s16le", ducked])
    mixf = pj(b, "mix.m4a")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", voice, "-i", ducked, "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0[a]",
         "-map", "[a]", "-c:a", "aac", "-b:a", "192k", mixf])
    wm = watermark(v["aspect"]); out = v["out"]
    fc = "[0:v][1:v]overlay=0:0:shortest=1[w]"; last = "[w]"
    if not L.land:  # thin progress bar for feed formats
        bx, by, bw = L.bar
        fc += (f";color=c=0x38475f:s={bw}x6:r={FPS}:d={total:.3f}[tr];color=c=0x3ddc97:s={bw}x6:r={FPS}:d={total:.3f}[gb];"
               f"[tr][gb]overlay=x='-w+w*t/{total:.3f}':y=0[bar];[w][bar]overlay={bx}:{by}:shortest=1[pb]"); last = "[pb]"
    fc += f";{last}format=yuv420p[v]"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", pj(b, "video.mp4"), "-loop", "1", "-i", wm, "-i", mixf, "-filter_complex", fc,
         "-map", "[v]", "-map", "2:a", *enc(), "-c:a", "copy", "-shortest", "-movflags", "+faststart", out + ".part.mp4"])
    os.replace(out + ".part.mp4", out)
    wm_mov = pj(b, "watermark.mov")
    run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-i", wm, "-t", f"{total + 0.2:.3f}",
         "-c:v", "qtrle", "-pix_fmt", "argb", wm_mov])
    srt = write_srt(pr, idx, starts, out[:-4] + ".srt")
    chs = chapter_list(pr, idx, starts) if v["kind"] == "full" else []
    tl = {"name": v["name"], "kind": v["kind"], "slug": pr.meta["slug"], "title": pr.meta["title"], "fps": FPS,
          "width": L.W, "height": L.H, "total": round(total, 3), "final": out, "srt": srt, "chapters": chs,
          "scenes": [{"name": pr.names[i], "type": pr.scenes[i]["type"], "start": round(s, 3), "duration": round(du, 3),
                      "video": (pj(pr.dirs[i], f"video_edit_{v['aspect']}.mp4") if pr.scenes[i]["type"] == "clip"
                                and os.path.exists(pj(pr.dirs[i], f"video_edit_{v['aspect']}.mp4")) else paths[(i, v["aspect"])]),
                      "has_own_audio": pr.scenes[i]["type"] == "clip" and pr.lens[i] > 0,
                      "audio": (pj(pr.dirs[i], "audio.mp3") if pr.scenes[i]["type"] != "clip" and os.path.exists(pj(pr.dirs[i], "audio.mp3")) else None),
                      "audio_offset": round(s + (0 if pr.scenes[i]["type"] == "clip" else LEAD), 3)}
                     for i, s, du in zip(idx, starts, durs)],
          "music": ducked, "watermark": wm_mov}
    json.dump(tl, open(pj(b, "timeline.json"), "w"), indent=2)
    return {"variant": v["name"], "out": out, "duration": round(dur(out), 2), "srt": bool(srt), "chapters": len(chs)}


# ---------- thumbnails ----------
def thumb(pr, aspect, out):
    W, H = THUMB_SIZE[aspect]; th = pr.meta.get("thumbnail") or {}
    title = th.get("title") or pr.meta["title"]; accent = th.get("accent", "")
    photo = th.get(f"photo_{aspect}") or (th.get("photo") if aspect == "16x9" else None) or \
        BRAND["thumb_photo"]["16x9" if aspect == "16x9" else "vertical"]
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H); c = cairo.Context(s); setc(c, BG); c.paint()
    if aspect == "16x9": rx, ry, rw, rh = int(W * 0.42), 0, W - int(W * 0.42), H
    else: rx, ry, rw, rh = 0, int(H * 0.40), W, H - int(H * 0.40)
    img = cairo.ImageSurface.create_from_png(cover_png(resolve_path(photo, pr.root), rw, rh, 0.25, th.get("photo_x", 0.75 if aspect == "16x9" else 0.5)))
    c.set_source_surface(img, rx, ry); c.paint()
    if aspect == "16x9": g = cairo.LinearGradient(rx, 0, rx + rw * 0.35, 0)
    else: g = cairo.LinearGradient(0, ry, 0, ry + rh * 0.3)
    g.add_color_stop_rgba(0, *BG, 1); g.add_color_stop_rgba(1, *BG, 0); c.set_source(g); c.rectangle(rx, ry, rw, rh); c.fill()
    m = 56 if aspect == "16x9" else 72; maxw = W * (0.56 if aspect == "16x9" else 0.88)
    text(c, pr.meta.get("series", ""), m, 74 if aspect == "16x9" else 150, 24, GREEN, 1, spacing=4)
    size = fit(c, title, 104 if aspect == "16x9" else 120, maxw, 3, minsize=56)
    y = (170 if aspect == "16x9" else 290) + size * 0.8
    h = para(c, title.upper() if th.get("upper", True) else title, m, y, size, maxw, INK, 1, "bold", 1.05)
    if accent:
        asz = fit(c, accent, int(size * 0.62), maxw, 2, minsize=36)
        para(c, accent, m, y + h + asz * 0.4, asz, maxw, GREEN, 1, "bold", 1.08)
    text(c, BRAND["name"].upper(), m, H - (48 if aspect == "16x9" else 80), 24 if aspect == "16x9" else 30, MUTED, 1, spacing=3)
    tmp = out + ".png"; s.write_to_png(tmp)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-q:v", "2", out]); os.remove(tmp)
    return out


def thumbs(pr):
    os.makedirs(pj(pr.root, "final"), exist_ok=True); out = []
    for a in pr.fmt.get("thumbs", []):
        name = "thumbnail_16x9.jpg" if a == "16x9" else f"cover_{a}.jpg"
        out.append(thumb(pr, a, pj(pr.root, "final", name)))
    return out


# ---------- publishing sheet ----------
def publish(pr, results):
    fin = pj(pr.root, "final"); os.makedirs(pj(fin, "posts"), exist_ok=True)
    full = {r["variant"]: r for r in results}
    prim = pr.meta["primary"]; tlp = pj(pr.root, "build", prim, "timeline.json")
    chs = json.load(open(tlp))["chapters"] if os.path.exists(tlp) else []
    chtxt = "\n".join(f"{tc(s, False)} {t}" for s, t in chs)
    if chs: open(pj(fin, "chapters.txt"), "w").write(chtxt + "\n")
    rel = lambda p_: os.path.relpath(p_, pr.root)
    lines = [f"# Publish — {pr.meta['title']}", "", f"**Format:** {pr.fmt['label']} ({pr.fmt['length']}) — {pr.fmt['purpose']}", ""]
    clips = [v for v in variants(pr) if v["kind"] == "cutdown"]
    for pl in pr.fmt["platforms"]:
        vid = pl["video"]
        files = [v["out"] for v in clips if v["aspect"] == vid.split(":")[1]] if vid.startswith("clip:") else \
            [v["out"] for v in variants(pr) if v["kind"] == "full" and v["aspect"] == vid]
        lines += [f"## {pl['name']}", f"- **Use:** {pl['use']}"]
        for f_ in files:
            srt = f_[:-4] + ".srt"
            lines.append(f"- **Video:** `{rel(f_)}`" + (f" · captions `{rel(srt)}`" if os.path.exists(srt) else ""))
        if pl.get("thumb"):
            tn = "thumbnail_16x9.jpg" if pl["thumb"] == "16x9" else f"cover_{pl['thumb']}.jpg"
            lines.append(f"- **Thumbnail/cover:** `final/{tn}`")
        src = pj(pr.root, "posts", pl["post"] + ".md")
        if os.path.exists(src):
            body = open(src).read().replace("{{CHAPTERS}}", chtxt or "(no chapters)")
            open(pj(fin, "posts", pl["post"] + ".md"), "w").write(body)
            lines.append(f"- **Text:** `final/posts/{pl['post']}.md`")
        lines.append("")
    open(pj(fin, "PUBLISH.md"), "w").write("\n".join(lines))


# ---------- commands ----------
POSTS = {
    "linkedin": "# LinkedIn — {title}\n\n<!-- Hook in the first 2 lines (LinkedIn cuts at ~210 characters). 900-1,800 characters, short paragraphs, "
                "one question at the end. Max 3,000 characters. For clips: YouTube link goes in the first comment. -->\n\nTODO\n\n{tags}\n",
    "youtube": "# YouTube — {title}\n\n## Title\n<!-- max 100 characters; the first ~60 show in search -->\nTODO\n\n## Description\n"
               "<!-- first 2 lines show above the fold; keep {{{{CHAPTERS}}}} — it is replaced with the chapter timestamps at render -->\nTODO\n\n"
               "Chapters\n{{{{CHAPTERS}}}}\n\nMore: https://{url}\n\n## Tags\nTODO\n",
    "youtube_shorts": "# YouTube Shorts — {title}\n\n## Title\n<!-- under 100 characters; add #Shorts -->\nTODO\n\n## Description\nTODO\n",
    "instagram": "# Instagram — {title}\n\n<!-- first line is the hook (shown before 'more'); max 2,200 characters; 3-5 hashtags -->\n\nTODO\n\n{tags}\n",
    "x": "# X — {title}\n\n<!-- 280 characters for the first post (non-Premium); a thread of 3-6 posts for longer formats -->\n\nTODO\n",
}


def new(fmt, title, audience="tech"):
    if fmt not in FORMATS: raise SystemExit(f"unknown format {fmt}; choose from {', '.join(FORMATS)}")
    if audience not in AUDIENCES: raise SystemExit(f"unknown audience {audience}; choose from {', '.join(AUDIENCES)}")
    A = AUDIENCES[audience]
    F = FORMATS[fmt]; pd = pj(PIPE, "projects"); os.makedirs(pd, exist_ok=True)
    nums = [int(x[:3]) for x in os.listdir(pd) if re.match(r"^\d{3}-", x)]
    slug = slugify(title)[:48].strip("-"); root = pj(pd, f"{(max(nums) + 1 if nums else 1):03d}-{slug}"); os.makedirs(root)
    draft = {"format": fmt, "audience": audience, "title": title, "slug": slug, "intro": "standard", "outro": F["outro"],
             "thumbnail": {"title": title, "accent": ""}, "cutdowns": [], "scenes": []}
    json.dump(draft, open(pj(root, "draft.json"), "w"), indent=2)
    print(json.dumps({"project": root, "format": F["label"], "length": F["length"], "spoken_words": F["words"],
                      "chapters": F["chapters"], "cutdowns": F["cutdowns"], "face": F["face"], "purpose": F["purpose"],
                      "audience": A["label"], "who": A["who"], "goal": A["goal"], "voice": A["voice"], "writing": A["writing"],
                      "visual_mix": A["visual_mix"], "visuals": A["visuals"], "cta": A["cta"]}, indent=2))


def scaffold(root):
    root = os.path.abspath(os.path.expanduser(root)); d = json.load(open(pj(root, "draft.json")))
    fmtname = d.get("format", "pulse"); F = FORMATS[fmtname]; slug = d.get("slug") or slugify(d["title"])
    orient = F.get("flow_orientation", "9x16"); fl = BRAND["flow"]
    aud = d.get("audience", "tech"); A = AUDIENCES[aud]
    meta = {"title": d["title"], "slug": slug, "format": fmtname, "audience": aud, "series": d.get("series") or A.get("series") or F["series"], "voice": BRAND["voice"],
            "fps": FPS, "aspects": d.get("aspects", F["aspects"]), "primary": F["primary"], "cutdowns": d.get("cutdowns", []),
            "thumbnail": d.get("thumbnail", {}), "created": time.strftime("%Y-%m-%d")}
    json.dump(meta, open(pj(root, "project.json"), "w"), indent=2)
    sd = pj(root, "scenes"); os.makedirs(sd, exist_ok=True)
    for sub in ("music", "build", "final", "resolve", "posts"): os.makedirs(pj(root, sub), exist_ok=True)
    scenes = list(d["scenes"]); intro = d.get("intro", "standard"); outro = d.get("outro", F.get("outro", "none"))
    if intro != "none" and not any(s.get("type") == "clip" and s.get("name") == "intro" for s in scenes):
        src = intro
        if intro == "standard":
            src = BRAND["intro"].get(f"video_{orient}") or ""
            if not os.path.exists(os.path.expanduser(src)): src = BRAND["intro"]["video"]
        scenes.insert(0, {"name": "intro", "type": "clip", "source": src, "line": BRAND["intro"]["line"],
                          "flow_prompt": BRAND["intro"].get(f"flow_prompt_{orient}", BRAND["intro"]["flow_prompt"]),
                          "brief": "Prakash on camera (Google Flow @me), standard intro."})
    if outro == "standard" and not any(s.get("name") == "outro" for s in scenes):
        o = BRAND["outro"]
        scenes.append({"name": "outro", "type": "clip", "source": o.get(f"video_{orient}", ""), "line": o["line"],
                       "flow_prompt": o.get(f"flow_prompt_{orient}", ""), "brief": "Prakash on camera, standard outro."})
    width = 3 if len(scenes) > 100 else 2; made = []
    for i, sc in enumerate(scenes):
        name = f"scene-{i:0{width}d}-{slugify(sc.get('name', sc['type']))}"; f = pj(sd, name); os.makedirs(f, exist_ok=True)
        spec = {k: v for k, v in sc.items() if k not in ("vo", "brief", "flow_prompt")}
        json.dump(spec, open(pj(f, "scene.json"), "w"), indent=2)
        if sc["type"] == "clip":
            src = os.path.expanduser(sc.get("source") or ""); dst = pj(f, "video.mp4")
            if src and not os.path.exists(dst) and os.path.exists(src): shutil.copy(src, dst)
            angle = sc.get("angle", "02_medium_A-cam"); ref = f"{fl['refs_' + orient]}/{angle}.jpg"
            open(pj(f, "prompt.md"), "w").write(
                f"# {name} — Google Flow clip\n\n**Brief:** {sc.get('brief','')}\n\n**Line:** \"{sc.get('line','')}\"\n\n"
                f"**Flow project:** {fl['project']}\n**Settings:** {fl['settings_' + orient]}\n"
                f"**References:** {ref} (angle) + Me (avatar)\n\n## Prompt\n\n{sc.get('flow_prompt','')}\n\n"
                f"Save the 1080p download here as `video.mp4`. Audio: Prakash's own lip-synced voice from Flow — no ElevenLabs line.\n")
        else:
            open(pj(f, "vo_script.md"), "w").write(
                f"# {name} — voiceover\n\n- Model: `{BRAND['voice']['model_id']}`\n- Voice: `{BRAND['voice']['voice_name']}` (`{BRAND['voice']['voice_id']}`)\n\n"
                f"## ElevenLabs text (paste as-is)\n\n```\n{sc.get('vo','')}\n```\n\n## Spoken words (for checking)\n\n{strip_tags(sc.get('vo',''))}\n")
            fields = {k: v for k, v in spec.items() if k not in ("name", "type", "min")}
            open(pj(f, "prompt.md"), "w").write(
                f"# {name} — rendered scene (Claude / reel.py)\n\n**Template:** `{sc['type']}`\n\n**Brief:** {sc.get('brief','')}\n\n"
                f"## On-screen content\n\n```json\n{json.dumps(fields, indent=2)}\n```\n\nEdit `scene.json`, then `reel.py render` updates only this scene.\n")
        made.append(name)
    tags = " ".join(BRAND["linkedin_hashtags"])
    for post in F["posts"]:
        fp = pj(root, "posts", post + ".md")
        if not os.path.exists(fp): open(fp, "w").write(POSTS[post].format(title=d["title"], tags=tags, url=BRAND["url"]))
    words = sum(len(strip_tags(s.get("vo", "")).split()) for s in scenes)
    print(json.dumps({"project": root, "format": fmtname, "scenes": len(made), "spoken_words": words,
                      "target_words": F["words"], "pending_clips": [m for m, s in zip(made, scenes) if s["type"] == "clip"
                                                                     and not os.path.exists(pj(sd, m, "video.mp4"))]}, indent=2))


def render(root, only=None, force=False):
    pr = Project(root); vs = variants(pr)
    if only:
        keep = set(only.split(","))
        vs = [v for v in vs if v["name"] in keep or v["aspect"] in keep and v["kind"] == "full" or ("clips" in keep and v["kind"] == "cutdown")]
    missing = [pr.names[i] for i, l in enumerate(pr.lens) if l == 0]
    if missing: print("warning: no audio/video yet for", missing)
    jobs = sorted({(i, v["aspect"]) for v in vs for i in v["idx"]}, key=lambda x: (-pr.durs[x[0]], x))
    need = sorted({(sh["src"][7:], a) for i, a in jobs if pr.scenes[i]["type"] == "broll" for sh in pr.scenes[i].get("shots", [])
                   if sh["src"].startswith("screen:")})
    if need:  # record screens once, before the parallel scene renders
        import screens
        for nm, a in need: screens.record(nm, a)
    with Pool(max(2, (os.cpu_count() or 2) // 2)) as pool:
        res = pool.map(_render_scene, [(pr.root, i, a, force) for i, a in jobs], chunksize=1)
    paths = dict(zip(jobs, res)); pr = Project(root)
    results = [compose(pr, v, paths) for v in vs]
    thumbs(pr); publish(pr, results)
    print(json.dumps({"results": results, "publish": pj(pr.root, "final", "PUBLISH.md")}, indent=2))


def preview(root, aspect=None):
    pr = Project(root); aspect = aspect or pr.meta["primary"]; L = Lay(aspect); b = pj(pr.root, "build"); os.makedirs(b, exist_ok=True)
    wm = cairo.ImageSurface.create_from_png(watermark(aspect)); paths = []
    for i, sc in enumerate(pr.scenes):
        if sc["type"] == "clip" and os.path.exists(pj(pr.dirs[i], "video.mp4")): continue
        if sc["type"] == "broll" and pr.lens[i] > 0:
            v = render_broll(pr, i, L); fp = pj(pr.dirs[i], f"preview_{aspect}.png")
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{pr.durs[i] * 0.6:.2f}", "-i", v, "-frames:v", "1", fp])
            s = cairo.ImageSurface.create_from_png(fp); c = cairo.Context(s); c.set_source_surface(wm, 0, 0); c.paint(); s.write_to_png(fp); paths.append(fp); continue
        S = Info(pr, i); s = frame(pr, i, max(0.5, pr.durs[i] - 0.6), L, S)
        c = cairo.Context(s); c.set_source_surface(wm, 0, 0); c.paint()
        fp = pj(pr.dirs[i], f"preview_{aspect}.png"); s.write_to_png(fp); paths.append(fp)
    cols = min(len(paths), 4 if L.land else 5); rows = math.ceil(len(paths) / cols); sheet = pj(b, f"sheet_{aspect}.jpg")
    lst = pj(b, "prev.txt"); open(lst, "w").write("".join(f"file '{x}'\n" for x in paths))
    sw, sh = (480, 270) if L.land else (324, int(324 * L.H / L.W))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-vf",
         f"scale={sw}:{sh},tile={cols}x{rows}:padding=4", "-frames:v", "1", sheet])
    print(sheet)


def check(root):
    pr = Project(root); F = pr.fmt; rep = {"format": F["label"], "variants": {}, "problems": []}
    for v in variants(pr):
        out = v["out"]
        if not os.path.exists(out): rep["problems"].append(f"not rendered: {os.path.relpath(out, pr.root)}"); continue
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", out, "-af", "ebur128=peak=true", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
        loud = [ln.strip() for ln in r.splitlines() if ln.strip().startswith(("I:", "Peak:"))][-2:]
        d = round(dur(out), 1); rep["variants"][v["name"]] = {"duration": d, "loudness": loud, "srt": os.path.exists(out[:-4] + ".srt")}
        if v["kind"] == "full" and v["aspect"] == pr.meta["primary"] and not (F["target_sec"][0] <= d <= F["target_sec"][1]):
            rep["problems"].append(f"{v['name']}: {d}s is outside the {F['label']} target {F['target_sec']}")
        if v["kind"] == "cutdown" and d > 90: rep["problems"].append(f"{v['name']}: cut-down is {d}s (keep clips <= 90 s)")
    tlp = pj(pr.root, "build", pr.meta["primary"], "timeline.json")
    if F["chapters"][0] and os.path.exists(tlp):
        chs = json.load(open(tlp))["chapters"]; rep["chapters"] = len(chs)
        if len(chs) < 3: rep["problems"].append("YouTube needs at least 3 chapters")
        short = [t for (s, t), (s2, _) in zip(chs, chs[1:] + [[json.load(open(tlp))["total"], ""]]) if s2 - s < 10]
        if short: rep["problems"].append(f"chapters shorter than 10 s: {short}")
    for post in F["posts"]:
        fp = pj(pr.root, "posts", post + ".md")
        if not os.path.exists(fp) or "TODO" in open(fp).read(): rep["problems"].append(f"post not written: posts/{post}.md")
    for i, sc in enumerate(pr.scenes):
        if sc["type"] == "clip" and pr.lens[i] == 0: rep["problems"].append(f"Flow clip pending: {pr.names[i]}")
        elif sc["type"] != "clip" and pr.lens[i] == 0: rep["problems"].append(f"no voiceover: {pr.names[i]}")
        if pr.lens[i] and not pr.words[i]: rep["problems"].append(f"no word timings (run subs.py): {pr.names[i]}")
    main = [i for i in range(len(pr.scenes)) if not pr.scenes[i].get("cutdown_only")]; tot = sum(pr.durs[i] for i in main) or 1
    share = sum(pr.durs[i] for i in main if pr.scenes[i]["type"] in ("broll", "image")) / tot
    A = AUDIENCES[pr.meta.get("audience", "tech")]; target = A["visual_mix"].get("broll", 0)
    rep["audience"] = A["label"]; rep["footage_share"] = {"actual": round(share, 2), "target": target}
    if share < target - 0.15: rep["problems"].append(f"only {share:.0%} real-life footage; {A['label']} target is {target:.0%}")
    rep["ok"] = not rep["problems"]
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "new":
        args, aud = sys.argv[3:], "tech"
        if len(args) > 1 and args[0] in AUDIENCES: aud, args = args[0], args[1:]
        new(sys.argv[2], " ".join(args), aud)
    elif cmd == "scaffold": scaffold(sys.argv[2])
    elif cmd == "preview": preview(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "render":
        only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
        render(sys.argv[2], only, "--force" in sys.argv)
    elif cmd == "thumbs": print(thumbs(Project(sys.argv[2])))
    elif cmd == "check": check(sys.argv[2])
    else: raise SystemExit(__doc__)
