#!/usr/bin/env python3
"""Build the reusable brand kit in ~/Movies/video-pipeline/assets (run once, or again after changing brand.json).
Creates: headshot.jpg, watermark.png (full-frame 1080x1350, transparent, badge lower-right),
watermark_badge.png (badge only), music_bed.m4a (10 min) and music_bed_long.m4a (45 min) ambient beds.
Per-aspect watermarks (watermark_16x9.png etc.) are rebuilt automatically by reel.py from the badge."""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import cairo, json, math, os, subprocess, sys

ROOT = os.path.expanduser("~/Movies/video-pipeline")
A = os.path.join(ROOT, "assets")
B = json.load(open(os.path.join(ROOT, "engine", "brand.json")))
W, H = B["width"], B["height"]


def run(c): subprocess.run(c, check=True)


def hex2rgb(h): h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def badge_surface():
    """Signature badge: round headshot + name / professional title / website."""
    wm = B["watermark"]; sc = wm.get("scale", 1.0)
    name, title, url = B["name"], B.get("title", ""), B["url"]
    font = B["font"]
    lines = [(name, 27 * sc, 1, (0.97, 0.98, 1.0)), (title, 16.5 * sc, 0, (0.80, 0.85, 0.93)), (url, 19 * sc, 0, hex2rgb(B["colors"]["accent"]))]
    lines = [l for l in lines if l[0]]
    bh = int(118 * sc); pad = int(12 * sc); av = bh - 2 * pad
    tmp = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)); tw = 0
    for t, size, bold, _ in lines:
        tmp.select_font_face(font, 0, bold); tmp.set_font_size(size); tw = max(tw, tmp.text_extents(t).x_advance)
    bw = int(pad + av + pad * 1.4 + tw + pad * 2.2)
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, bw, bh); c = cairo.Context(s)
    r = bh / 2
    c.new_sub_path(); c.arc(bw - r, r, r, -math.pi / 2, math.pi / 2); c.arc(r, r, r, math.pi / 2, 1.5 * math.pi); c.close_path()
    c.set_source_rgba(0.02, 0.04, 0.08, wm.get("bg_alpha", 0.66)); c.fill_preserve()
    g = hex2rgb(B["colors"]["accent"]); c.set_source_rgba(*g, 0.55); c.set_line_width(1.5 * sc); c.stroke()
    img = cairo.ImageSurface.create_from_png(os.path.join(A, "headshot.png"))
    cx, cy = pad + av / 2, bh / 2
    c.save(); c.arc(cx, cy, av / 2, 0, 2 * math.pi); c.clip()
    k = av / img.get_width(); c.translate(cx - av / 2, cy - av / 2); c.scale(k, k)
    c.set_source_surface(img, 0, 0); c.get_source().set_filter(cairo.FILTER_BEST); c.paint(); c.restore()
    c.set_source_rgba(*g, 1); c.set_line_width(3 * sc); c.arc(cx, cy, av / 2 - 1, 0, 2 * math.pi); c.stroke()
    tx = pad + av + pad * 1.4
    ys = {3: [bh / 2 - 20 * sc, bh / 2 + 6 * sc, bh / 2 + 32 * sc], 2: [bh / 2 - 4 * sc, bh / 2 + 24 * sc]}[len(lines)]
    for (t, size, bold, col), y in zip(lines, ys):
        c.select_font_face(font, 0, bold); c.set_font_size(size); c.set_source_rgba(*col, 1); c.move_to(tx, y); c.show_text(t)
    return s


def main():
    os.makedirs(A, exist_ok=True)
    src = os.path.expanduser(B["headshot_source"]); crop = B["headshot_crop"]
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", f"crop={crop},scale=512:512:flags=lanczos,unsharp=5:5:0.6:5:5:0.0", os.path.join(A, "headshot.jpg")])
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", os.path.join(A, "headshot.jpg"), os.path.join(A, "headshot.png")])
    badge = badge_surface(); badge.write_to_png(os.path.join(A, "watermark_badge.png"))
    wm = B["watermark"]
    full = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H); c = cairo.Context(full)
    x = W - wm["margin_right"] - badge.get_width(); y = H - wm["margin_bottom"] - badge.get_height()
    c.set_source_surface(badge, x, y); c.paint_with_alpha(wm.get("opacity", 0.92))
    full.write_to_png(os.path.join(A, "watermark.png"))
    import glob
    for f in glob.glob(os.path.join(A, "watermark_*x*.png")): os.remove(f)  # per-aspect copies are rebuilt by reel.py
    for bed, d in ((os.path.join(A, "music_bed.m4a"), 600), (os.path.join(A, "music_bed_long.m4a"), 2700)):
        if os.path.exists(bed) and "--music" not in sys.argv: continue
        pad = ("aevalsrc='0.10*sin(2*PI*110*t)+0.07*sin(2*PI*164.81*t)*(0.6+0.4*sin(2*PI*0.13*t))"
               "+0.06*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*0.09*t+1))+0.04*sin(2*PI*329.63*t)*(0.5+0.5*sin(2*PI*0.07*t+2))"
               f"':s=48000:d={d}")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", pad, "-af",
             "lowpass=f=900,aecho=0.8:0.6:300|600:0.3|0.2,aformat=channel_layouts=stereo", "-c:a", "aac", "-b:a", "192k", bed])
    print(json.dumps({"badge": [badge.get_width(), badge.get_height()], "watermark_xy": [x, y]}))


if __name__ == "__main__":
    main()
