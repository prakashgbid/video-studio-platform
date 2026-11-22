#!/usr/bin/env python3
"""Transcribe each scene's audio (audio.mp3, or the clip's own audio) and print expected vs heard.
usage: vo_check.py <project> [--takes]   (--takes also checks take_a/take_b)"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import sys, os, glob, re, subprocess, numpy as np
from faster_whisper import WhisperModel

root = os.path.abspath(os.path.expanduser(sys.argv[1])); takes = "--takes" in sys.argv
m = WhisperModel("small.en", device="cpu", compute_type="int8")


def heard(f):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", f, "-vn", "-f", "s16le", "-ac", "1", "-ar", "16000", "-"], capture_output=True).stdout
    return " ".join(x.text.strip() for x in m.transcribe(np.frombuffer(raw, np.int16).astype(np.float32) / 32768)[0])


def expected(d):
    p = os.path.join(d, "vo_script.md")
    if os.path.exists(p):
        t = open(p).read(); return t.split("## Spoken words (for checking)")[-1].strip()
    p = os.path.join(d, "prompt.md")
    if os.path.exists(p):
        q = re.findall(r'"([^"]{20,})"', open(p).read()); return q[0] if q else ""
    return ""


def norm(s): return re.sub(r"[^a-z0-9 ]", "", s.lower())


for d in sorted(glob.glob(os.path.join(root, "scenes", "scene-*"))):
    files = [os.path.join(d, "audio.mp3")] if os.path.exists(os.path.join(d, "audio.mp3")) else []
    if not files and os.path.exists(os.path.join(d, "video.mp4")) and os.path.exists(os.path.join(d, "prompt.md")) \
            and "Google Flow clip" in open(os.path.join(d, "prompt.md")).read():
        files = [os.path.join(d, "video.mp4")]
    if takes: files += sorted(glob.glob(os.path.join(d, "take_*.mp3")))
    exp = expected(d)
    for f in files:
        h = heard(f); ew, hw = norm(exp).split(), norm(h).split()
        miss = [w for w in ew if w not in hw]
        flag = "OK " if len(miss) <= max(1, len(ew) // 12) else "CHECK"
        print(f"{flag} {os.path.basename(d)}/{os.path.basename(f)}\n   expected: {exp}\n   heard:    {h}\n   missing:  {miss}")
