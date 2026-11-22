"""Audio quality gate for every built video.

usage: python3 audio_qc.py <production_dir>      (reads build/ stems + final, writes build/audio_qc.json)

Checks, per dialogue line (spans come from the dialogue stem itself):
  1. Dialogue-to-background ratio (DBR): speech level vs music+effects level during the line.
     Pass >= 12 dB (broadcast practice for clear dialogue over a bed); warn 8-12; fail < 8.
  2. Intelligibility: the line is transcribed twice, once from the clean dialogue stem and once
     from the FINAL mix. Word match must be >= 90% (fail < 80%). Catches lines buried by music.
  3. Whole programme: integrated loudness -16 LUFS +/-1.5, true peak <= -1 dBTP, no clipping.
Exit code 1 if anything fails, so a build can refuse to publish.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import sys, os, json, subprocess, re, difflib
import numpy as np

SR = 16000
def load(path, sr=SR, ch=1):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", path, "-ac", str(ch), "-ar", str(sr), "-f", "f32le", "-"])
    return np.frombuffer(raw, np.float32)

def db(x): return 20 * np.log10(np.maximum(x, 1e-9))

def speech_spans(d, sr=SR, hop=0.02, thr_db=-42, min_len=0.35, gap=0.45):
    n = int(sr * hop); fr = len(d) // n
    rms = np.sqrt(np.mean(d[:fr * n].reshape(fr, n) ** 2, axis=1)); on = db(rms) > thr_db
    spans, s = [], None
    for i, v in enumerate(on):
        t = i * hop
        if v and s is None: s = t
        if not v and s is not None:
            spans.append([s, t]); s = None
    if s is not None: spans.append([s, fr * hop])
    merged = []
    for a, b in spans:
        if merged and a - merged[-1][1] < gap: merged[-1][1] = b
        else: merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= min_len]

def words(t):
    t = t.lower().replace("fourteen", "14")
    t = re.sub(r"\b((?:[a-z]\.){2,})", lambda m: m.group(1).replace(".", ""), t)   # A.I. == AI, U.S. == US
    return re.findall(r"[a-z0-9']+", t)

def main(root):
    b = f"{root}/build"; last = json.load(open(f"{b}/last_build.json"))
    stems = last["stems"]; final = last["output"]
    d = load(stems["dialogue"]); bg = load(stems["music"]) * last["mix"].get("music", 0.8) + load(stems["fx"]) * last["mix"].get("fx", 1.0)
    bg_g = last.get("bg_gain_file")
    if bg_g and os.path.exists(bg_g):   # the ducked background actually used in the mix
        bg = load(bg_g)
    mix = load(final)
    from faster_whisper import WhisperModel
    m = WhisperModel("small.en", compute_type="int8")
    res, fails, warns = [], 0, 0
    for a, z in speech_spans(d):
        i, j = int(a * SR), int(z * SR)
        dl = db(np.sqrt(np.mean(d[i:j] ** 2))); bl = db(np.sqrt(np.mean(bg[i:j] ** 2)))
        dbr = dl - bl
        pad = int(0.15 * SR); i2, j2 = max(0, i - pad), min(len(mix), j + pad)
        ref = " ".join(s.text for s in m.transcribe(d[i2:j2], language="en")[0])
        got = " ".join(s.text for s in m.transcribe(mix[i2:j2], language="en")[0])
        wr, wg = words(ref), words(got)
        if not wr or (len(wr) == 1 and wr[0] in ("you", "uh", "um", "hmm") and dl < -30):
            continue          # room tone / breath that the transcriber turned into a filler word: not a line
        match = difflib.SequenceMatcher(None, wr, wg).ratio() if wr else 1.0
        status = "pass"
        if dbr < 8 or match < 0.80: status = "FAIL"
        elif dbr < 12 or match < 0.90: status = "warn"
        fails += status == "FAIL"; warns += status == "warn"
        shot = next((s for s, t0, t1 in last["timeline"] if t0 <= a < t1), "?")
        res.append({"shot": shot, "t": round(a, 2), "dur": round(z - a, 2), "dbr_db": round(float(dbr), 1),
                    "match": round(match, 2), "status": status, "line": ref.strip()[:90], "heard": got.strip()[:90]})
    ln = subprocess.run(["ffmpeg", "-nostats", "-i", final, "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    I = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", ln)[-1]); TP = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", ln)[-1])
    prog = {"lufs": I, "true_peak": TP, "lufs_ok": abs(I + 16) <= 1.5, "peak_ok": TP <= -1.0}
    fails += (not prog["lufs_ok"]) + (not prog["peak_ok"])
    out = {"lines": res, "programme": prog, "fails": fails, "warns": warns,
           "rules": {"dbr_pass": 12, "dbr_fail": 8, "match_pass": 0.9, "match_fail": 0.8, "lufs": -16, "true_peak": -1}}
    json.dump(out, open(f"{b}/audio_qc.json", "w"), indent=1)
    for r in res:
        print(f"{r['status']:>4}  {r['shot']:>5} {r['t']:7.2f}s  DBR {r['dbr_db']:5.1f} dB  match {r['match']:.2f}  {r['line'][:60]}")
    print(f"programme: {I} LUFS, true peak {TP} dBTP | {len(res)} lines, {fails} fail, {warns} warn")
    return 1 if fails else 0

if __name__ == "__main__": sys.exit(main(sys.argv[1]))
