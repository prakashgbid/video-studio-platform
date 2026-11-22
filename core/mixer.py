"""Dialogue-first mixer: music and effects make room for every spoken line (one duck on the combined background).

mix(stems, out_wav, bg_wav, params)
  * Dialogue is levelled (each line already normalised in its stem) and lightly compressed.
  * For every 10 ms the mixer knows whether someone is speaking (from the dialogue stem itself).
    While they speak, music is pulled down until it sits at least `music_margin` dB under the voice,
    and effects at least `fx_margin` dB under it. The duck starts `lookahead` s before the first word
    (so the first syllable is never masked), falls fast (attack) and recovers slowly after the line
    (release), so it sounds like a mixer riding the faders, not pumping.
  * Outside dialogue the bed plays at full level: the energy of the score is kept.
  * The ducked bed is also written to bg_wav, so the QC gate measures exactly what was mixed.
"""
import subprocess, numpy as np

SR = 48000
DEFAULTS = {"bed_margin": 19, "max_duck_db": 30, "lookahead": 0.15, "hold": 0.30,
            "attack": 0.06, "release": 0.50, "speech_db": -45, "music": 0.8, "fx": 1.0, "dialogue": 1.0,
            "lufs": -16, "true_peak": -2.0}

def load(p):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", p, "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"])
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()

def save(x, p):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", str(x.shape[1]), "-i", "-", p],
                   input=x.astype(np.float32).tobytes(), check=True)

def frames_rms(x, hop):
    m = x.mean(axis=1) if x.ndim == 2 else x; n = len(m) // hop
    return np.sqrt(np.mean(m[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)

def smooth_gain(g, hop_s, attack, release):
    out = np.empty_like(g); y = 1.0
    a_dn, a_up = np.exp(-hop_s / attack), np.exp(-hop_s / release)
    for i, v in enumerate(g):
        a = a_dn if v < y else a_up; y = a * y + (1 - a) * v; out[i] = y
    return out

def duck_gain(d_db, speech, bed_db, margin, p, hop_s):
    need = np.where(speech, np.minimum(0.0, (d_db - margin) - bed_db), 0.0)
    need = np.maximum(need, -p["max_duck_db"])
    g = 10 ** (need / 20)
    return smooth_gain(g, hop_s, p["attack"], p["release"])

def mix(stems, out_wav, bg_wav, params=None):
    p = {**DEFAULTS, **(params or {})}
    D, M, F = (load(stems[k]) for k in ("dialogue", "music", "fx"))
    n = min(len(D), len(M), len(F)); D, M, F = D[:n] * p["dialogue"], M[:n] * p["music"], F[:n] * p["fx"]
    hop = int(SR * 0.01); hop_s = 0.01
    d = frames_rms(D, hop); m = frames_rms(M, hop); f = frames_rms(F, hop)
    d_db = 20 * np.log10(d)
    speech = d_db > p["speech_db"]
    # each spoken line = one span (pauses < 0.45 s merged); its level = average speech energy over the span,
    # measured exactly the way audio_qc.py measures it. The duck covers the span plus lookahead and hold.
    look = int(p["lookahead"] / hop_s); k = int(p["hold"] / hop_s); gap = int(0.45 / hop_s)
    lvl = np.full(len(d_db), -120.0); sp = np.zeros(len(d_db), bool)
    idx = np.flatnonzero(speech); spans = []
    if len(idx):
        st = idx[0]; pv = idx[0]
        for i in idx[1:]:
            if i - pv > gap: spans.append((st, pv + 1)); st = i
            pv = i
        spans.append((st, pv + 1))
    for a0, b0 in spans:
        e = 10 * np.log10(np.mean(d[a0:b0] ** 2) + 1e-12)
        s0, s1 = max(0, a0 - look), min(len(d_db), b0 + k)
        lvl[s0:s1] = np.maximum(lvl[s0:s1], e); sp[s0:s1] = True
    # one gain for the whole background (music + effects together), so their sum sits bed_margin dB under the voice
    bed_r = frames_rms(M + F, hop)
    bed_db = 20 * np.log10(bed_r)
    # bed level judged on a short window so drum hits are caught but single samples are not
    w = 20; bed_db = 10 * np.log10(np.convolve(10 ** (bed_db / 10), np.ones(w) / w, mode='same') + 1e-12)
    gm = duck_gain(lvl, sp, bed_db, p["bed_margin"], p, hop_s); gf = gm
    t = (np.arange(len(gm)) + 0.5) * hop
    gm_s = np.interp(np.arange(n), t, gm)[:, None]; gf_s = np.interp(np.arange(n), t, gf)[:, None]
    bed = M * gm_s + F * gf_s
    save(bed, bg_wav)
    raw = out_wav + ".pre.wav"; save(D + bed, raw)
    # programme loudness + true-peak ceiling (two-pass loudnorm keeps the dialogue/bed balance intact)
    meas = subprocess.run(["ffmpeg", "-v", "info", "-i", raw, "-af",
        f"loudnorm=I={p['lufs']}:TP={p['true_peak']}:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
    import json, re
    js = json.loads(re.findall(r"\{[^{}]+\}", meas)[-1])
    lf = (f"loudnorm=I={p['lufs']}:TP={p['true_peak']}:LRA=11:measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
          f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
    if "inf" in str(js["input_i"]):   # silent programme (animatic with no sound yet): nothing to normalise
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-ar", str(SR), out_wav], check=True)
    else:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-af", f"{lf},alimiter=limit=0.78:level=false", "-ar", str(SR), out_wav], check=True)
    import os; os.remove(raw)
    ducked = float(np.mean(gm[sp] < 0.5)) if sp.any() else 0.0
    return {"speech_seconds": round(float(sp.sum()) * hop_s, 1), "music_ducked_share": round(ducked, 2),
            "max_music_duck_db": round(float(20 * np.log10(gm.min())), 1)}
