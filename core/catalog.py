"""Studio asset catalog (build plan 1.13-1.15): one searchable record per file in the studio.

    python catalog.py scan                  # incremental: hashes, media info, registry links, tags, visual embeddings
    python catalog.py search "rainy street at night" [--kind video] [--top 10]
    python catalog.py like <file>           # visually similar assets
    python catalog.py need "<what you need>" --kind video [--for <production>]
                                            # reuse-first gate: search, log, and say REUSE / CHECK STOCK / GENERATE
    python catalog.py register <file> --ticket n_xxxxxx --origin "Google Flow" --model veo-3 --prompt "..." [--genid ID] [--cost 0.40]
                                            # record a NEW generation; refused without a ticket from `need`
    python catalog.py approve <file>        # mark an approved master (kept forever by the keep policy)
    python catalog.py gate <production>     # every generated asset the production uses must be registered
    python catalog.py dupes                 # identical files (same checksum)
    python catalog.py show <id|path>        # one record
    python catalog.py stats

Source of truth (on Drive, plain text): catalog/catalog.jsonl, catalog/searches.jsonl, catalog/deletions.jsonl.
Rebuildable runtime (local only): ~/VideoStudioRuntime/catalog/ (hash cache, CLIP embeddings).
Nothing leaves this Mac. CLIP ViT-B/32 (MIT licence) runs locally with onnxruntime.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import os, sys, re, json, time, hashlib, argparse, subprocess, concurrent.futures as cf
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(os.path.dirname(os.path.realpath(HERE)))   # .../14 Video Studio
CAT = os.path.join(STUDIO, "02 Libraries", "_catalog")
RECORDS = os.path.join(CAT, "catalog.jsonl")
SEARCHES = os.path.join(CAT, "searches.jsonl")
DELETIONS = os.path.join(CAT, "deletions.jsonl")
GENS = os.path.join(CAT, "generations.jsonl")        # one line per registered generation (by checksum)
APPROVALS = os.path.join(CAT, "approvals.jsonl")     # approved masters (by checksum)
GATE_START = "2026-11-22"                             # generated media older than this is grandfathered
GENERATED = ("Google Flow", "ElevenLabs", "GPU worker", "Marble", "Kokoro")
RUNTIME = os.environ.get("VIDEO_RUNTIME", os.path.expanduser("~/VideoStudioRuntime"))
RT = os.path.join(RUNTIME, "catalog"); os.makedirs(RT, exist_ok=True); os.makedirs(CAT, exist_ok=True)
HASHCACHE = os.path.join(RT, "hashcache.json")
EMB = os.path.join(RT, "embeddings.npz")
CLIP = os.path.join(RUNTIME, "models", "clip-vit-b32")

SKIP_DIRS = {".git", "__pycache__", "_catalog", "node_modules", ".venv", "cache", ".cache"}
KINDS = {
    "video": (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"),
    "image": (".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff", ".gif"),
    "audio": (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"),
    "model": (".safetensors", ".onnx", ".pt", ".ckpt", ".bin", ".gguf"),
    "project": (".drp", ".json", ".fcpxml", ".edl", ".otio"),
    "doc": (".md", ".txt", ".srt", ".vtt", ".html", ".pdf", ".csv", ".docx"),
    "code": (".py", ".sh", ".js", ".css"),
}
MEDIA = ("video", "image", "audio")
STOP = set("the a an of and or to in on at for with by from is are mp4 mov jpg png mp3 wav m4a json md v1 v2 v3 v4 v5 v6 take raw media file final build".split())

def kind_of(p):
    e = os.path.splitext(p)[1].lower()
    for k, exts in KINDS.items():
        if e in exts: return k
    return "other"

def rel(p): return os.path.relpath(os.path.realpath(p), STUDIO)

def walk():
    for root, dirs, files in os.walk(STUDIO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if f.startswith(".") or f.endswith((".pyc", ".log")): continue
            yield os.path.join(root, f)

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,channels,sample_rate",
                        "-of", "json", p], capture_output=True, text=True)
    try: d = json.loads(r.stdout)
    except Exception: return {}
    out = {"duration": round(float(d.get("format", {}).get("duration", 0) or 0), 2)}
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and "width" not in out:
            out.update(width=s.get("width"), height=s.get("height"), vcodec=s.get("codec_name"))
            try: n, dn = s.get("avg_frame_rate", "0/1").split("/"); out["fps"] = round(int(n) / int(dn), 3) if int(dn) else None
            except Exception: pass
        if s.get("codec_type") == "audio" and "acodec" not in out:
            out.update(acodec=s.get("codec_name"), channels=s.get("channels"), sample_rate=s.get("sample_rate"))
    return out

# ---------------------------------------------------------------- registries: what each production says about its files
def registries():
    """path(rel) -> list of {registry, key, origin, line, prompt, credits, licence, tags, locked, page, author}"""
    links = {}
    def add(path, info):
        if os.path.exists(path): links.setdefault(rel(path), []).append(info)
    for p in walk():
        b = os.path.basename(p); d = os.path.dirname(p)
        if not b.endswith(".json"): continue
        try: data = json.load(open(p))
        except Exception: continue
        reg = rel(p)
        if b == "meta.json" and isinstance(data, dict) and data.get("file"):        # stock b-roll
            add(os.path.join(d, data["file"]), {"registry": reg, "origin": f"{data.get('provider','stock')} stock",
                "licence": data.get("license"), "page": data.get("page"), "author": data.get("author"),
                "tags": data.get("tags", []), "locked": True})
            continue
        def visit(node, key=None, ctx=None):
            if isinstance(node, dict):
                c = {k: node.get(k) for k in ("origin", "line", "prompt", "credits_flow", "credits_el", "locked", "license", "licence") if k in node}
                for k, v in node.items(): visit(v, k if isinstance(v, (dict, list)) else key, c or ctx)
                if isinstance(node.get("path"), str): hit(node["path"], key, c)
            elif isinstance(node, list):
                for v in node: visit(v, key, ctx)
            elif isinstance(node, str) and 3 < len(node) < 300 and os.path.splitext(node)[1].lower() in sum(KINDS.values(), ()):
                hit(node, key, ctx or {})
        def hit(s, key, c):
            s = os.path.expanduser(s)
            for base in (d, STUDIO):
                cand = os.path.realpath(os.path.normpath(os.path.join(base, s)))
                if os.path.isfile(cand):
                    info = {"registry": reg, "key": key}
                    info.update({k: v for k, v in (c or {}).items() if v not in (None, "")})
                    add(cand, info); break
        visit(data)
    return links

def code_refs():
    """Asset names the platform code loads by convention (e.g. assets/watermark_{aspect}.png) -> regexes."""
    pats = []
    for f in os.listdir(HERE):
        if not f.endswith((".py", ".sh")) or f in ("catalog.py", "cleanup.py"): continue
        txt = open(os.path.join(HERE, f), errors="ignore").read()
        for lit in re.findall(r"[\"']([^\"'\n]{3,120}\.(?:mp4|mov|png|jpg|jpeg|mp3|wav|m4a|json))[\"']", txt):
            pats.append(re.compile(re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(os.path.basename(lit))) + "$"))
    return pats

def prompt_beside(p):
    """A scene folder's prompt.md / vo_script.md describes the media in it."""
    d = os.path.dirname(p); txt = []
    for n in ("prompt.md", "vo_script.md"):
        f = os.path.join(d, n)
        if os.path.exists(f):
            try: txt.append(open(f).read()[:600])
            except Exception: pass
    return " ".join(txt).strip()

