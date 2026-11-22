#!/usr/bin/env python3
"""Scene contact sheets for review (build plan 1.7).

    python contact.py <production> [--scene SC01] [--cols 4]

One image per scene: every film shot's best current frame (mid-shot), labelled with shot id, stage and version,
duration and cast; out-of-date stages are marked. Written to docs/contact_<scene>.jpg.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402
import os, sys, argparse, subprocess, tempfile
import numpy as np, cv2
import filmstages

def frame(path, t, w):
    if path.endswith(".png"): im = cv2.imread(path)
    else:
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", f.name], check=True)
            im = cv2.imread(f.name)
    h = int(im.shape[0] * w / im.shape[1]); return cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--scene"); ap.add_argument("--cols", type=int, default=4)
    a = ap.parse_args(); F = filmstages.Film(a.root); W = 480
    scenes = {}
    for s in F.shots: scenes.setdefault(s["id"].split("_")[0], []).append(s)
    for sc, shots in scenes.items():
        if a.scene and sc != a.scene: continue
        tiles = []
        for s in shots:
            p, st = F.best(s)
            if p:
                im = frame(p, s["dur"] / 2, W); c = F.current(s, st)
                stale = any(F.current(s, x) and F.current(s, x)["key"] != F.key(s, x) for x in F.plan(s))
                lab = f"{s['id']}  {st} v{c['v']}{'  OUT OF DATE' if stale else ''}"
            else:
                im = np.full((int(W * F.H / F.W), W, 3), 40, np.uint8); lab = f"{s['id']}  no stage yet"
            bar = np.full((52, W, 3), 18, np.uint8)
            cv2.putText(bar, lab, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 200, 255) if "OUT" in lab else (235, 235, 235), 1, cv2.LINE_AA)
            cv2.putText(bar, f"{s['dur']:.1f}s  {', '.join(s.get('cast', []))}  {s.get('camera', '')[:38]}", (8, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (170, 170, 170), 1, cv2.LINE_AA)
            tiles.append(np.vstack([im, bar]))
        th = max(t.shape[0] for t in tiles); tiles = [np.vstack([t, np.zeros((th - t.shape[0], W, 3), np.uint8)]) for t in tiles]
        while len(tiles) % a.cols: tiles.append(np.zeros_like(tiles[0]))
        rows = [np.hstack(tiles[i:i + a.cols]) for i in range(0, len(tiles), a.cols)]
        head = np.full((60, rows[0].shape[1], 3), 10, np.uint8)
        cv2.putText(head, f"{F.P.get('name', '')} - {sc} - {len(shots)} shots, {sum(s['dur'] for s in shots):.1f}s", (14, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        out = os.path.join(F.root, "docs", f"contact_{sc}.jpg"); os.makedirs(os.path.dirname(out), exist_ok=True)
        cv2.imwrite(out, np.vstack([head] + rows), [cv2.IMWRITE_JPEG_QUALITY, 88]); print(out)

if __name__ == "__main__": main()
