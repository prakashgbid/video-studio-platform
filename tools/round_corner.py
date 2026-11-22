#!/usr/bin/env python3
"""Round one convex beard/hair corner and fill the shaved sliver with surrounding skin (studio tool).

    python round_corner.py <in.jpg> <out.jpg> <cx> <cy> [--radius 34] [--kernel 81] [--keep-left-of X]

Finds the beard (dark) region near the corner, removes only the tip that a disk of `kernel` px cannot reach
(morphological opening) within `radius` px of (cx, cy), and inpaints that sliver from the neighbouring skin,
with a soft 2 px feather and faint grain. Use --keep-left-of to stop it touching shadows beyond the beard edge.
"""
import argparse, numpy as np, cv2
ap = argparse.ArgumentParser(); ap.add_argument("inp"); ap.add_argument("out"); ap.add_argument("cx", type=int); ap.add_argument("cy", type=int)
ap.add_argument("--radius", type=int, default=34); ap.add_argument("--kernel", type=int, default=81); ap.add_argument("--keep-left-of", type=int)
a = ap.parse_args(); im = cv2.imread(a.inp); out = im.copy()
x0, y0 = max(0, a.cx - 110), max(0, a.cy - 120); x1, y1 = min(im.shape[1], a.cx + 90), min(im.shape[0], a.cy + 120)
R = im[y0:y1, x0:x1].copy(); g = cv2.cvtColor(R, cv2.COLOR_BGR2GRAY); valid = g < 200
t, _ = cv2.threshold(g[valid].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
beard = cv2.morphologyEx(((g < t) & valid).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
n, lab = cv2.connectedComponents(beard); beard = (lab == (np.bincount(lab[lab > 0].ravel()).argmax())).astype(np.uint8)  # main beard only
opened = cv2.morphologyEx(beard, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (a.kernel, a.kernel)))
yy, xx = np.mgrid[0:R.shape[0], 0:R.shape[1]]
near = ((xx - (a.cx - x0)) ** 2 + (yy - (a.cy - y0)) ** 2) < a.radius ** 2
removed = beard.astype(bool) & ~opened.astype(bool) & near
if a.keep_left_of: removed &= xx < (a.keep_left_of - x0)
m = cv2.dilate(removed.astype(np.uint8), np.ones((3, 3), np.uint8))
fill = cv2.inpaint(R, m * 255, 6, cv2.INPAINT_TELEA)
al = cv2.GaussianBlur(m.astype(np.float32), (0, 0), 2.2)[..., None]
noise = cv2.GaussianBlur(np.random.default_rng(3).standard_normal(g.shape).astype(np.float32), (0, 0), 0.8) * 3
out[y0:y1, x0:x1] = np.clip(R * (1 - al) + fill * al + noise[..., None] * al, 0, 255).astype(np.uint8)
cv2.imwrite(a.out, out, [cv2.IMWRITE_JPEG_QUALITY, 96]); print(f"rounded corner at ({a.cx},{a.cy}): {int(removed.sum())} px shaved")
