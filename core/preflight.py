#!/usr/bin/env python3
"""Studio pre-flight check (build plan 11.3): is everything in place before a production run?

    python preflight.py [--for film|reel|series]

OK / WARN / FAIL per item, with the fix. Exit code 1 if anything FAILs.
"""
import os, sys, json, time, shutil, subprocess, stat
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
STUDIO = os.path.dirname(os.path.dirname(os.path.realpath(HERE)))
CFG = os.path.expanduser("~/.config/video-pipeline"); RT = os.path.expanduser("~/VideoStudioRuntime")
res = []
def r(level, item, msg, fix=""): res.append((level, item, msg, fix))
def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True)

def main():
    want = sys.argv[sys.argv.index("--for") + 1] if "--for" in sys.argv else "film"
    # tools
    for t in ("ffmpeg", "ffprobe", "git"): r("OK" if shutil.which(t) else "FAIL", t, "found" if shutil.which(t) else "missing", f"brew install {t}")
    vp = os.path.join(RT, "venv/bin/python")
    if os.path.exists(vp):
        miss = sh(f'"{vp}" -c "import numpy, cv2, onnxruntime, cairo"').returncode
        r("OK" if not miss else "FAIL", "python venv", "numpy, cv2, onnxruntime, cairo" if not miss else "a module is missing", "pip install into ~/VideoStudioRuntime/venv")
    else: r("FAIL", "python venv", "missing", "recreate ~/VideoStudioRuntime/venv")
    for m in ("models/clip-vit-b32", "models"):
        pass
    r("OK" if os.path.isdir(os.path.join(RT, "models/clip-vit-b32")) else "WARN", "catalog CLIP model", "present" if os.path.isdir(os.path.join(RT, "models/clip-vit-b32")) else "missing: picture search off")
    # storage
    free = shutil.disk_usage(os.path.expanduser("~")).free / 1e9
    r("OK" if free > 30 else "WARN" if free > 12 else "FAIL", "disk free", f"{free:.0f} GB", "run cleanup.py plan / free space")
    r("OK" if os.path.isdir(os.path.join(STUDIO, "04 Productions")) else "FAIL", "studio on Drive", STUDIO, "start Google Drive")
    # keys
    kp = os.path.join(CFG, "keys.json")
    if os.path.exists(kp):
        mode = stat.S_IMODE(os.stat(kp).st_mode); K = json.load(open(kp))
        r("OK" if mode == 0o600 else "FAIL", "keys.json permissions", oct(mode), "chmod 600 ~/.config/video-pipeline/keys.json")
        if os.path.realpath(kp).startswith(STUDIO): r("FAIL", "keys.json location", "inside the studio folder!", "move it out")
        need = {"film": ["runpod", "huggingface"], "reel": [], "series": []}[want]
        for k in ["runpod", "huggingface"]:
            r("OK" if K.get(k) else ("FAIL" if k in need else "WARN"), f"key: {k}", "present" if K.get(k) else "missing", "build plan 0.4 / 0.5")
    else: r("FAIL", "keys.json", "missing", "create ~/.config/video-pipeline/keys.json (chmod 600)")
    el = sh('security find-generic-password -s elevenlabs-api >/dev/null 2>&1 && echo y').stdout.strip() == "y"
    r("OK" if el else "WARN", "key: elevenlabs (Keychain)", "present" if el else "missing")
    # GPU guard rails
    g = os.path.join(CFG, "gpu.json")
    if os.path.exists(g):
        G = json.load(open(g)); r("OK", "GPU spend cap", f"${G.get('monthly_cap_usd')}/month, max ${G.get('max_hourly_usd')}/h, idle stop {G.get('idle_minutes')} min")
    else: r("WARN", "GPU spend cap", "no gpu.json: defaults ($50/month)", "create ~/.config/video-pipeline/gpu.json")
    # licences
    try:
        import licences; L = licences.load(); bad = [k for k, v in L.items() if v["status"] != "cleared"]
        r("OK", "model licences", f"{len(L)} models, {len(L) - len(bad)} cleared, {len(bad)} blocked/unverified")
    except Exception as e: r("FAIL", "model licences", str(e), "check 02 Libraries/_catalog/model_licences.json")
    # catalog freshness
    cp = os.path.join(STUDIO, "02 Libraries/_catalog/catalog.jsonl")
    if os.path.exists(cp):
        age = (time.time() - os.path.getmtime(cp)) / 86400
        r("OK" if age < 8 else "WARN", "asset catalog", f"updated {age:.1f} days ago", "catalog.py scan")
    else: r("FAIL", "asset catalog", "missing", "catalog.py scan")
    # Resolve
    res_ok = os.path.isdir("/Applications/DaVinci Resolve/DaVinci Resolve.app")
    running = sh("pgrep -f 'DaVinci Resolve.app/Contents/MacOS/Resolve'").returncode == 0
    r("OK" if res_ok else "FAIL", "DaVinci Resolve Studio", ("installed, running" if running else "installed, not running (start it for resolve_*.py)") if res_ok else "missing")
    # platform code
    st = sh(f'cd "{os.path.dirname(HERE)}" && git status --porcelain && git rev-list --count @{{u}}..HEAD 2>/dev/null')
    lines = [l for l in st.stdout.splitlines() if l.strip()]
    dirty = [l for l in lines if not l.strip().isdigit()]; ahead = next((int(l) for l in lines if l.strip().isdigit()), 0)
    r("OK" if not dirty and not ahead else "WARN", "platform code (git)", "committed and pushed" if not dirty and not ahead else f"{len(dirty)} uncommitted, {ahead} unpushed", "commit and push 00 Platform")
    # accounts that only Prakash can create
    if want == "film":
        for name, hint in (("Epic Games / Unreal", "0.6"), ("World Labs Marble", "0.7")):
            r("WARN", name, "not checkable from here; confirm the account exists", f"build plan {hint}")
    w = max(len(x[1]) for x in res)
    for lvl, item, msg, fix in res:
        print(f"{lvl:4}  {item:{w}}  {msg}{('   -> ' + fix) if lvl != 'OK' and fix else ''}")
    n = {k: sum(1 for x in res if x[0] == k) for k in ("OK", "WARN", "FAIL")}
    print(f"\n{n['OK']} ok, {n['WARN']} warnings, {n['FAIL']} failures ({want})")
    sys.exit(1 if n["FAIL"] else 0)

if __name__ == "__main__": main()
