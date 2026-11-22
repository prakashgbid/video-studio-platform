#!/usr/bin/env python3
"""Start a new film from the template (build plan 1.3).

    python new_film.py F03 "The Title" [--format short|feature|episode]

Creates "04 Productions/<CODE>-<slug>/" from 00 Platform/templates/film, fills in the title, code and format,
and indexes it in the catalog. Refuses to overwrite an existing production.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402
import os, re, sys, shutil, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(os.path.dirname(os.path.realpath(HERE)))
TPL = os.path.join(STUDIO, "00 Platform", "templates", "film")

def slug(t): return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:40]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("code"); ap.add_argument("title")
    ap.add_argument("--format", default="short", choices=["short", "feature", "episode"])
    a = ap.parse_args()
    if not re.fullmatch(r"F\d\d", a.code): sys.exit("code must look like F03")
    if any(d.startswith(a.code + "-") for d in os.listdir(os.path.join(STUDIO, "04 Productions"))):
        sys.exit(f"{a.code} already exists in 04 Productions")
    dst = os.path.join(STUDIO, "04 Productions", f"{a.code}-{slug(a.title)}")
    shutil.copytree(TPL, dst)
    for root, _, files in os.walk(dst):
        for f in files:
            if f.endswith((".json", ".md")):
                p = os.path.join(root, f); s = open(p).read()
                s = s.replace("{{TITLE}}", a.title).replace("{{CODE}}", a.code).replace("{{FORMAT}}", a.format)
                open(p, "w").write(s)
    try:
        import catalog as C; C.scan(embed=False, quiet=True, prefix=C.rel(dst) + os.sep)
    except Exception as e: print("catalog index skipped:", e)
    print("created", os.path.relpath(dst, STUDIO))

if __name__ == "__main__": main()
