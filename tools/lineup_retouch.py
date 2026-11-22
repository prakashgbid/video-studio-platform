#!/usr/bin/env python3
"""Beard / hairline line-up retouch (studio tool).

    python lineup_retouch.py <input.jpg> <output.jpg> <spec.json>

spec.json: {"points": [[x,y], ...],          # the line-up edge as a polyline (straight segments)
            "hair": [x,y],                    # any point on the hair/beard side
            "radius": 9,                      # rounded corners, px (or "tangent": visible curve length per edge, px)
            "bands": [12, 6, 9],              # per segment: how far either side of the edge is rebuilt
            "soft": 3.5,                      # edge softness, px (natural beard edge, not a drawn line)
            "tips": 1.6,                      # hair-tip irregularity along the edge, px
            "seed": 7}
The edge is rebuilt with skin sampled from just outside the band on the skin side and beard/hair sampled from just
inside on the hair side, so texture stays real; only the outline changes. Works on a crop for speed.
"""
import sys, json, math
import numpy as np, cv2

def rounded_path(pts, r, tangent=None):
    pts = [np.array(p, np.float64) for p in pts]; out = [pts[0]]
    for i in range(1, len(pts) - 1):
        P, V, N = pts[i - 1], pts[i], pts[i + 1]
        u1 = (V - P) / np.linalg.norm(V - P); u2 = (N - V) / np.linalg.norm(N - V)
        cosang = np.clip(np.dot(-u1, u2), -1, 1); theta = math.acos(cosang)          # interior angle
        if theta < 1e-3 or abs(theta - math.pi) < 1e-3: out.append(V); continue
        if tangent:                                   # round by visible curve length along each edge
            t = tangent; r = t * math.tan(theta / 2)
        else: t = r / math.tan(theta / 2)
        T1 = V - u1 * t; T2 = V + u2 * t
        bis = (-u1 + u2); bis /= np.linalg.norm(bis); C = V + bis * (r / math.sin(theta / 2))
        a1 = math.atan2(*(T1 - C)[::-1]); a2 = math.atan2(*(T2 - C)[::-1])
        da = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        for k in range(13): a = a1 + da * k / 12; out.append(C + r * np.array([math.cos(a), math.sin(a)]))
    out.append(pts[-1]); return np.array(out)

def main():
    src_p, out_p, spec_p = sys.argv[1:4]; S = json.load(open(spec_p))
    img = cv2.imread(src_p).astype(np.float32); H, W = img.shape[:2]
    pts = np.array(S["points"], np.float64); pad = 60
    x0, y0 = int(max(0, pts[:, 0].min() - pad)), int(max(0, pts[:, 1].min() - pad))
    x1, y1 = int(min(W, pts[:, 0].max() + pad)), int(min(H, pts[:, 1].max() + pad))
    crop = img[y0:y1, x0:x1]; h, w = crop.shape[:2]
    path = rounded_path(S["points"], S.get("radius", 9), S.get("tangent")) - [x0, y0]
    hp = np.array(S["hair"], np.float64) - [x0, y0]
    # hair region: polygon closed on the hair side, far outside the crop; supersampled for a clean edge
    a, b = path[0], path[-1]; d0 = path[1] - a; d1 = b - path[-2]
    # extend both ends outward, then close around the hair point side
    big = 4 * max(h, w)
    ext_a = a - d0 / np.linalg.norm(d0) * big; ext_b = b + d1 / np.linalg.norm(d1) * big
    cen = path.mean(0); push = (hp - cen); push = push / (np.linalg.norm(push) + 1e-9) * big
    poly = np.vstack([ext_a, path, ext_b, ext_b + push, ext_a + push])
    SS = 4; mask = np.zeros((h * SS, w * SS), np.uint8)
    cv2.fillPoly(mask, [np.round(poly * SS).astype(np.int32)], 255, lineType=cv2.LINE_AA)
    hair = cv2.resize(mask, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    if hair[int(hp[1]), int(hp[0])] < 0.5: hair = 1 - hair
    hb = (hair > 0.5).astype(np.uint8)
    din = cv2.distanceTransform(hb, cv2.DIST_L2, 5); dout = cv2.distanceTransform(1 - hb, cv2.DIST_L2, 5)
    sd = din - dout                                                     # >0 inside hair
    sd = cv2.GaussianBlur(sd, (0, 0), 1.0)
    gy, gx = np.gradient(cv2.GaussianBlur(sd, (0, 0), 3.0)); gn = np.sqrt(gx ** 2 + gy ** 2) + 1e-6
    nx, ny = gx / gn, gy / gn                                           # normal into hair
    # band width per segment (nearest segment), and only near the given edge
    bandw = np.zeros((h, w), np.float32); nearest = np.full((h, w), 1e9, np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    P = np.array(S["points"], np.float64) - [x0, y0]
    for i in range(len(P) - 1):
        p, q = P[i], P[i + 1]; v = q - p; L = np.linalg.norm(v); u = v / L
        t = np.clip((xx - p[0]) * u[0] + (yy - p[1]) * u[1], 0, L)
        dist = np.hypot(xx - (p[0] + u[0] * t), yy - (p[1] + u[1] * t))
        m = dist < nearest; nearest[m] = dist[m]; bandw[m] = S["bands"][i]
    bandw = cv2.GaussianBlur(bandw, (0, 0), 8)          # no width jump at corners between segments
    inband = (np.abs(sd) < bandw) & (nearest < bandw + 4)
    g = S.get("sample_gap", 1); k_s = np.where(sd <= 0, bandw + g, sd + bandw + g); k_h = np.where(sd >= 0, bandw + g, -sd + bandw + g)
    Sk = cv2.remap(crop, xx - nx * k_s, yy - ny * k_s, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    Hr = cv2.remap(crop, xx + nx * k_h, yy + ny * k_h, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    # natural edge: soft transition + hair-tip irregularity (fine streaks along the normal direction)
    rng = np.random.default_rng(S.get("seed", 7))
    nz = rng.standard_normal((h, w)).astype(np.float32)
    streak = cv2.GaussianBlur(nz, (0, 0), S.get("grain", 1.6)); streak /= (streak.std() + 1e-6)   # fine grain: individual hair tips
    blob = cv2.GaussianBlur(rng.standard_normal((h, w)).astype(np.float32), (0, 0), 6); blob /= (blob.std() + 1e-6)
    soft = S.get("soft", 3.5); tips = S.get("tips", 1.6)
    d_eff = sd + tips * streak + S.get("wobble", 0.6) * blob
    a = np.clip(0.5 + d_eff / soft, 0, 1); a = a * a * (3 - 2 * a)
    new = Sk * (1 - a[..., None]) + Hr * a[..., None]
    # a real shaved edge keeps a faint stubble shadow on the skin side: no pale halo
    sh = S.get("shadow", 0.22) * np.exp(-np.clip(-sd, 0, None) / S.get("shadow_px", 4.0)) * (sd < 0)
    new = new * (1 - sh[..., None]) + Hr * sh[..., None]
    wgt = np.clip((bandw - np.abs(sd)) / (bandw * 0.35 + 1e-6), 0, 1); wgt = wgt * wgt * (3 - 2 * wgt) * inband
    crop_out = crop * (1 - wgt[..., None]) + new * wgt[..., None]
    img[y0:y1, x0:x1] = crop_out
    cv2.imwrite(out_p, np.clip(img, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 96]); print("written", out_p)

if __name__ == "__main__": main()
