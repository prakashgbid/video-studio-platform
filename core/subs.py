#!/usr/bin/env python3
"""Word timings for every scene (faster-whisper, word_timestamps) -> build/words/<scene>.json.
reel.py uses them for (1) cue-timed reveals: a bullet / card / diagram node appears when its "cue" phrase is spoken,
and (2) the .srt caption files for every rendered video.
Run after the voiceovers (audio.mp3) and Flow clips (video.mp4) are in place; only changed scenes are redone.
usage: subs.py <project> [--force]"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import sys, os, glob, json, subprocess
import numpy as np
from faster_whisper import WhisperModel

root = os.path.abspath(os.path.expanduser(sys.argv[1])); force = "--force" in sys.argv
out_dir = os.path.join(root, "build", "words"); os.makedirs(out_dir, exist_ok=True)
model = None
for d in sorted(glob.glob(os.path.join(root, "scenes", "scene-*"))):
    sc = json.load(open(os.path.join(d, "scene.json")))
    src = os.path.join(d, "audio.mp3") if sc["type"] != "clip" else os.path.join(d, "video.mp4")
    if not os.path.exists(src): continue
    out = os.path.join(out_dir, os.path.basename(d) + ".json")
    if not force and os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src): continue
    if model is None: model = WhisperModel("small.en", device="cpu", compute_type="int8")
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", src, "-vn", "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True).stdout
    segs, _ = model.transcribe(np.frombuffer(raw, np.int16).astype(np.float32) / 32768, word_timestamps=True)
    words = [[w.word.strip(), round(w.start, 3), round(w.end, 3)] for s in segs for w in (s.words or [])]
    json.dump({"src": src, "words": words}, open(out, "w"))
    print(f"{os.path.basename(d)}: {len(words)} words")
print("done")