def role_of(r):
    parts = r.split(os.sep)
    if parts[0] == "90 Archive": return "test" if len(parts) > 1 and parts[1] == "tests" else "archive"
    if parts[0] == "01 Brand": return "brand"
    if parts[0] == "00 Platform": return "platform"
    if "_search" in parts or r.endswith(".t.jpg"): return "preview"
    if "final" in parts: return "final"
    if "build" in parts: return "derived"
    return "source"

def production_of(r):
    parts = r.split(os.sep)
    if parts[0] == "04 Productions" and len(parts) > 2: return "/".join(parts[:2])
    if parts[0] == "03 Series" and len(parts) > 3: return "/".join(parts[:3])
    if parts[0] == "03 Series" and len(parts) > 2: return "/".join(parts[:2])
    return None

def tags_of(r, extra=()):
    words = re.split(r"[^a-z0-9]+", r.lower().replace("pexels-video-", " "))
    t = [w for w in words if len(w) > 2 and w not in STOP and not re.fullmatch(r"[0-9a-f]{6,}|\d+", w)]
    t += [x.lower() for x in extra if x]
    out = []
    for w in t:
        if w not in out: out.append(w)
    return out[:40]

def origin_guess(r, k):
    if "/voices/" in f"/{r}" and k == "audio": return "voice (Kokoro or ElevenLabs)"
    if f"/{r}".startswith("/02 Libraries/sfx/"): return "ElevenLabs SFX"
    if f"/{r}".startswith("/02 Libraries/music/") or "/music/" in f"/{r}": return "ElevenLabs Music"
    if re.search(r"/scenes/[^/]+/(take|raw)_[ab]\.mp3$", "/" + r): return "ElevenLabs voice take"
    if "/shots/raw/" in "/" + r: return "Google Flow"
    if "/build/" in "/" + r or "/final/" in "/" + r: return "own render (build engine)"
    return None

