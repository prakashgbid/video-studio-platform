"""Kokoro character voice line: kokoro_say.py <voice> "<text>" <out.wav> [speed]"""
import sys, warnings; warnings.filterwarnings("ignore")
from kokoro import KPipeline
import soundfile as sf, numpy as np
v, text, out = sys.argv[1], sys.argv[2], sys.argv[3]; speed = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
pipe = KPipeline(lang_code=v[0], repo_id="hexgrad/Kokoro-82M")
audio = np.concatenate([a for _, _, a in pipe(text, voice=v, speed=speed)])
sf.write(out, audio, 24000)
