#!/usr/bin/env python3
"""Teaching-moment reels from a built film or episode (build plan 1.6).

    python reelcut.py list  <production>              # reels defined in production.json "reels"
    python reelcut.py cut   <production> [reel ids]   # -> final/reels/<id>_9x16.mp4 + <id>_dialogue.wav (clean stem)
    python reelcut.py guide <production>              # build/framing_guide.mp4: the cut with the 9:16 safe area drawn

production.json:
    "reels": [{"id": "pranayama", "title": "Breathe away fear", "shots": ["SC02_040", "SC02_050"],
               "x": 0.5, "pad": 0.3, "music_db": -18}]
x = horizontal centre of the 9:16 window (0 = left edge, 1 = right). Uses the last build (build/last_build.json):
its timeline and the separate dialogue/music stems, so the dialogue is clean and the music sits low.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402
import os, sys, json, argparse, subprocess

def run(c): subprocess.run(c, check=True)

def load(root):
    root = os.path.realpath(root); P = json.load(open(os.path.join(root, "production.json")))
    lb = os.path.join(root, "build", "last_build.json")
    if not os.path.exists(lb): sys.exit("no build yet: run build.py first")
    return root, P, json.load(open(lb))

def window(P, B, r):
    tl = {sid: (a, b) for sid, a, b in B["timeline"]}
    miss = [s for s in r["shots"] if s not in tl]
    if miss: sys.exit(f"reel {r['id']}: shots not in the last build: {', '.join(miss)}")
    pad = r.get("pad", 0.3); t0 = max(0, min(tl[s][0] for s in r["shots"]) - pad); t1 = min(B["duration"], max(tl[s][1] for s in r["shots"]) + pad)
    return t0, t1

def cut(root, P, B, r):
    t0, t1 = window(P, B, r); d = t1 - t0
    vid = os.path.join(root, "build", "video.mp4"); st = B["stems"]
    out_dir = os.path.join(root, "final", "reels"); os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{r['id']}_9x16.mp4"); tmp = out[:-4] + ".tmp.mp4"
    x = r.get("x", 0.5); mdb = r.get("music_db", -18)
    vf = f"crop=ih*9/16:ih:(iw-ih*9/16)*{x}:0,scale=1080:1920:flags=lanczos,setsar=1"
    af = (f"[1]atrim={t0}:{t1},asetpts=PTS-STARTPTS[d];[2]atrim={t0}:{t1},asetpts=PTS-STARTPTS,volume={mdb}dB[m];"
          f"[d][m]amix=inputs=2:normalize=0,afade=t=in:d=0.15,afade=t=out:st={max(d-0.3,0)}:d=0.3[a]")
    pre = out[:-4] + ".pre.wav"     # two-pass loudness: social platforms play at -14 LUFS
    run(["ffmpeg", "-y", "-v", "error", "-i", vid, "-i", st["dialogue"], "-i", st["music"], "-filter_complex", af, "-map", "[a]", "-ar", "48000", pre])
    import re
    m = subprocess.run(["ffmpeg", "-v", "info", "-i", pre, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    js = json.loads(re.findall(r"\{[^{}]+\}", m)[-1])
    ln = ("anull" if "inf" in js["input_i"] else
          f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={js['input_i']}:measured_TP={js['input_tp']}:measured_LRA={js['input_lra']}:"
          f"measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t0}", "-t", f"{d}", "-i", vid, "-i", pre,
         "-filter_complex", f"[0]{vf}[v];[1]{ln}[a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", tmp])
    os.remove(pre)
    os.replace(tmp, out)
    wav = os.path.join(out_dir, f"{r['id']}_dialogue.wav")
    run(["ffmpeg", "-y", "-v", "error", "-i", st["dialogue"], "-af", f"atrim={t0}:{t1},asetpts=PTS-STARTPTS", "-ar", "48000", wav])
    print(f"{r['id']}: {d:.1f}s -> {os.path.relpath(out, root)} (+ clean dialogue wav)")

def guide(root, P, B):
    src = os.path.join(root, "build", P.get("output", "cut.mp4")); out = os.path.join(root, "build", "framing_guide.mp4")
    vf = ("drawbox=x=(iw-ih*9/16)/2:y=0:w=ih*9/16:h=ih:color=yellow@0.9:t=3,"
          "drawbox=x=(iw-ih*9/16)/2+ih*9/16*0.08:y=ih*0.12:w=ih*9/16*0.84:h=ih*0.68:color=yellow@0.45:t=2")
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "copy", out])
    print("framing guide:", os.path.relpath(out, root), "(outer box = 9:16 crop at x=0.5; inner = caption-safe face zone)")

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list").add_argument("root"); g = sp.add_parser("guide"); g.add_argument("root")
    c = sp.add_parser("cut"); c.add_argument("root"); c.add_argument("ids", nargs="*")
    a = ap.parse_args(); root, P, B = load(a.root); R = P.get("reels", [])
    if a.cmd == "list":
        for r in R: t0, t1 = window(P, B, r); print(f"{r['id']:20} {t1-t0:5.1f}s  {', '.join(r['shots'])}  {r.get('title', '')}")
    elif a.cmd == "guide": guide(root, P, B)
    else:
        for r in R:
            if not a.ids or r["id"] in a.ids: cut(root, P, B, r)

if __name__ == "__main__": main()
