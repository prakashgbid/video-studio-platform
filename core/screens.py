#!/usr/bin/env python3
"""Record the HTML screens in assets/broll/screens/src/ as frame-exact video clips for B-roll.

Each page defines window.DURATION and window.seek(t). For every frame we call seek(t) and take a JPEG screenshot
(headless Chrome via Playwright, separate temporary profile — never the user's own browser profile), then encode.
Clips: assets/broll/screens/<name>_<aspect>.mp4   (16x9 = desktop layout, 4x5 / 9x16 = phone layout)

usage:  screens.py list
        screens.py record <name|all> [aspect|all] [--force]
Reference a clip in a broll scene as  {"src": "screen:<name>"}  — reel.py records it on demand.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import json, os, subprocess, sys

ENGINE = os.path.dirname(os.path.abspath(__file__)); PIPE = os.environ.get("VIDEO_PIPELINE", os.path.expanduser("~/Movies/video-pipeline"))
SRC = os.path.join(PIPE, "assets", "broll", "screens", "src"); OUT = os.path.join(PIPE, "assets", "broll", "screens")
FPS = 30
VIEW = {"16x9": (1440, 810, 4 / 3), "4x5": (540, 675, 2), "9x16": (540, 960, 2)}  # css viewport + device scale → output size


def names(): return sorted(f[:-5] for f in os.listdir(SRC) if f.endswith(".html"))


def out_path(name, aspect): return os.path.join(OUT, f"{name}_{aspect}.mp4")


def fresh(name, aspect):
    o = out_path(name, aspect)
    if not os.path.exists(o): return False
    deps = [os.path.join(SRC, name + ".html"), os.path.join(SRC, "kit.js"), os.path.join(SRC, "kit.css"), os.path.abspath(__file__)]
    imgs = os.path.join(SRC, "img"); deps += [os.path.join(imgs, f) for f in os.listdir(imgs)] if os.path.isdir(imgs) else []
    return all(os.path.getmtime(d) <= os.path.getmtime(o) for d in deps)


def record(name, aspect, force=False, browser=None):
    if not force and fresh(name, aspect): return out_path(name, aspect)
    from playwright.sync_api import sync_playwright
    w, h, dpr = VIEW[aspect]; W, H = round(w * dpr), round(h * dpr); out = out_path(name, aspect); tmp = out + ".part.mp4"
    own = browser is None
    if own: pw = sync_playwright().start(); browser = pw.chromium.launch(channel="chrome", headless=True)
    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr, is_mobile=w < 760, has_touch=w < 760)
    page = ctx.new_page(); page.goto("file://" + os.path.join(SRC, name + ".html")); page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready"); dur = page.evaluate("window.DURATION || 10"); n = int(round(dur * FPS))
    enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                            "-vf", f"scale={W}:{H}:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-crf", "17", "-preset", "veryfast",
                            "-r", str(FPS), tmp], stdin=subprocess.PIPE)
    for f in range(n):
        page.evaluate(f"window.seek({f / FPS})")
        enc.stdin.write(page.screenshot(type="jpeg", quality=92))
    enc.stdin.close(); ok = enc.wait() == 0; ctx.close()
    if own: browser.close(); pw.stop()
    if ok: os.replace(tmp, out)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "list": print("\n".join(names())); sys.exit()
    which = names() if sys.argv[2] == "all" else [sys.argv[2]]
    aspects = list(VIEW) if len(sys.argv) < 4 or sys.argv[3] in ("all", "--force") else [sys.argv[3]]
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        for nm in which:
            for a in aspects: print(record(nm, a, "--force" in sys.argv, b), flush=True)
        b.close()
