#!/usr/bin/env python3
"""Film shot stages (build plan 1.4, 1.5): boards -> layout -> photoreal -> facelock -> upscale.

    python filmstages.py status <production>                       # every film shot, every stage: current version or missing
    python filmstages.py run <production> [--shots SC01_010,..] [--stage layout] [--backend local] [--force]
    python filmstages.py costs <production>                        # cost ledger: per shot, per stage, total

A shot in production.json with "kind": "film" lists its stages, e.g.
    "stages": {"boards": "local", "layout": "unreal", "photoreal": "wan_vace", "facelock": false, "upscale": "4k"}
Each stage takes the previous stage's output plus the shot's settings. Its fingerprint (inputs + settings + model)
decides whether it must run again; outputs are versioned files on Drive:  shots/<stage>/<shot>_vN.<ext>,
indexed in shots/stages.json (so the keep policy holds the current and previous version of each).
Every run appends to docs/cost_ledger.jsonl (seconds, GPU, $ or credits).

Rules enforced here:
  * a shot with a minor in its cast may not use a hosted backend (Flow, Runway, Kling, ElevenLabs ...)
  * every model must be cleared in the licence register (licences.py)
  * the build never runs stages; it only uses the best existing output (see best()).
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402
import os, sys, json, time, shutil, hashlib, argparse, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
STAGES = ["boards", "layout", "photoreal", "facelock", "upscale"]
EXT = {"boards": "png", "layout": "mp4", "photoreal": "mp4", "facelock": "mp4", "upscale": "mp4"}
HOSTED = {"flow", "veo", "runway", "kling", "elevenlabs", "marble"}
MODELS = {"wan_vace": "wan2.2-i2v-a14b", "wan_animate": "wan-animate-2-14b", "wan_t2v": "wan2.2-t2v-a14b",
          "wan_fast": "wan2.2-ti2v-5b", "inswapper": "inswapper_128", "hyperswap": "hyperswap_1a/1b_256"}
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"

class NotReady(Exception): pass

def sha(x): return hashlib.sha1(json.dumps(x, sort_keys=True).encode()).hexdigest()[:12]
def fsig(p): st = os.stat(p); return f"{os.path.basename(p)}|{st.st_size}|{int(st.st_mtime)}"
def run(c): subprocess.run(c, check=True)
def esc(t): return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace(",", "\\,").replace("%", "\\%")

class Film:
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.P = json.load(open(os.path.join(self.root, "production.json")))
        cp = os.path.join(self.root, self.P.get("cast", "cast/cast.json"))
        self.cast = json.load(open(cp)).get("members", {}) if os.path.exists(cp) else {}
        self.fps = self.P["fps"]; self.W, self.H = self.P["size"]; self.MW, self.MH = self.P.get("master_size", self.P["size"])
        self.idx_path = os.path.join(self.root, "shots", "stages.json")
        self.idx = json.load(open(self.idx_path)) if os.path.exists(self.idx_path) else {}
        self.ledger = os.path.join(self.root, "docs", "cost_ledger.jsonl")
        self.shots = [s for s in self.P.get("shots", []) if s.get("kind") == "film"]

    def save(self):
        tmp = self.idx_path + ".tmp"; json.dump(self.idx, open(tmp, "w"), indent=1); os.replace(tmp, self.idx_path)

    def plan(self, s): return [st for st in STAGES if s.get("stages", {}).get(st)]

    def current(self, s, stage):
        v = self.idx.get(s["id"], {}).get(stage, [])
        v = [x for x in v if os.path.exists(os.path.join(self.root, x["file"]))]
        return v[-1] if v else None

    def best(self, s):
        """Latest stage that has an output (used by the build)."""
        for st in reversed(self.plan(s)):
            c = self.current(s, st)
            if c: return os.path.join(self.root, c["file"]), st
        return None, None

    def key(self, s, stage):
        i = self.plan(s).index(stage); prev = self.plan(s)[i - 1] if i else None
        pc = self.current(s, prev) if prev else None
        if prev and not pc: return None
        settings = {k: s.get(k) for k in ("id", "dur", "set", "cast", "camera", "action", "lines", "lens", "light")}
        backend = s["stages"][stage]
        idm = {m: (self.cast.get(m, {}) or {}).get("identity_model") for m in s.get("cast", [])} if stage in ("photoreal", "facelock") else {}
        return sha({"stage": stage, "backend": backend, "settings": settings, "prev": pc and pc["key"],
                    "idm": idm, "fps": self.fps, "size": [self.W, self.H], "master": [self.MW, self.MH] if stage == "upscale" else None})

    def check_rules(self, s, stage, backend):
        b = str(backend).lower()
        minors = [m for m in s.get("cast", []) if (self.cast.get(m) or {}).get("minor")]
        if minors and b in HOSTED:
            raise SystemExit(f"REFUSED {s['id']} {stage}: {', '.join(minors)} in shot; children only through local/open tools, never {b}")
        if b in MODELS:
            import licences; licences.require([MODELS[b]], self.root)

    def run_stage(self, s, stage, backend=None, force=False):
        backend = backend or s["stages"][stage]
        if stage == "upscale" and backend in (True, "4k"): backend = "local" if backend is True else backend
        self.check_rules(s, stage, backend)
        k = self.key(s, stage)
        if k is None: raise NotReady(f"{s['id']}: {stage} needs the previous stage first")
        cur = self.current(s, stage)
        if cur and cur["key"] == k and not force: return cur, False
        vers = self.idx.setdefault(s["id"], {}).setdefault(stage, [])
        n = max([x["v"] for x in vers] or [0]) + 1
        rel = os.path.join("shots", stage, f"{s['id']}_v{n}.{EXT[stage]}"); out = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True); tmp = out + ".tmp." + EXT[stage]
        t0 = time.time()
        fn = getattr(self, f"_{backend}", None) if backend == "local" or backend == "4k" else None
        if backend in ("local", "4k"):
            getattr(self, f"_local_{stage}")(s, tmp)
            cost = {"usd": 0.0, "placeholder": stage in ("layout", "photoreal", "facelock")}
        else:
            raise NotReady(BACKEND_HELP.get(backend, f"backend {backend!r} is not set up yet"))
        os.replace(tmp, out)
        e = {"v": n, "key": k, "file": rel, "backend": backend, "model": MODELS.get(backend), "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "seconds": round(time.time() - t0, 1), **cost}
        vers.append(e); self.save()
        os.makedirs(os.path.dirname(self.ledger), exist_ok=True)
        with open(self.ledger, "a") as f: f.write(json.dumps({"shot": s["id"], "stage": stage, **e}) + "\n")
        return e, True

    # local backends: real enough to test the whole chain, marked as placeholders where they stand in for GPU work
    def _local_boards(self, s, out):
        lines = [f"{s['id']}  ·  {s.get('set', '')}", s.get("camera", ""), s.get("action", "")[:90], "with: " + ", ".join(s.get("cast", []))]
        dts = ",".join(f"drawtext=fontfile={FONT}:text='{esc(t)}':fontcolor=white@0.85:fontsize={40 if i == 0 else 30}:x=60:y={80 + i*70}" for i, t in enumerate(lines) if t)
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x1b2230:s={self.W}x{self.H}", "-vf", dts, "-frames:v", "1", "-f", "image2", out])

    def _local_layout(self, s, out):
        src = os.path.join(self.root, self.current(s, "boards")["file"]); n = int(round(s["dur"] * self.fps))
        run(["ffmpeg", "-y", "-v", "error", "-framerate", str(self.fps), "-i", src, "-vf",
             f"format=yuv420p,loop=-1:1,trim=duration={s['dur']},zoompan=z='1+0.0008*on':d=1:s={self.W}x{self.H}:fps={self.fps}",
             "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-f", "mp4", out])

    def _copy_prev(self, s, stage, out):
        i = self.plan(s).index(stage); src = os.path.join(self.root, self.current(s, self.plan(s)[i - 1])["file"])
        shutil.copyfile(src, out)
    def _local_photoreal(self, s, out): self._copy_prev(s, "photoreal", out)
    def _local_facelock(self, s, out): self._copy_prev(s, "facelock", out)
    def _local_upscale(self, s, out):
        i = self.plan(s).index("upscale"); src = os.path.join(self.root, self.current(s, self.plan(s)[i - 1])["file"])
        run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", f"scale={self.MW}:{self.MH}:flags=lanczos", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", "-f", "mp4", out])

BACKEND_HELP = {
    "unreal": "Unreal layout needs the Epic account and the cloud Unreal render (tasks 0.6, 6.1-6.2)",
    "wan_vace": "Wan photoreal needs the RunPod worker (tasks 0.3-0.4, 2.1-2.5) and the identity models (3.3-3.7)",
    "wan_animate": "Wan Animate needs the RunPod worker (tasks 0.3-0.4, 2.1-2.5)",
    "wan_t2v": "Wan needs the RunPod worker (tasks 0.3-0.4, 2.1-2.5)",
    "wan_fast": "Wan needs the RunPod worker (tasks 0.3-0.4, 2.1-2.5)",
    "inswapper": "blocked by licence", "hyperswap": "blocked by licence",
    "flow": "Flow shots are generated by hand, then registered: catalog.py need -> generate -> catalog.py register, and put at shots/<stage>/<shot>_vN",
}

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    for c in ("status", "costs"): sp.add_parser(c).add_argument("root")
    r = sp.add_parser("run"); r.add_argument("root"); r.add_argument("--shots", default=""); r.add_argument("--stage"); r.add_argument("--backend"); r.add_argument("--force", action="store_true")
    a = ap.parse_args(); F = Film(a.root)
    if a.cmd == "status":
        for s in F.shots:
            cells = []
            for st in F.plan(s):
                c = F.current(s, st); k = F.key(s, st)
                cells.append(f"{st}:{'v'+str(c['v']) if c else '-'}{'' if not c or c['key'] == k else '*'}")
            print(f"{s['id']:10} " + "  ".join(cells))
        print("(* = out of date: an input changed since that version)")
    elif a.cmd == "run":
        want = set(x for x in a.shots.split(",") if x); done = skipped = 0; blocked = []
        for s in F.shots:
            if want and s["id"] not in want: continue
            for st in F.plan(s):
                if a.stage and st != a.stage: continue
                try:
                    e, ran = F.run_stage(s, st, a.backend, a.force); done += ran; skipped += not ran
                    if ran: print(f"{s['id']:10} {st:9} v{e['v']} ({e['backend']}, {e['seconds']}s)")
                except NotReady as x: blocked.append(f"{s['id']} {st}: {x}"); break
        print(f"ran {done}, up to date {skipped}, waiting {len(blocked)}")
        for b in blocked[:12]: print("  waiting:", b)
    elif a.cmd == "costs":
        rows = [json.loads(l) for l in open(F.ledger)] if os.path.exists(F.ledger) else []
        by = {}
        for r in rows:
            k = (r["shot"], r["stage"]); by.setdefault(k, [0, 0.0, 0.0]); by[k][0] += 1; by[k][1] += r.get("seconds", 0); by[k][2] += r.get("usd", 0) or 0
        for (sh, st), (n, sec, usd) in sorted(by.items()): print(f"{sh:10} {st:9} runs {n:2}  {sec/60:6.1f} min  ${usd:7.2f}")
        tot = sum(v[2] for v in by.values()); gpu = sum(r.get("seconds", 0) for r in rows if r.get("backend") not in ("local", "4k"))
        print(f"TOTAL ${tot:.2f} · GPU time {gpu/3600:.2f} h · {len(rows)} stage runs")

if __name__ == "__main__": main()
