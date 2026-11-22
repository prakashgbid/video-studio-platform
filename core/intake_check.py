"""Cast intake checker (build plan task 3.1).

    python intake_check.py <film_dir>            # checks every cast/<name>/
    python intake_check.py <film_dir> --person thea

For each person it checks photos, videos and voice against cast/INTAKE.md and writes
cast/<name>/_intake_report.md plus a one-line summary per person. Nothing is uploaded anywhere.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import os, sys, json, glob, argparse, subprocess, hashlib
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
import cv2, numpy as np

IMG = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff")
VID = (".mp4", ".mov", ".m4v", ".avi", ".mkv")
AUD = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")
NEED = {"photos": (40, 60), "videos": (3, 6), "voice_min_adult": 5.0, "voice_min_child": 2.0}
# YuNet face detector (OpenCV Zoo, MIT licence); kept in the local runtime, not on Drive
YUNET = os.path.expanduser("~/VideoStudioRuntime/models/face_detection_yunet_2023mar.onnx")

def detect(img):
    """Faces as (x, y, w, h, yaw) on img; yaw from eye/nose landmarks: ~0 frontal, |yaw| > 0.35 profile-ish."""
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(YUNET, "", (w, h), 0.7)
    _, f = det.detect(img)
    out = []
    for r in (f if f is not None else []):
        x, y, fw, fh = r[:4]; rex, rey, lex, ley, nx, ny = r[4:10]
        mid = (rex + lex) / 2; eye = max(abs(lex - rex), 1)
        out.append((x, y, fw, fh, float((nx - mid) / eye)))
    return out

def files(d, exts):
    return sorted(p for p in glob.glob(os.path.join(d, "**", "*"), recursive=True)
                  if p.lower().endswith(exts) and not os.path.basename(p).startswith("."))

def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height,codec_type",
                        "-of", "json", p], capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except Exception: return {}

def load_image(p):
    if p.lower().endswith(".heic"):   # macOS: convert with sips to a temp jpg
        tmp = f"/tmp/intake_{hashlib.md5(p.encode()).hexdigest()}.jpg"
        if not os.path.exists(tmp):
            subprocess.run(["sips", "-s", "format", "jpeg", p, "--out", tmp], capture_output=True)
        p = tmp
    return cv2.imread(p)

def dhash(img, n=8):
    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (n + 1, n))
    return int("".join("1" if b else "0" for b in (g[:, 1:] > g[:, :-1]).flatten()), 2)

def check_photo(p):
    img = load_image(p)
    if img is None: return {"file": p, "ok": False, "issues": ["cannot open"]}
    h, w = img.shape[:2]; issues = []
    if min(h, w) < 1024: issues.append(f"low resolution {w}x{h} (want shortest side >= 1024)")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (int(w * 1024 / max(h, w)), int(h * 1024 / max(h, w))))
    blur = cv2.Laplacian(small, cv2.CV_64F).var()
    if blur < 60: issues.append(f"blurry (sharpness {blur:.0f})")
    sc = 1024 / max(h, w)
    faces = detect(cv2.resize(img, (int(w * sc), int(h * sc))))
    kind = "none"; face_px = 0
    if faces:
        fx, fy, fw, fh, yaw = max(faces, key=lambda f: f[2] * f[3])
        face_px = int(fh / sc)
        kind = "profile" if abs(yaw) > 0.35 else ("three-quarter" if abs(yaw) > 0.15 else "front")
        if face_px < 160: kind = "none"   # a tiny face = full-body shot
        if face_px < 500 and kind != "none": issues.append(f"face small ({face_px}px tall; want >= 500 unless a full-body shot)")
    else:
        issues.append("no face found (fine for a full-body or back view)")
    return {"file": p, "ok": not [i for i in issues if not i.startswith("no face") and not i.startswith("face small")],
            "w": w, "h": h, "blur": round(blur), "faces": len(faces), "view": kind, "face_px": face_px,
            "issues": issues, "hash": dhash(img)}

def check_person(person_dir):
    name = os.path.basename(person_dir.rstrip("/"))
    # who is a minor comes from the film's own cast/cast.json (data, never hard-coded in the platform)
    cf = os.path.join(os.path.dirname(person_dir.rstrip("/")), "cast.json")
    members = json.load(open(cf)).get("members", {}) if os.path.exists(cf) else {}
    child = bool(members.get(name, {}).get("minor"))
    photos = [check_photo(p) for p in files(os.path.join(person_dir, "photos"), IMG)]
    # near-duplicates (burst shots)
    dups = []
    for i in range(len(photos)):
        for j in range(i + 1, len(photos)):
            if "hash" in photos[i] and "hash" in photos[j] and bin(photos[i]["hash"] ^ photos[j]["hash"]).count("1") <= 4:
                dups.append((os.path.basename(photos[i]["file"]), os.path.basename(photos[j]["file"])))
    vids = []
    for p in files(os.path.join(person_dir, "video"), VID):
        d = probe(p); st = [s for s in d.get("streams", []) if s.get("codec_type") == "video"]
        dur = float(d.get("format", {}).get("duration", 0)); w = st[0]["width"] if st else 0; h = st[0]["height"] if st else 0
        iss = []
        if min(w, h) < 1080 and max(w, h) < 1920: iss.append(f"below 1080p ({w}x{h})")
        if not 8 <= dur <= 90: iss.append(f"length {dur:.0f}s (want 10-60s)")
        vids.append({"file": p, "dur": round(dur, 1), "w": w, "h": h, "issues": iss})
    voice = []
    for p in files(os.path.join(person_dir, "voice"), AUD + VID):
        d = probe(p); voice.append({"file": p, "dur": float(d.get("format", {}).get("duration", 0))})
    vmin = sum(v["dur"] for v in voice) / 60
    good = [p for p in photos if p["ok"]]
    views = {k: sum(1 for p in good if p.get("view") == k) for k in ("front", "three-quarter", "profile", "none")}
    need_v = NEED["voice_min_child"] if child else NEED["voice_min_adult"]
    todo = []
    if len(good) < NEED["photos"][0]: todo.append(f"add {NEED['photos'][0] - len(good)} more usable photos")
    if views["three-quarter"] < 8: todo.append("add more three-quarter (half-turned) photos")
    if views["profile"] < 4: todo.append("add a few side-profile photos")
    if views["none"] < 6: todo.append("add 6-10 full-body photos")
    if len(vids) < NEED["videos"][0]: todo.append(f"add {NEED['videos'][0] - len(vids)} more video clips")
    if vmin < need_v: todo.append(f"add {need_v - vmin:.1f} more minutes of clean voice")
    if dups: todo.append(f"{len(dups)} near-duplicate photo pairs (bursts) count once")
    status = "READY" if not todo else "NEEDS MORE"
    lines = [f"# Intake report: {name}", "", f"**{status}**", "",
             f"- Photos: {len(photos)} found, {len(good)} usable (front {views['front']}, three-quarter {views['three-quarter']}, profile {views['profile']}, full-body/other {views['none']})",
             f"- Videos: {len(vids)} ({sum(v['dur'] for v in vids):.0f}s total)",
             f"- Voice: {vmin:.1f} min (need {need_v:.0f}+)", ""]
    if todo: lines += ["## To do"] + [f"- {t}" for t in todo] + [""]
    bad = [p for p in photos if p["issues"]]
    if bad: lines += ["## Photo notes"] + [f"- {os.path.basename(p['file'])}: {'; '.join(p['issues'])}" for p in bad] + [""]
    vb = [v for v in vids if v["issues"]]
    if vb: lines += ["## Video notes"] + [f"- {os.path.basename(v['file'])}: {'; '.join(v['issues'])}" for v in vb] + [""]
    if dups: lines += ["## Near-duplicates"] + [f"- {a} = {b}" for a, b in dups] + [""]
    open(os.path.join(person_dir, "_intake_report.md"), "w").write("\n".join(lines))
    return name, status, todo

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("film"); ap.add_argument("--person")
    a = ap.parse_args(); cast = os.path.join(a.film, "cast")
    people = [a.person] if a.person else sorted(d for d in os.listdir(cast) if os.path.isdir(os.path.join(cast, d)) and not d.startswith(("_", ".")))
    for p in people:
        n, s, t = check_person(os.path.join(cast, p))
        print(f"{n:8} {s:10} {'; '.join(t)}")
