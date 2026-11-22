#!/usr/bin/env python3
"""Stock footage and photos for B-roll — Pexels and Pixabay (free for commercial use, no attribution required),
kept in a reusable, tagged library with a licence record for every file.

Keys (free): ~/.config/video-pipeline/keys.json  {"pexels": "...", "pixabay": "..."}   — never inside the project folders.

usage
  broll.py search <pexels|pixabay> <video|photo> "<query>" [--n 12] [--portrait]
        → prints candidates and writes a numbered contact sheet  assets/broll/_search/<query>.jpg  to look at
  broll.py get <pexels|pixabay>:<video|photo>:<id> [--tags a,b,c]
        → downloads the best file (≤ 4K; 1080p preferred) to assets/broll/lib/<provider>-<kind>-<id>/ + meta.json (licence, author, page)
          and prints the library id to use in a broll shot:  {"src": "lib:<id>"}
  broll.py list [tag]
Rules: no visible brand logos or trademarks as the subject; no footage implying a real person endorses anything;
       prefer people-free or crowd shots for sensitive topics. Licence pages: pexels.com/license · pixabay.com/service/license-summary
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import json, os, re, subprocess, sys, time, urllib.parse, urllib.request

ENGINE = os.path.dirname(os.path.abspath(__file__)); PIPE = os.environ.get("VIDEO_PIPELINE", os.path.expanduser("~/Movies/video-pipeline"))
LIB = os.path.join(PIPE, "assets", "broll", "lib"); SEARCH = os.path.join(PIPE, "assets", "broll", "_search")
KEYS = os.path.expanduser("~/.config/video-pipeline/keys.json")
LICENSE = {"pexels": "Pexels License — free to use, no attribution required (https://www.pexels.com/license/)",
           "pixabay": "Pixabay Content License — free to use, no attribution required (https://pixabay.com/service/license-summary/)"}


def key(p):
    k = json.load(open(KEYS)).get(p) if os.path.exists(KEYS) else None
    if not k: raise SystemExit(f"no {p} API key in {KEYS}")
    return k


def http_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "video-pipeline/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)


def download(url, dst, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "video-pipeline/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst + ".part", "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
    os.replace(dst + ".part", dst)


def search(provider, kind, query, n=12, portrait=False):
    q = urllib.parse.quote(query); out = []
    if provider == "pexels":
        h = {"Authorization": key("pexels")}; ori = "portrait" if portrait else "landscape"
        if kind == "video":
            for v in http_json(f"https://api.pexels.com/videos/search?query={q}&per_page={n}&orientation={ori}", h)["videos"]:
                out.append({"id": v["id"], "w": v["width"], "h": v["height"], "dur": v["duration"], "thumb": v["image"], "page": v["url"], "author": v["user"]["name"]})
        else:
            for p in http_json(f"https://api.pexels.com/v1/search?query={q}&per_page={n}&orientation={ori}", h)["photos"]:
                out.append({"id": p["id"], "w": p["width"], "h": p["height"], "dur": 0, "thumb": p["src"]["medium"], "page": p["url"], "author": p["photographer"]})
    else:
        k = key("pixabay")
        if kind == "video":
            for v in http_json(f"https://pixabay.com/api/videos/?key={k}&q={q}&per_page={max(3, n)}&safesearch=true")["hits"]:
                L = v["videos"]["large"] if v["videos"]["large"]["url"] else v["videos"]["medium"]
                out.append({"id": v["id"], "w": L["width"], "h": L["height"], "dur": v["duration"], "thumb": L.get("thumbnail") or v["videos"]["tiny"].get("thumbnail", ""),
                            "page": v["pageURL"], "author": v["user"]})
        else:
            ori = "vertical" if portrait else "horizontal"
            for p in http_json(f"https://pixabay.com/api/?key={k}&q={q}&image_type=photo&orientation={ori}&per_page={max(3, n)}&safesearch=true")["hits"]:
                out.append({"id": p["id"], "w": p["imageWidth"], "h": p["imageHeight"], "dur": 0, "thumb": p["webformatURL"], "page": p["pageURL"], "author": p["user"]})
    os.makedirs(SEARCH, exist_ok=True); slug = re.sub(r"[^a-z0-9]+", "-", f"{provider}-{kind}-{query}".lower()).strip("-")
    tmp = os.path.join(SEARCH, slug); os.makedirs(tmp, exist_ok=True); thumbs = []
    for i, r in enumerate(out):
        f = os.path.join(tmp, f"{i:02d}.jpg")
        try:
            download(r["thumb"], f)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", f, "-vf", f"scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2,"
                            f"drawtext=text='{i}':x=8:y=8:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.6", f + ".t.jpg"], check=True)
            thumbs.append(f + ".t.jpg")
        except Exception as e: print("thumb failed", i, e)
    if thumbs:
        cols = 4; rows = -(-len(thumbs) // cols); sheet = os.path.join(SEARCH, slug + ".jpg")
        lst = os.path.join(tmp, "l.txt"); open(lst, "w").write("".join(f"file '{t}'\n" for t in thumbs))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-vf", f"tile={cols}x{rows}:padding=4", "-frames:v", "1", sheet], check=True)
        print("sheet:", sheet)
    for i, r in enumerate(out): print(f"[{i}] {provider}:{kind}:{r['id']}  {r['w']}x{r['h']}  {r['dur']}s  by {r['author']}  {r['page']}")
    json.dump(out, open(os.path.join(tmp, "results.json"), "w"), indent=1)


def get(ref, tags=()):
    provider, kind, vid = ref.split(":"); lid = f"{provider}-{kind}-{vid}"; d = os.path.join(LIB, lid)
    if os.path.exists(os.path.join(d, "meta.json")): print(lid); return lid
    os.makedirs(d, exist_ok=True)
    if provider == "pexels":
        h = {"Authorization": key("pexels")}
        if kind == "video":
            v = http_json(f"https://api.pexels.com/videos/videos/{vid}", h)
            files = [f for f in v["video_files"] if f.get("file_type") == "video/mp4" and f.get("width")]
            best = min(files, key=lambda f: (abs(max(f["width"], f["height"]) - 1920) if max(f["width"], f["height"]) >= 1080 else 9999 + (1080 - max(f["width"], f["height"]))))
            url, w, hgt, dur, page, author = best["link"], best["width"], best["height"], v["duration"], v["url"], v["user"]["name"]; ext = "mp4"
        else:
            p = http_json(f"https://api.pexels.com/v1/photos/{vid}", h)
            url, w, hgt, dur, page, author = p["src"]["original"], p["width"], p["height"], 0, p["url"], p["photographer"]; ext = "jpg"
    else:
        k = key("pixabay")
        if kind == "video":
            v = http_json(f"https://pixabay.com/api/videos/?key={k}&id={vid}")["hits"][0]
            L = v["videos"]["large"] if v["videos"]["large"]["url"] else v["videos"]["medium"]
            url, w, hgt, dur, page, author = L["url"], L["width"], L["height"], v["duration"], v["pageURL"], v["user"]; ext = "mp4"
        else:
            p = http_json(f"https://pixabay.com/api/?key={k}&id={vid}")["hits"][0]
            url, w, hgt, dur, page, author = p.get("largeImageURL"), p["imageWidth"], p["imageHeight"], 0, p["pageURL"], p["user"]; ext = "jpg"
    f = f"media.{ext}"; download(url, os.path.join(d, f))
    meta = {"id": lid, "provider": provider, "kind": kind, "file": f, "width": w, "height": hgt, "duration": dur, "page": page,
            "author": author, "license": LICENSE[provider], "downloaded": time.strftime("%Y-%m-%d"), "tags": list(tags)}
    json.dump(meta, open(os.path.join(d, "meta.json"), "w"), indent=2); print(lid); return lid


def add_local(path, kind, source, tags=()):
    """Register a file you made (e.g. a Google Flow clip or still) in the library."""
    base = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(path))[0].lower()).strip("-")[:40]
    lid = f"own-{kind}-{base}"; d = os.path.join(LIB, lid); os.makedirs(d, exist_ok=True)
    ext = os.path.splitext(path)[1].lower(); f = "media" + ext; subprocess.run(["cp", path, os.path.join(d, f)], check=True)
    json.dump({"id": lid, "provider": "own", "kind": kind, "file": f, "source": source, "license": "Created by/for Prakash Tiwari — own rights",
               "downloaded": time.strftime("%Y-%m-%d"), "tags": list(tags)}, open(os.path.join(d, "meta.json"), "w"), indent=2)
    print(lid); return lid


def lst(tag=None):
    for d in sorted(os.listdir(LIB)) if os.path.isdir(LIB) else []:
        mf = os.path.join(LIB, d, "meta.json")
        if not os.path.exists(mf): continue
        m = json.load(open(mf))
        if tag and tag not in m.get("tags", []): continue
        print(f"lib:{m['id']:<40} {m['kind']:<6} {m.get('duration', 0)}s  tags={','.join(m.get('tags', []))}")


if __name__ == "__main__":
    a = sys.argv[1:]
    tags = a[a.index("--tags") + 1].split(",") if "--tags" in a else []
    if not a: print(__doc__)
    elif a[0] == "search":
        n = int(a[a.index("--n") + 1]) if "--n" in a else 12
        search(a[1], a[2], a[3], n, "--portrait" in a)
    elif a[0] == "get": get(a[1], tags)
    elif a[0] == "add": add_local(a[1], a[2], a[3], tags)   # broll.py add <file> <video|photo> "<source>" --tags ...
    elif a[0] == "list": lst(a[1] if len(a) > 1 else None)
