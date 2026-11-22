"""Rebuild ~/Movies/video-pipeline: a LOCAL folder of shortcuts that maps the old pre-restructure paths onto the
numbered studio layout on Google Drive, so old Resolve projects, skills and scripts keep working.

    python compat_links.py            # safe to run any time; touches only ~/Movies/video-pipeline
"""
import os, shutil
HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))            # .../14 Video Studio
COMPAT = os.path.expanduser("~/Movies/video-pipeline")
def link(rel_compat, rel_root):
    dst = os.path.join(COMPAT, rel_compat); src = os.path.join(ROOT, rel_root)
    if not os.path.exists(src): return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.islink(dst): os.remove(dst)
    os.symlink(src, dst)
def build():
    if os.path.islink(COMPAT): os.remove(COMPAT)
    elif os.path.isdir(COMPAT):
        # only ever contains shortcuts; refuse to touch real files
        for r, ds, fs in os.walk(COMPAT):
            for f in fs + ds:
                p = os.path.join(r, f)
                if not os.path.islink(p) and not os.path.isdir(p): raise SystemExit(f"real file in compat folder: {p}")
        shutil.rmtree(COMPAT)
    os.makedirs(COMPAT)
    for top in os.listdir(ROOT):
        if top[:2].isdigit(): link(top, top)                  # 00 Platform, 01 Brand, ...
    link("studio", "")
    link("engine", "00 Platform/core"); link("tools", "00 Platform/tools")
    link("README.md", "00 Platform/docs/README.md"); link("flow-shot-bible.md", "00 Platform/docs/flow-shot-bible.md")
    link("projects", "04 Productions")
    for f in os.listdir(os.path.join(ROOT, "04 Productions")):          # films (F01, F02 ...) also under films/
        if f[:1] == "F" and f[1:3].isdigit(): link(f"films/{f}", f"04 Productions/{f}")
    link("catalog", "02 Libraries/_catalog"); link("refs", "01 Brand/studio-angles")
    link("_archive", "90 Archive"); link("_tests", "90 Archive/tests")
    link("assets/broll", "02 Libraries/stock"); link("assets/source", "01 Brand/source")
    link("assets/voice_tests", "90 Archive/voice_tests")
    for f in os.listdir(os.path.join(ROOT, "01 Brand")):
        if os.path.isfile(os.path.join(ROOT, "01 Brand", f)): link(f"assets/{f}", f"01 Brand/{f}")
    A = "03 Series/AGENTIC"
    for f in os.listdir(os.path.join(ROOT, A)):
        if f != "E00 Trailer": link(f"series/agentic/{f}", f"{A}/{f}")
    link("series/agentic/trailer", f"{A}/E00 Trailer")
    for f in ("plates", "sfx", "music", "LICENCES.csv"): link(f"series/agentic/library/{f}", f"02 Libraries/{f}")
    print("compat folder rebuilt:", COMPAT)
if __name__ == "__main__": build()