# ---------------------------------------------------------------- CLIP (local)
class Clip:
    def __init__(self):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        o = ort.SessionOptions(); o.log_severity_level = 3
        self.v = ort.InferenceSession(os.path.join(CLIP, "vision_model_quantized.onnx"), o, providers=["CPUExecutionProvider"])
        self.t = ort.InferenceSession(os.path.join(CLIP, "text_model_quantized.onnx"), o, providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(os.path.join(CLIP, "tokenizer.json"))
        self.mean = np.array([0.48145466, 0.4578275, 0.40821073], np.float32); self.std = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
    def image(self, rgb):                       # HxWx3 uint8
        import cv2
        h, w = rgb.shape[:2]; s = 224 / min(h, w)
        im = cv2.resize(rgb, (max(224, round(w * s)), max(224, round(h * s))), interpolation=cv2.INTER_CUBIC)
        y, x = (im.shape[0] - 224) // 2, (im.shape[1] - 224) // 2
        im = (im[y:y + 224, x:x + 224].astype(np.float32) / 255 - self.mean) / self.std
        e = self.v.run(None, {"pixel_values": im.transpose(2, 0, 1)[None]})[0][0]
        return e / np.linalg.norm(e)
    def text(self, s):
        ids = self.tok.encode(s).ids[:77]
        e = self.t.run(None, {"input_ids": np.array([ids], np.int64)})[0][0]
        return e / np.linalg.norm(e)

def frame_of(p, k, dur):
    import cv2
    if k == "image":
        if p.lower().endswith(".heic"): return None
        im = cv2.imread(p); return None if im is None else cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    t = max(0.0, (dur or 0) * 0.4)
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", p, "-frames:v", "1", "-vf", "scale=448:-2",
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    if not r.stdout: return None
    im = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
    return None if im is None else cv2.cvtColor(im, cv2.COLOR_BGR2RGB)

def load_emb():
    if os.path.exists(EMB):
        z = np.load(EMB, allow_pickle=False); return dict(zip(z["ids"].tolist(), z["vecs"]))
    return {}

def save_emb(d):
    ids = sorted(d); np.savez(EMB, ids=np.array(ids), vecs=np.array([d[i] for i in ids], np.float32) if ids else np.zeros((0, 512), np.float32))

# ---------------------------------------------------------------- records
def load():
    if not os.path.exists(RECORDS): return {}
    out = {}
    for line in open(RECORDS):
        line = line.strip()
        if line: r = json.loads(line); out[r["path"]] = r
    return out

def save(recs):
    tmp = RECORDS + ".tmp"
    with open(tmp, "w") as f:
        for k in sorted(recs): f.write(json.dumps(recs[k], ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, RECORDS)

def jsonl(p):
    if not os.path.exists(p): return []
    return [json.loads(l) for l in open(p) if l.strip()]

def append(p, entry):
    with open(p, "a") as f: f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def scan(embed=True, quiet=False, prefix=None):
    """prefix: rescan only that studio-relative folder (fast, used after every build); other records are untouched."""
    t0 = time.time(); old = load()
    hc = json.load(open(HASHCACHE)) if os.path.exists(HASHCACHE) else {}
    paths = [p for p in walk() if not prefix or rel(p).startswith(prefix)]
    gens = {}
    for g in jsonl(GENS): gens[g["sha256"]] = g
    approved = {a["sha256"]: a for a in jsonl(APPROVALS)}
    links = registries()
    def one(p):
        r = rel(p); st = os.stat(p); key = f"{r}|{st.st_size}|{int(st.st_mtime)}"
        h = hc.get(key) or sha256(p)
        k = kind_of(p)
        rec = dict(old.get(r, {}))
        rec.update(path=r, kind=k, ext=os.path.splitext(p)[1].lower(), size=st.st_size, sha256=h,
                   mtime=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                   role=role_of(r), production=production_of(r))
        rec["id"] = "a_" + h[:12]
        if k in ("video", "audio") and (rec.get("_probed") != key):
            rec["media"] = probe(p); rec["_probed"] = key
        elif k == "image" and rec.get("_probed") != key:
            try:
                import cv2; im = cv2.imread(p)
                rec["media"] = {"width": im.shape[1], "height": im.shape[0]} if im is not None else {}
            except Exception: rec["media"] = {}
            rec["_probed"] = key
        L = links.get(r, [])
        rec["used_by"] = sorted({f"{l['registry']}#{l.get('key')}" for l in L})
        info = {}
        for l in L:
            for f in ("origin", "line", "prompt", "licence", "license", "page", "author", "credits_flow", "credits_el", "locked"):
                if l.get(f) not in (None, "") and f not in info: info[f] = l[f]
        if "license" in info: info.setdefault("licence", info.pop("license"))
        info.setdefault("origin", origin_guess(r, k))
        if k in MEDIA and not info.get("prompt"):
            pb = prompt_beside(p)
            if pb: info["prompt"] = pb
        rec["source"] = {k2: v for k2, v in info.items() if v not in (None, "")}
        g = gens.get(h)
        if g:
            rec["source"].update({k2: g[k2] for k2 in ("origin", "model", "prompt", "genid", "cost", "credits", "ticket", "platform_url") if g.get(k2) not in (None, "")})
            rec["registered"] = g["time"]
        if h in approved: rec["keep"] = "master"; rec["approved"] = approved[h]["time"]
        extra = sum([l.get("tags", []) for l in L], [])
        rec["tags"] = tags_of(r, extra)
        desc = " ".join(x for x in (info.get("line"), info.get("prompt")) if x)
        rec["description"] = re.sub(r"\s+", " ", desc)[:400] if desc else None
        rec["status"] = "active"; rec.pop("deleted_at", None); rec.pop("restore_until", None)
        return r, rec, key, h
    recs = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for r, rec, key, h in ex.map(one, paths):
            recs[r] = rec; hc[key] = h
    # files that vanished since the last scan: keep the record, marked missing/deleted
    for r, rec in old.items():
        if prefix and not r.startswith(prefix) and r not in recs: recs[r] = rec; continue
        if r not in recs:
            if rec.get("status") not in ("deleted", "missing"): rec["status"] = "missing"
            recs[r] = rec
    # duplicates
    by = {}
    for r, rec in recs.items():
        if rec.get("status") == "active" and rec["kind"] in MEDIA + ("model",): by.setdefault(rec["sha256"], []).append(r)
    for r, rec in recs.items(): rec.pop("dup_of", None)
    for h, rs in by.items():
        if len(rs) > 1:
            # keep: a referenced copy, then an original (raw/source folders), then a source-role file, then the shorter path
            keep = sorted(rs, key=lambda x: (not recs[x].get("used_by"), not re.search(r"/(raw|source)/", "/" + x),
                                             recs[x]["role"] != "source", len(x)))[0]
            for x in rs:
                if x != keep: recs[x]["dup_of"] = keep
    json.dump(hc, open(HASHCACHE, "w"))
    save(recs)
    n_emb = 0
    if embed:
        E = load_emb(); clip = None
        todo = [rec for rec in recs.values() if rec.get("status") == "active" and rec["kind"] in ("video", "image")
                and rec["id"] not in E and rec.get("role") != "derived" and rec["size"] > 2000]
        if todo:
            clip = Clip()
            def fr(rec): return rec, frame_of(os.path.join(STUDIO, rec["path"]), rec["kind"], (rec.get("media") or {}).get("duration"))
            with cf.ThreadPoolExecutor(4) as ex:
                for rec, im in ex.map(fr, todo):
                    if im is not None:
                        E[rec["id"]] = clip.image(im); n_emb += 1
            save_emb(E)
    if not quiet:
        act = [r for r in recs.values() if r.get("status") == "active"]
        print(f"catalog: {len(act)} files ({sum(r['size'] for r in act)/1e9:.2f} GB), "
              f"{sum(1 for r in act if r.get('used_by'))} linked to a production, "
              f"{sum(1 for r in act if r.get('dup_of'))} duplicates, {n_emb} new visual embeddings, {time.time()-t0:.0f}s")
    return recs

# ---------------------------------------------------------------- search
def _kw(q, rec):
    qs = [w for w in re.split(r"[^a-z0-9]+", q.lower()) if len(w) > 2 and w not in STOP]
    if not qs: return 0.0
    hay = " ".join([" ".join(rec.get("tags", [])), rec.get("description") or "", rec["path"].lower(),
                    (rec.get("source") or {}).get("origin") or ""]).lower()
    return sum(1 for w in qs if w in hay) / len(qs)

def search(q, kind=None, top=10, include_derived=False, quiet=False):
    recs = [r for r in load().values() if r.get("status") == "active" and not r.get("dup_of")
            and (kind is None or r["kind"] == kind) and (include_derived or r["role"] not in ("derived", "preview"))
            and r["kind"] in MEDIA + ("model",)]
    E = load_emb(); tv = None
    if any(r["id"] in E for r in recs):
        try: tv = Clip().text(q)
        except Exception: tv = None
    scored = []
    for r in recs:
        kw = _kw(q, r); vis = 0.0
        if tv is not None and r["id"] in E:
            vis = float(np.clip((float(E[r["id"]] @ tv) - 0.19) / 0.12, 0, 1))
        s = max(kw, vis) * 0.7 + min(kw, vis) * 0.3
        if s > 0.05: scored.append((round(s, 3), round(kw, 2), round(vis, 2), r))
    scored.sort(key=lambda x: -x[0])
    if not quiet:
        for s, kw, vis, r in scored[:top]:
            m = r.get("media") or {}
            dim = f"{m.get('width')}x{m.get('height')}" if m.get("width") else ""
            dur = f"{m.get('duration')}s" if m.get("duration") else ""
            print(f"{s:.2f}  (words {kw:.2f}, picture {vis:.2f})  {r['kind']:5} {dim:9} {dur:7} {r['path']}")
            if r.get("description"): print(f"       {r['description'][:110]}")
    return scored[:top]

def like(path, top=10):
    recs = load(); r0 = recs.get(rel(path)); E = load_emb()
    if not r0 or r0["id"] not in E: sys.exit("not catalogued or no picture embedding (run scan)")
    v = E[r0["id"]]; out = []
    for r in recs.values():
        if r.get("status") == "active" and r["id"] in E and r["path"] != r0["path"] and not r.get("dup_of"):
            out.append((float(E[r["id"]] @ v), r))
    out.sort(key=lambda x: -x[0])
    for s, r in out[:top]: print(f"{s:.2f}  {r['kind']:5} {r['path']}")

def need(q, kind=None, production=None):
    """Reuse-first gate: every new generation must be preceded by this search; the log is checked by the build."""
    hits = search(q, kind, top=5, quiet=True)
    best = hits[0][0] if hits else 0.0
    verdict = "REUSE" if best >= 0.75 else ("REVIEW CANDIDATES" if best >= 0.45 else "CHECK STOCK, THEN GENERATE")
    ticket = "n_" + hashlib.sha1(f"{time.time()}{q}".encode()).hexdigest()[:6]
    entry = {"ticket": ticket, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "query": q, "kind": kind, "production": production,
             "verdict": verdict, "best": best, "candidates": [r["path"] for s, _, _, r in hits]}
    with open(SEARCHES, "a") as f: f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"{verdict}  (best match {best:.2f})   ticket {ticket}")
    for s, kw, vis, r in hits: print(f"  {s:.2f}  {r['path']}")
    return entry

def register(path, ticket, origin, model=None, prompt=None, genid=None, cost=None, credits=None, url=None, override=None):
    """Record a new generation. Refused unless a reuse-first search (`need`) ran first and did not say REUSE."""
    full = os.path.realpath(path)
    if not os.path.exists(full): sys.exit(f"no such file: {path}")
    T = {e.get("ticket"): e for e in jsonl(SEARCHES)}
    t = T.get(ticket)
    if not t: sys.exit(f"REFUSED: ticket {ticket!r} not found. Run `catalog.py need \"<what you need>\" --kind ... --for ...` first.")
    age = time.time() - time.mktime(time.strptime(t["time"], "%Y-%m-%dT%H:%M:%S"))
    if age > 14 * 86400: sys.exit(f"REFUSED: ticket {ticket} is {age/86400:.0f} days old; search again, the library has changed.")
    if t["verdict"] == "REUSE" and not override:
        sys.exit(f"REFUSED: that search found a reusable asset ({t['candidates'][0]}). Use it, or pass --override \"<why it won't do>\".")
    h = sha256(full)
    g = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "sha256": h, "path": rel(full), "ticket": ticket, "query": t["query"],
         "production": t.get("production"), "origin": origin, "model": model, "prompt": prompt, "genid": genid,
         "cost": cost, "credits": credits, "platform_url": url, "override": override}
    append(GENS, {k: v for k, v in g.items() if v not in (None, "")})
    scan(embed=False, quiet=True, prefix=os.path.dirname(rel(full)) + os.sep)
    print(f"registered {rel(full)}  ({origin}{', ' + model if model else ''})")

