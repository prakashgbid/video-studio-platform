"""Animated cinematic title cards (movie-trailer style), rendered from a JSON spec. Deterministic.

usage: python3 titles.py spec.json out.mp4
spec = {
  "dur": 2.2, "fps": 30, "size": [1920,1080],
  "bg": "black" | "<image path>", "bg_zoom": [1.0, 1.05], "bg_reveal": 0.5,   # seconds to rise out of black
  "flash": 0.12,                      # white flash on the cut (decay seconds), 0 = none
  "grain": 0.025, "fade_out": 0.25,
  "elements": [ {"text": "AGENTIC", "font": "Futura.ttc", "index": 0, "size": 210,
                 "track": [0.35, 0.5], "x": "center" | px, "y": 470, "fill": "silver" | "#hex",
                 "fade_in": [0.0, 0.25], "glow": 0.35, "sweep": [0.3, 1.3]},
                {"rule": [x, y, w, h], "color": "#E0A84B", "fade_in": [0.5, 0.9], "grow": true} ]
}
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import sys, json, subprocess, numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont

TITLES_VERSION = "1.0"
FONTS = "/System/Library/Fonts/Supplemental/"

def hex_rgb(h): h = h.lstrip("#"); return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255

def text_mask(text, font, track_em, W, H, x, y):
    """Alpha mask of tracked text; returns mask (H,W) float and bbox."""
    adv = [font.getlength(c) for c in text]; gap = font.size * track_em
    total = sum(adv) + gap * (len(text) - 1)
    x0 = (W - total) / 2 if x == "center" else float(x)
    img = Image.new("L", (W, H), 0); d = ImageDraw.Draw(img); cx = x0
    asc, desc = font.getmetrics(); top = y - (asc + desc) / 2
    for c, a in zip(text, adv):
        d.text((cx, top), c, font=font, fill=255); cx += a + gap
    m = np.asarray(img, np.float32) / 255
    return m, (int(x0), int(top), int(total), asc + desc)

def ramp(t, a, b):
    return 0.0 if t <= a else 1.0 if t >= b else (t - a) / (b - a)

def render(spec, out):
    W, H = spec.get("size", [1920, 1080]); fps = spec.get("fps", 30); dur = spec["dur"]; n = int(round(dur * fps))
    bg = None
    if spec.get("bg", "black") != "black":
        im = cv2.imread(spec["bg"], cv2.IMREAD_COLOR); im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255
        s = max(W / im.shape[1], H / im.shape[0]); bg = cv2.resize(im, (int(im.shape[1] * s + 0.5), int(im.shape[0] * s + 0.5)), interpolation=cv2.INTER_AREA)
    rng = np.random.default_rng(7)
    fonts = {}
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    for f in range(n):
        t = f / fps
        if bg is not None:
            z0, z1 = spec.get("bg_zoom", [1.0, 1.0]); z = z0 + (z1 - z0) * (t / dur)
            h2, w2 = bg.shape[:2]; cw, ch = W / z, H / z; cx, cy = w2 / 2, h2 / 2
            M = np.float32([[z * W / W, 0, -(cx - cw / 2) * z], [0, z, -(cy - ch / 2) * z]])
            frame = cv2.warpAffine(bg, M, (W, H), flags=cv2.INTER_LINEAR)
            frame *= ramp(t, 0, spec.get("bg_reveal", 0.0)) if spec.get("bg_reveal") else 1.0
        else:
            frame = np.zeros((H, W, 3), np.float32)
        for e in spec["elements"]:
            if "rule" in e:
                a = ramp(t, *e.get("fade_in", [0, 0.01])); x, y, w, h = e["rule"]
                if e.get("grow"): cxr = x + w / 2; w = w * a; x = cxr - w / 2
                if a > 0 and w > 1:
                    col = hex_rgb(e.get("color", "#E0A84B")); sl = frame[int(y):int(y + h), int(x):int(x + w)]
                    sl[:] = sl * (1 - a) + col * a
                continue
            key = (e["font"], e.get("index", 0), e["size"])
            if key not in fonts: fonts[key] = ImageFont.truetype(FONTS + e["font"], e["size"], index=e.get("index", 0))
            tr = e.get("track", [0.1, 0.1]); track = tr[0] + (tr[1] - tr[0]) * (t / dur)
            m, (bx, by, bw, bh) = text_mask(e["text"], fonts[key], track, W, H, e.get("x", "center"), e["y"])
            a = ramp(t, *e.get("fade_in", [0, 0.01]))
            if spec.get("fade_out"): a *= 1 - ramp(t, dur - spec["fade_out"], dur)
            if a <= 0: continue
            if e.get("fill", "silver") == "silver":       # brushed-silver vertical gradient across the letters
                g = np.clip((yy * H - by) / max(bh, 1), 0, 1); v = 0.72 + 0.28 * np.cos((g - 0.35) * np.pi)
                fill = np.broadcast_to(v[:, :, None] * np.array([0.93, 0.95, 1.0], np.float32), (H, W, 3))
            else:
                fill = np.broadcast_to(hex_rgb(e["fill"]), (H, W, 3))
            fill = np.array(fill, np.float32)
            if e.get("sweep"):
                s0, s1 = e["sweep"]; p = ramp(t, s0, s1)
                if 0 < p < 1:
                    xs = np.arange(W, dtype=np.float32)[None, :]; ys = np.arange(H, dtype=np.float32)[:, None]
                    centre = bx - 200 + p * (bw + 400); band = np.exp(-(((xs - centre) + (ys - by) * 0.35) / 60.0) ** 2)
                    fill = np.clip(fill + band[:, :, None] * 0.6, 0, 1.3)
            if e.get("glow", 0):
                gl = cv2.GaussianBlur(m, (0, 0), e["size"] * 0.12) * e["glow"] * a
                frame = frame + gl[:, :, None] * np.array([0.75, 0.85, 1.0], np.float32)
            mm = (m * a)[:, :, None]; frame = frame * (1 - mm) + fill * mm
        if spec.get("flash"):
            fl = 0.85 * np.exp(-t / spec["flash"]); frame = frame + fl
        if spec.get("fade_out") and bg is not None: frame *= 1 - ramp(t, dur - spec["fade_out"], dur)
        if spec.get("grain"): frame = frame + rng.normal(0, spec["grain"], (H // 2, W // 2, 1)).astype(np.float32).repeat(2, 0).repeat(2, 1)
        enc.stdin.write((np.clip(frame, 0, 1) * 255).astype(np.uint8).tobytes())
    enc.stdin.close(); enc.wait(); print(f"title {n} frames -> {out}")

if __name__ == "__main__":
    render(json.load(open(sys.argv[1])), sys.argv[2])
