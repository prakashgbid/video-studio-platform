"""Green/blue-screen keyer that never punches holes in the subject.

usage: python3 keyer.py in.mp4 out.mov [--screen green|blue] [--feather 2]
Output: ProRes 4444 with alpha, same size and frame rate as the input.

How it works, per frame:
  1. Screen mask in HSV (hue band around the sampled screen colour, enough saturation).
  2. Only screen regions CONNECTED TO THE FRAME BORDER count as background. Any "screen-like"
     pixels inside the subject (spill on grey hair, cream blouse, glasses) are enclosed by the
     subject and are kept as foreground, so the subject has no holes.
  3. Matte is cleaned (open/close), lightly feathered at the edge only, and green spill is
     removed from the foreground colour (clamp G to max(R,B) near edges and on light tones).
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import sys, argparse, subprocess, json
import numpy as np, cv2

KEYER_VERSION = "1.1"   # bump only when default output changes; new options with old-behaviour defaults do not bump it

def probe(path):
    o = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,r_frame_rate", "-of", "json", path]))["streams"][0]
    n, d = map(int, o["r_frame_rate"].split("/")); return o["width"], o["height"], n / d

def sample_screen(frame):
    h, w = frame.shape[:2]; m = max(8, w // 60)
    patches = [frame[0:m, 0:m], frame[0:m, w - m:w], frame[h // 3:h // 3 + m, 0:m], frame[h // 3:h // 3 + m, w - m:w]]
    hsv = cv2.cvtColor(np.concatenate([p.reshape(-1, 3) for p in patches])[None], cv2.COLOR_BGR2HSV)[0]
    ok = hsv[(hsv[:, 1] > 60) & (hsv[:, 2] > 40)]          # ignore black bars / dark corners
    return float(np.median((ok if len(ok) else hsv)[:, 0]))

def screen_stats(frame, hue, tol=14):
    """Median saturation and value of the screen itself (pixels near the screen hue in the top third)."""
    h = frame.shape[0]; hsv = cv2.cvtColor(frame[: h // 3], cv2.COLOR_BGR2HSV).reshape(-1, 3)
    sel = hsv[(np.abs(hsv[:, 0].astype(int) - hue) <= tol) & (hsv[:, 1] > 60)]
    return (float(np.median(sel[:, 1])), float(np.median(sel[:, 2]))) if len(sel) > 100 else (120.0, 120.0)

def letterbox(frame, thr=24):
    """Rows of near-black bars at the top/bottom edge (some generators add them); returned as (top, bottom) counts."""
    lum = frame.mean(axis=(1, 2)); h = len(lum); t = 0; b = 0
    while t < h // 8 and lum[t] < thr: t += 1
    while b < h // 8 and lum[h - 1 - b] < thr: b += 1
    return t, b

def matte(frame, hue, hue_tol=14, smin=60, vmin=40, feather=2, bars=(0, 0), bar_margin=2):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hh = hsv[..., 0].astype(np.int16)
    screen = ((np.abs(hh - hue) <= hue_tol) & (hsv[..., 1] >= smin) & (hsv[..., 2] >= vmin)).astype(np.uint8)
    if bars[0]: screen[:bars[0] + bar_margin] = 1          # letterbox bars count as background, never as subject
    if bars[1]: screen[-(bars[1] + bar_margin):] = 1
    screen = cv2.morphologyEx(screen, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab = cv2.connectedComponents(screen, connectivity=4)
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    border = border[border != 0]
    bg = np.isin(lab, border).astype(np.uint8)
    fg = 1 - bg
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    # keep the largest foreground blobs only (drops specks), fill any remaining enclosed gaps
    n, lab, st, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    keep = np.zeros_like(fg)
    if n > 1:
        big = max(st[1:, cv2.CC_STAT_AREA])
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] >= 0.02 * big: keep[lab == i] = 1
    a = keep.astype(np.float32)
    if feather > 0:
        a = cv2.GaussianBlur(a, (0, 0), feather)
        a = np.where(cv2.erode(keep, np.ones((3, 3), np.uint8)) == 1, 1.0, a)  # solid inside, soft only at the rim
    return a

def despill(frame, alpha, screen="green"):
    f = frame.astype(np.float32); b, g, r = f[..., 0], f[..., 1], f[..., 2]
    if screen == "green":
        lim = np.maximum(r, b); f[..., 1] = np.where(g > lim, lim + (g - lim) * 0.15, g)
    else:
        lim = np.maximum(r, g); f[..., 0] = np.where(b > lim, lim + (b - lim) * 0.15, b)
    return np.clip(f, 0, 255).astype(np.uint8)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--screen", default="green"); ap.add_argument("--feather", type=float, default=2)
    ap.add_argument("--hue-tol", type=int, default=14); ap.add_argument("--bar-margin", type=int, default=2)
    ap.add_argument("--adaptive", action="store_true", help="screen = only colours close to the measured screen saturation/brightness (keeps grey or dark green props)")
    ap.add_argument("--unletterbox", action="store_true", help="crop black bars off and rescale to full frame before keying")
    a = ap.parse_args(); w, h, fps = probe(a.src)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", a.src, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}", "-r", f"{fps}",
        "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", a.dst], stdin=subprocess.PIPE)
    hue = None; size = w * h * 3; k = 0
    while True:
        buf = dec.stdout.read(size)
        if len(buf) < size: break
        fr = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        if hue is None:
            hue = sample_screen(fr); bars = letterbox(fr); smin, vmin = 60, 40
            if a.adaptive: S, V = screen_stats(fr, hue); smin, vmin = int(S * 0.62), int(V * 0.55)
        if a.unletterbox and (bars[0] or bars[1]):
            t, b = bars[0] + 2, bars[1] + 2
            fr = cv2.resize(np.ascontiguousarray(fr[t:h - b]), (w, h), interpolation=cv2.INTER_LANCZOS4); use_bars = (0, 0)
        else: use_bars = bars
        al = matte(fr, hue, a.hue_tol, smin=smin, vmin=vmin, feather=a.feather, bars=use_bars, bar_margin=a.bar_margin); col = despill(fr, al, a.screen)
        enc.stdin.write(np.dstack([col, (al * 255).astype(np.uint8)]).tobytes()); k += 1
    enc.stdin.close(); enc.wait(); dec.wait(); print(f"keyed {k} frames, screen hue {hue}, letterbox rows {bars}, thresholds S>={smin} V>={vmin}")

if __name__ == "__main__": main()