def approve(path, note=None):
    full = os.path.realpath(path)
    if not os.path.exists(full): sys.exit(f"no such file: {path}")
    append(APPROVALS, {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "sha256": sha256(full), "path": rel(full), "note": note})
    scan(embed=False, quiet=True, prefix=os.path.dirname(rel(full)) + os.sep)
    print(f"approved master: {rel(full)} (kept forever)")

def gate(root, quiet=False):
    """Every generated media file a production uses must be registered (made after a reuse-first search).
    Returns the list of violations; files dated before GATE_START are grandfathered."""
    root = os.path.realpath(root)
    try: A = json.load(open(os.path.join(root, "assets.json")))
    except FileNotFoundError: return []
    recs = load(); by_sha = {}
    for r in recs.values(): by_sha.setdefault(r.get("sha256"), r)
    regs = {g["sha256"] for g in jsonl(GENS)}
    bad = []
    for aid, a in A.items():
        p = a.get("path") if isinstance(a, dict) else None
        if not p: continue
        full = p if os.path.isabs(p) else os.path.normpath(os.path.join(root, p))
        full = os.path.realpath(os.path.expanduser(full))
        if not os.path.isfile(full) or kind_of(full) not in MEDIA: continue
        r = rel(full); rec = recs.get(r)
        origin = (rec or {}).get("source", {}).get("origin") or origin_guess(r, kind_of(full)) or ""
        if not any(origin.startswith(o) for o in GENERATED): continue
        if time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(full))) < GATE_START: continue
        h = (rec or {}).get("sha256") or sha256(full)
        if h not in regs: bad.append((aid, r, origin))
    if not quiet:
        for aid, r, o in bad: print(f"  UNREGISTERED GENERATION  {aid}: {r}  ({o})")
    return bad

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("scan"); s.add_argument("--no-embed", action="store_true")
    s = sp.add_parser("search"); s.add_argument("q"); s.add_argument("--kind"); s.add_argument("--top", type=int, default=10); s.add_argument("--all", action="store_true")
    s = sp.add_parser("like"); s.add_argument("path"); s.add_argument("--top", type=int, default=10)
    s = sp.add_parser("need"); s.add_argument("q"); s.add_argument("--kind"); s.add_argument("--for", dest="prod")
    s = sp.add_parser("register"); s.add_argument("path"); s.add_argument("--ticket", required=True); s.add_argument("--origin", required=True)
    for o in ("model", "prompt", "genid", "url", "override"): s.add_argument("--" + o)
    s.add_argument("--cost", type=float); s.add_argument("--credits", type=float)
    s = sp.add_parser("approve"); s.add_argument("path"); s.add_argument("--note")
    s = sp.add_parser("gate"); s.add_argument("root")
    sp.add_parser("dupes"); sp.add_parser("stats")
    s = sp.add_parser("show"); s.add_argument("x")
    a = ap.parse_args()
    if a.cmd == "scan": scan(embed=not a.no_embed)
    elif a.cmd == "search": search(a.q, a.kind, a.top, include_derived=a.all)
    elif a.cmd == "like": like(a.path, a.top)
    elif a.cmd == "need": need(a.q, a.kind, a.prod)
    elif a.cmd == "register": register(a.path, a.ticket, a.origin, a.model, a.prompt, a.genid, a.cost, a.credits, a.url, a.override)
    elif a.cmd == "approve": approve(a.path, a.note)
    elif a.cmd == "gate":
        bad = gate(a.root); print("gate: OK" if not bad else f"gate: {len(bad)} unregistered generation(s)"); sys.exit(1 if bad else 0)
    elif a.cmd == "dupes":
        recs = load(); g = {}
        for r in recs.values():
            if r.get("dup_of"): g.setdefault(r["dup_of"], []).append(r)
        tot = 0
        for k, rs in sorted(g.items()):
            print(k); [print(f"   = {r['path']}") for r in rs]; tot += sum(r["size"] for r in rs)
        print(f"{sum(len(v) for v in g.values())} duplicate copies, {tot/1e6:.0f} MB")
    elif a.cmd == "stats":
        recs = [r for r in load().values() if r.get("status") == "active"]; t = {}
        for r in recs: t.setdefault((r["role"], r["kind"]), [0, 0]); t[(r["role"], r["kind"])][0] += 1; t[(r["role"], r["kind"])][1] += r["size"]
        for (ro, k), (n, b) in sorted(t.items(), key=lambda x: -x[1][1]): print(f"{ro:10} {k:8} {n:5} files {b/1e6:9.1f} MB")
    elif a.cmd == "show":
        recs = load(); r = recs.get(a.x) or recs.get(rel(a.x)) or next((v for v in recs.values() if v["id"] == a.x), None)
        print(json.dumps(r, indent=1, ensure_ascii=False) if r else "not found")

if __name__ == "__main__": main()
