"""Modular, cached video build.  One production = one folder with production.json + assets.json.

    python3 build.py <production_dir> [--plan] [--only S12,S37b] [--force S12]

Principles (the framework rule, see engine/MODULAR.md):
  * Everything is a small unit with its own spec: a SHOT, a DERIVED asset (e.g. a keyed clip),
    an audio STEM, the final MUX.
  * Each unit's cache key = hash(its spec + engine version + the bytes-identity of every input file).
    If the key has not changed, the unit is reused, never rebuilt.
  * The build NEVER generates AI media. Generated media (Flow clips, ElevenLabs speech/music/SFX)
    are ASSETS registered in assets.json with their prompt, generation ids and cost; they change only
    when someone regenerates that one asset on purpose.
  * Audio cues are anchored to a shot ("shot": "S12", "at": 0.2), so changing one shot's length
    moves only the cues that belong after it; nothing else is re-generated.
  * Final assembly is a stream copy (concat + mux), so re-cutting costs seconds.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import os, sys, json, hashlib, subprocess, textwrap, argparse, shutil, time

ENGINE_VERSION = "build-1.0"   # picture units (shots, derived assets)
AUDIO_VERSION = "audio-1.1"    # stems, mix, QC: bump to rebuild sound only
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"; FONTB = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
STUDIO = os.path.dirname(os.path.dirname(os.path.realpath(HERE)))   # 14 Video Studio (on Google Drive)
# Build caches are regenerable output: they live on the local disk, never on Drive.
RUNTIME = os.environ.get("VIDEO_RUNTIME", os.path.expanduser("~/VideoStudioRuntime"))
def cache_dir(root):
    rel = os.path.relpath(os.path.realpath(root), os.path.realpath(STUDIO))
    if rel.startswith(".."): rel = "_external/" + sha(os.path.realpath(root))
    return os.path.join(RUNTIME, "cache", rel, "cache")

# ---------------------------------------------------------------- helpers
def sha(x): return hashlib.sha1(x if isinstance(x, bytes) else json.dumps(x, sort_keys=True).encode()).hexdigest()[:12]
def fid(path):
    """Identity of an input file: size + mtime + hash of first/last 1 MB (fast, catches real changes)."""
    st = os.stat(path); h = hashlib.sha1(f"{st.st_size}".encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20)); f.seek(max(0, st.st_size - (1 << 20))); h.update(f.read(1 << 20))
    return h.hexdigest()[:12]
def run(cmd): subprocess.run(cmd, check=True)
def esc(t): return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace(",", "\\,")
def dt(text, size=60, y="(h-text_h)/2", color="white", font=FONTB, x="(w-text_w)/2"):
    return f"drawtext=fontfile='{font}':text='{esc(text)}':fontsize={size}:fontcolor={color}:x={x}:y={y}:expansion=none"

class Build:
    def __init__(self, root, force=()):
        self.root = os.path.abspath(root)
        self.P = json.load(open(f"{self.root}/production.json"))
        self.A = json.load(open(f"{self.root}/assets.json"))
        self.out = f"{self.root}/build"; os.makedirs(self.out, exist_ok=True)
        self.cache = cache_dir(self.root); os.makedirs(self.cache, exist_ok=True)
        self.fps = self.P["fps"]; self.W, self.H = self.P["size"]
        self.force = set(force); self.log = []; self.qc = True
        self.label = self.P.get("label")   # e.g. "ANIMATIC" burns shot ids; None for final
        cp = self.P.get("cast"); self.cast = json.load(open(os.path.join(self.root, cp))) if cp else {}
        self.resolve()

    # assets ----------------------------------------------------------------
    def asset(self, aid):
        a = self.A[aid]; p = a["path"]
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.root, p))

    def derived(self, aid):
        """Derived assets (e.g. keyed talent) are cached units too."""
        a = self.A[aid]; d = a["derive"]
        src = self.asset(d["from"]) if d.get("from") else None
        tool = {"key": ("keyer.py", "KEYER_VERSION"), "title": ("titles.py", "TITLES_VERSION")}.get(d["op"])
        extra = {k: fid(self.path_of(v)) for k, v in d.get("spec", {}).items() if k == "bg_asset"}
        key = sha({"d": d, "src": fid(src) if src else None, "extra": extra, "v": ENGINE_VERSION, "tool": self.tool_version(*tool) if tool else None})
        dst = f"{self.cache}/{aid}.{key}{d.get('ext', '.mp4' if d['op'] == 'title' else '.mov')}"
        if os.path.exists(dst) and aid not in self.force: self.log.append(("reuse", aid)); return dst
        if getattr(self, "planning", False): raise FileNotFoundError(dst)   # --plan never builds
        if d["op"] == "title":                 # animated title card (engine/titles.py), background from an asset
            spec = dict(d["spec"]); spec.setdefault("fps", self.fps); spec.setdefault("size", [self.W, self.H])
            if spec.get("bg_asset"): spec["bg"] = self.path_of(spec.pop("bg_asset"))
            tmp = dst + ".spec.json"; json.dump(spec, open(tmp, "w"))
            run([PY, f"{HERE}/titles.py", tmp, dst]); os.remove(tmp)
            self.log.append(("build", aid)); return dst
        if d["op"] == "audio_filter":          # deterministic audio processing of one asset (e.g. areverse, trims, EQ)
            run(["ffmpeg", "-v", "error", "-y", "-i", src, "-af", d["af"], "-ar", "48000", dst])
            self.log.append(("build", aid)); return dst
        if d["op"] == "key":
            run([PY, f"{HERE}/keyer.py", src, dst, "--screen", d.get("screen", "green"), "--feather", str(d.get("feather", 2)),
                 "--bar-margin", str(d.get("bar_margin", 2))] + (["--unletterbox"] if d.get("unletterbox") else []) + (["--adaptive"] if d.get("adaptive") else []))
        self.log.append(("build", aid)); return dst

    def tool_version(self, fname, var):
        import re
        m = re.search(rf'{var}\s*=\s*"([^"]+)"', open(f"{HERE}/{fname}").read()); return m.group(1) if m else fid(f"{HERE}/{fname}")

    def path_of(self, aid): return self.derived(aid) if "derive" in self.A[aid] else self.asset(aid)

    # resolve: talent fitting, talent audio, lower thirds ---------------------
    def speech_span(self, aid):
        """(first word start, last word end) of the asset's speech, from word-level timestamps; cached by file identity."""
        import numpy as np
        p = self.asset(aid); fp = f"{self.cache}/fit.json"
        cache = json.load(open(fp)) if os.path.exists(fp) else {}
        k = f"words:{aid}:{fid(p)}"
        if k in cache: return tuple(cache[k][:2])
        x = np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-i", p, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]), np.float32)
        if not hasattr(self, "_wm"):
            from faster_whisper import WhisperModel; self._wm = WhisperModel("small.en", compute_type="int8")
        segs, _ = self._wm.transcribe(x, word_timestamps=True, vad_filter=True)
        words = [w for sg in segs for w in (sg.words or []) if w.word.strip()]
        span = (round(words[0].start, 2), round(words[-1].end, 2), " ".join(w.word.strip() for w in words)) if words else (0.0, len(x) / 16000, "")
        cache[k] = list(span); json.dump(cache, open(fp, "w"), indent=1); return span[:2]

    def resolve(self):
        """Derived, deterministic shot settings. Never generates media."""
        seen = set(); auto_cues = []
        for s in self.P["shots"]:
            for L in s.get("layers", []):
                if L.get("type") == "talent" and L.get("fit"):
                    src = self.A[L["asset"]]["derive"]["from"] if "derive" in self.A[L["asset"]] else L["asset"]
                    if s.get("silent"):
                        L.setdefault("in", 0.5)
                    else:
                        a, b = self.speech_span(src)
                        L["in"] = round(max(0.0, a - L.get("lead", 0.35)), 2)
                        s["dur"] = round(max(s.get("min_dur", 0), (b + L.get("tail", 0.5)) - L["in"]), 2)
                        if s.get("talent_audio", True):
                            auto_cues.append({"shot": s["id"], "at": 0, "asset": src, "src_in": L["in"], "src_out": round(L["in"] + s["dur"], 2), "_auto": True})
            ch = s.get("character"); lt = s.get("lower_third", None)
            if ch and ch in self.cast and (lt is True or (lt is None and ch not in seen)):
                s["_lower_third"] = {"name": self.cast[ch]["name"], "title": self.cast[ch]["title"], "at": 0.4,
                                     "dur": round(min(3.4, s["dur"] - 0.5), 2)}
            if ch: seen.add(ch)
        if auto_cues:
            shots_with_auto = {c["shot"] for c in auto_cues}
            self.P["audio"]["dialogue"] = [c for c in self.P["audio"]["dialogue"] if c["shot"] not in shots_with_auto] + auto_cues

    def lower_third(self, lt):
        a, b = lt["at"], lt["at"] + lt["dur"]; f = 0.35; H = self.H
        al = f"if(lt(t,{a}),0,if(lt(t,{a+f}),(t-{a})/{f},if(lt(t,{b-f}),1,if(lt(t,{b}),({b}-t)/{f},0))))"
        sh = "shadowcolor=black@0.7:shadowx=2:shadowy=2"
        return [f"drawbox=x=96:y={H-236}:w=6:h=100:color=0xE0A84B@0.95:t=fill:enable='between(t,{a+0.1},{b-0.1})'",
                dt(lt["name"], 50, f"{H-238}", "white", FONTB, "124").replace(":expansion=none", f":alpha='{al}':{sh}:expansion=none"),
                dt(lt["title"], 30, f"{H-176}", "white", FONT, "126").replace(":expansion=none", f":alpha='{al}':{sh}:expansion=none")]

    # timeline --------------------------------------------------------------
    def timeline(self):
        t = 0.0; tl = []
        for s in self.P["shots"]:
            tl.append((s, t)); t += s["dur"]
        return tl, t

    # shots -----------------------------------------------------------------
    def shot_inputs(self, s):
        ids = [v for k, v in s.items() if k in ("asset", "plate") and v and isinstance(v, str)] + [l["asset"] for l in s.get("layers", [])] + list(s.get("assets", [])) + [it["asset"] for it in s.get("items", [])]
        return ids

    def shot_key(self, s):
        files = {i: fid(self.path_of(i)) for i in self.shot_inputs(s)}
        return sha({"s": s, "files": files, "fps": self.fps, "size": [self.W, self.H], "label": self.label, "v": ENGINE_VERSION})

    def render_shot(self, s):
        key = self.shot_key(s); dst = f"{self.cache}/{s['id']}.{key}.mp4"
        if os.path.exists(dst) and s["id"] not in self.force: self.log.append(("reuse", s["id"])); return dst
        d = s["dur"]; n = int(round(d * self.fps)); W, H, F = self.W, self.H, self.fps; k = s["kind"]
        if k == "still":
            ins = ["-i", self.path_of(s["asset"])]
            z = s.get("zoom", 0.0006)
            fc = f"[0]scale={int(W*1.25)}:-2,zoompan=z='1.0+{z}*on':d={n}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={F}[base]"
        elif k == "video":
            ins = ["-stream_loop", "-1", "-ss", str(s.get("in", 0)), "-i", self.path_of(s["asset"])]
            fc = f"[0]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={F},trim=duration={d},setpts=PTS-STARTPTS[base]"
        elif k == "montage" and s.get("items"):
            ins, parts = [], []; seg = d / len(s["items"])
            for i, it in enumerate(s["items"]):
                ins += ["-ss", str(it.get("in", 1.0)), "-t", f"{seg:.3f}", "-i", self.path_of(it["asset"])]
            ins += ["-f", "lavfi", "-i", f"color=c=0x0b0d12:s={W}x{H}:r={F}:d={d}"]; bgi = len(s["items"])
            for i in range(len(s["items"])):
                parts.append(f"[{i}]fps={F},scale={W}:-2,setpts=PTS-STARTPTS,format=yuva420p[m{i}]")
            fc = ";".join(parts) + ";" + "".join(f"[m{i}]" for i in range(len(s["items"]))) + f"concat=n={len(s['items'])}:v=1:a=0[mm];[{bgi}][mm]overlay=(W-w)/2:H-h:shortest=1[base]"
        elif k == "montage":
            ins, parts = [], []; seg = d / len(s["assets"])
            for i, aid in enumerate(s["assets"]):
                ins += ["-loop", "1", "-framerate", str(F), "-t", f"{seg:.3f}", "-i", self.path_of(aid)]
                parts.append(f"[{i}]crop=iw/4:ih:0:0,scale=-2:{H},pad={W}:{H}:(ow-iw)/2:0:black,setsar=1[m{i}]")
            fc = ";".join(parts) + ";" + "".join(f"[m{i}]" for i in range(len(s["assets"]))) + f"concat=n={len(s['assets'])}:v=1:a=0[base]"
        else:  # card / screen / black / call: solid colour base
            col = s.get("bg", {"screen": "0x0b1020", "call": "0x111827"}.get(k, "black"))
            ins = ["-f", "lavfi", "-i", f"color=c={col}:s={W}x{H}:r={F}:d={d}"]; fc = "[0]null[base]"
        tail = "[base]"; nin = len([x for x in ins if x == "-i"])
        for j, L in enumerate(s.get("layers", [])):
            p = self.path_of(L["asset"]); i = nin + j
            if L["type"] == "talent":     # keyed video with alpha
                ins += ["-ss", str(L.get("in", 0)), "-i", p]; sc = L.get("scale", 1.0); x, y = L.get("x", "(W-w)/2"), L.get("y", "H-h")
                fc += f";[{i}]fps={F},trim=duration={d},setpts=PTS-STARTPTS,scale={int(W*sc)}:-2,format=yuva420p[l{j}];{tail}[l{j}]overlay={x}:{y}:shortest=0[b{j}]"
            else:                          # sheet panel (animatic stand-in)
                ins += ["-i", p]; hh = L.get("h", 860); x, y = L.get("x", "W-w-260"), L.get("y", "H-h")
                fc += f";[{i}]crop=iw/4:ih:0:0,scale=-2:{hh}[l{j}];{tail}[l{j}]overlay={x}:{y}[b{j}]"
            tail = f"[b{j}]"
        texts = []
        if s.get("text"):
            if k == "card":
                lines = s["text"].split("|"); texts.append(dt(lines[0], 84, "(h-text_h)/2-40" if len(lines) > 1 else "(h-text_h)/2"))
                if len(lines) > 1: texts.append(dt(lines[1], 44, "h/2+50", "white@0.85", FONT))
            elif k == "screen" and s.get("block"):        # paragraph, vertically and horizontally centred as one block
                size = s.get("size", 34); lh = int(size * 1.45); lines = textwrap.wrap(s["text"], s.get("wrap", 70))
                top = (self.H - lh * len(lines)) // 2
                for i, ln in enumerate(lines): texts.append(dt(ln, size, f"{top + i*lh}", s.get("color", "white@0.9"), FONT))
            elif k == "screen":
                for i, ln in enumerate(textwrap.wrap(s["text"], 52)): texts.append(dt(ln, 56, f"(h/2-70)+{i*80}"))
            else: texts.append(dt(s["text"], 48, "h-170"))
        if s.get("name"): texts.append(dt(s["name"].upper(), 30, "60", "white@0.8", FONTB, "60"))
        if s.get("_lower_third"): texts += self.lower_third(s["_lower_third"])
        if self.label: texts.append(dt(f"{self.label} · {s['id']}", 24, "h-46", "white@0.55", FONT, "30"))
        chain = ",".join(texts) if texts else "null"
        fc += f";{tail}{chain},trim=duration={d},format=yuv420p[out]"
        tmp = dst[:-4] + ".tmp.mp4"
        run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", fc, "-map", "[out]", "-frames:v", str(n), "-r", str(F),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", tmp])
        os.replace(tmp, dst); self.log.append(("build", s["id"])); return dst

    # audio stems -----------------------------------------------------------
    def abs_cues(self, track, starts):
        out = []
        for c in self.P["audio"][track]:
            t = starts[c["shot"]] + c.get("at", 0) + self.P.get("offset", 0)
            out.append({**c, "t": round(t, 3)})
        return out

    def stem(self, track, starts, total):
        cues = self.abs_cues(track, starts)
        key = sha({"track": track, "cues": cues, "files": {c["asset"]: fid(self.path_of(c["asset"])) for c in cues},
                   "total": total, "v": AUDIO_VERSION})
        dst = f"{self.cache}/stem_{track}.{key}.wav"
        if os.path.exists(dst) and track not in self.force: self.log.append(("reuse", f"stem:{track}")); return dst
        ins, fl = [], []
        for i, c in enumerate(cues):
            ins += ["-i", self.path_of(c["asset"])]; ms = int(c["t"] * 1000)
            f = f"[{i}]aresample=48000,aformat=channel_layouts=stereo"
            if "src_in" in c or "src_out" in c: f += f",atrim={c.get('src_in', 0)}:{c.get('src_out', 99999)},asetpts=PTS-STARTPTS"
            if c.get("tempo"): f += f",atempo={c['tempo']}"
            if c.get("fade_in"): f += f",afade=t=in:d={c['fade_in']}"
            if c.get("dur"): f += f",atrim=0:{c['dur']},afade=t=out:st={max(c['dur']-c.get('fade', 0.4), 0)}:d={c.get('fade', 0.4)}"
            vol = c.get("vol", 1.0)
            if track == "dialogue" and c.get("level", True): vol *= self.line_gain(c)   # every line to the same speech level
            f += f",volume={vol:.4f},adelay={ms}|{ms}[c{i}]"; fl.append(f)
        fc = ";".join(fl) + ";" + "".join(f"[c{i}]" for i in range(len(cues))) + f"amix=inputs={len(cues)}:normalize=0,apad,atrim=0:{total}[a]"
        run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", fc, "-map", "[a]", "-ar", "48000", dst + ".tmp.wav"])
        os.replace(dst + ".tmp.wav", dst); self.log.append(("build", f"stem:{track}")); return dst

    def line_gain(self, c, target_db=-20.0):
        """Gain that brings this line's speech (active frames only) to target_db RMS."""
        import numpy as np
        p = self.path_of(c["asset"]); a = c.get("src_in", 0); b = c.get("src_out", None)
        cmd = ["ffmpeg", "-v", "error", "-ss", str(a)] + (["-t", str(b - a)] if b else []) + ["-i", p, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
        x = np.frombuffer(subprocess.check_output(cmd), np.float32); hop = 320; n = len(x) // hop
        if n == 0: return 1.0
        r = np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12); dbs = 20 * np.log10(r)
        act = dbs[dbs > dbs.max() - 25]
        lvl = 10 * np.log10(np.mean(10 ** (act / 10))) if len(act) else -20
        return float(min(8.0, max(0.125, 10 ** ((target_db - lvl) / 20))))

    def mix(self, stems, total):
        """Dialogue-first mix (engine/mixer.py): bed ducks under every line, then loudness to target."""
        sys.path.insert(0, HERE); import mixer
        m = self.P.get("mix", {}); params = {k: v for k, v in m.items() if k in mixer.DEFAULTS}
        key = sha({"stems": [os.path.basename(s) for s in stems.values()], "mix": params, "v": AUDIO_VERSION, "mixer": fid(f"{HERE}/mixer.py")})
        dst = f"{self.cache}/mix.{key}.wav"; bg = f"{self.cache}/bed.{key}.wav"
        self.bed = bg; self.mix_params = {**mixer.DEFAULTS, **params}
        if os.path.exists(dst) and os.path.exists(bg): self.log.append(("reuse", "mix")); return dst
        info = mixer.mix(stems, dst, bg, params); self.log.append(("build", "mix")); print("  mix:", info); return dst

    def mix_old(self, stems, total):
        m = self.P.get("mix", {}); key = sha({"stems": [os.path.basename(s) for s in stems.values()], "mix": m, "v": ENGINE_VERSION})
        dst = f"{self.cache}/mix.{key}.wav"
        if os.path.exists(dst): self.log.append(("reuse", "mix")); return dst
        ins = []; order = ["dialogue", "fx", "music"]
        for t in order: ins += ["-i", stems[t]]
        fc = (f"[0]volume={m.get('dialogue', 1.6)},asplit[d][key];[1]volume={m.get('fx', 1.0)}[f];[2]volume={m.get('music', 0.8)}[mu];"
              "[mu][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=350[md];"
              f"[d][f][md]amix=inputs=3:normalize=0,loudnorm=I={m.get('lufs', -16)}:TP=-1[a]")
        run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", fc, "-map", "[a]", "-ar", "48000", dst + ".tmp.wav"])
        os.replace(dst + ".tmp.wav", dst); self.log.append(("build", "mix")); return dst

    # assemble --------------------------------------------------------------
    def build(self, only=None, plan=False):
        tl, total = self.timeline(); starts = {s["id"]: t for s, t in tl}
        total += self.P.get("offset", 0)
        if plan:
            self.planning = True
            for s, t in tl:
                try: key = self.shot_key(s); cached = os.path.exists(f"{self.cache}/{s['id']}.{key}.mp4")
                except FileNotFoundError as e: cached = False
                print(f"{s['id']:>5} {t:7.2f}s {s['dur']:5.2f}s {s['kind']:8} {'cached' if cached else 'WILL RENDER'}")
            return
        t0 = time.time(); segs = []
        for s, _ in tl:
            if only and s["id"] not in only:
                key = self.shot_key(s); p = f"{self.cache}/{s['id']}.{key}.mp4"
                segs.append(p if os.path.exists(p) else self.render_shot(s)); continue
            segs.append(self.render_shot(s))
        lst = f"{self.out}/concat.txt"; open(lst, "w").write("".join(f"file '{p}'\n" for p in segs))
        vid = f"{self.out}/video.mp4"; run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", vid])
        stems = {t: self.stem(t, starts, total) for t in ("dialogue", "fx", "music")}
        mixed = self.mix(stems, total)
        name = self.P.get("output", "cut.mp4"); out = f"{self.out}/{name}"
        run(["ffmpeg", "-y", "-v", "error", "-i", vid, "-i", mixed, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out])
        built = [x for a, x in self.log if a == "build"]; reused = [x for a, x in self.log if a == "reuse"]
        print(f"done in {time.time()-t0:.1f}s -> {out}\n  rebuilt ({len(built)}): {', '.join(built) or 'nothing'}\n  reused  ({len(reused)})")
        json.dump({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "rebuilt": built, "reused": len(reused), "duration": total,
                   "output": out, "stems": stems, "bg_gain_file": self.bed, "mix": self.mix_params,
                   "timeline": [[s["id"], t + self.P.get("offset", 0), t + self.P.get("offset", 0) + s["dur"]] for s, t in tl]},
                  open(f"{self.out}/last_build.json", "w"), indent=1)
        # prune: cache files that this build did not use (superseded versions) are pure build output
        used = set(segs) | set(stems.values()) | {mixed, self.bed} | {self.derived(a) for a in self.A if "derive" in self.A[a]}
        freed = 0
        for f in os.listdir(f"{self.cache}"):
            p = f"{self.cache}/{f}"
            if p not in used and not f.endswith(".json"): freed += os.path.getsize(p); os.remove(p)
        if freed: print(f"  pruned superseded cache: {freed/1e6:.0f} MB")
        if self.qc:
            print("audio QC (dialogue clarity, loudness):")
            rc = subprocess.run([PY, f"{HERE}/audio_qc.py", self.root]).returncode
            if rc: print("AUDIO QC FAILED: see build/audio_qc.json. Do not publish until fixed."); sys.exit(1)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--plan", action="store_true")
    ap.add_argument("--only", default=""); ap.add_argument("--force", default=""); ap.add_argument("--no-qc", action="store_true")
    a = ap.parse_args()
    b = Build(a.root, force=[x for x in a.force.split(",") if x]); b.qc = not a.no_qc
    b.build(only=set(x for x in a.only.split(",") if x) or None, plan=a.plan)
