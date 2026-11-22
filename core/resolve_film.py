#!/usr/bin/env python3
"""Hand a built production (film or series episode) to DaVinci Resolve Studio with every element on its own track
(build plan 1.11). Resolve must be running (Preferences > System > General > External scripting = Local).

    /opt/homebrew/bin/python3 resolve_film.py <production> [--folder Films]

Creates a NEW project "<name> YYYY-MM-DD HHMM" in the Project Manager folder (never touches an existing project):
  V1 Picture      one clip per shot: film shots use their best stage output (ungraded, for grading); others the engine render
  V2 VFX          empty, for comps
  V3 Titles       empty, for Resolve titles
  V4 Captions     empty, for subtitles
  V5 Reference    the engine's cut, disabled (for sync checks)
  A  Dialogue (+ Dialogue 2 ...)  one clip per line, at its time; overlapping lines get an extra lane
  A  FX (+ FX 2 ...)              one clip per effect
  A  Music (+ Music 2 ...)        one clip per cue (crossfades on two lanes)
  A  Stem: dialogue / fx / music  the engine's stems, disabled (mix reference)
Exports a backup to <production>/resolve/<project>.drp and prints a report.
"""
import os, sys, json, time, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ.setdefault("RESOLVE_SCRIPT_API", API)
os.environ.setdefault("RESOLVE_SCRIPT_LIB", "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
sys.path.append(os.path.join(API, "Modules"))

def die(m): print("ERROR:", m); sys.exit(1)

def media_len(p):
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--folder", default="Films"); a = ap.parse_args()
    root = os.path.realpath(a.root)
    import build as BLD
    b = BLD.Build(root); tl, total = b.timeline(); starts = {s["id"]: t for s, t in tl}
    LB = json.load(open(os.path.join(root, "build", "last_build.json"))); off = b.P.get("offset", 0)
    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
    if not resolve: die("Resolve is not running or external scripting is off")
    pm = resolve.GetProjectManager(); pm.GotoRootFolder()
    if a.folder not in (pm.GetFolderListInCurrentFolder() or []): pm.CreateFolder(a.folder)
    pm.OpenFolder(a.folder)
    name = f"{b.P.get('name', os.path.basename(root))} {time.strftime('%Y-%m-%d %H%M')}"
    if name in (pm.GetProjectListInCurrentFolder() or []): name += time.strftime("%S")
    proj = pm.CreateProject(name)
    if not proj: die(f"could not create project {name}")
    fps = b.fps
    for k, v in {"timelineResolutionWidth": str(b.W), "timelineResolutionHeight": str(b.H), "timelineFrameRate": str(fps),
                 "timelinePlaybackFrameRate": str(fps)}.items(): proj.SetSetting(k, v)
    mp = proj.GetMediaPool(); rb = mp.GetRootFolder()
    bins = {n: mp.AddSubFolder(rb, n) for n in ("01 Shots", "02 Dialogue", "03 FX", "04 Music", "05 Reference")}
    items = {}
    def imp(binname, path, label):
        if path in items: return items[path]
        mp.SetCurrentFolder(bins[binname]); got = mp.ImportMedia([path]) or []
        if not got: die(f"import failed: {path}")
        got[0].SetClipProperty("Clip Name", label); items[path] = got[0]; return got[0]
    fr = lambda sec: int(round(sec * fps))
    # audio cues: sorted by time; overlapping cues (layered FX, music crossfades) get extra lanes, so nothing is dropped
    lanes = []; retimed = []   # lanes: (label, [clips]); retimed: cues the engine time-stretched (set speed in Resolve)
    for track, label, binname in (("dialogue", "Dialogue", "02 Dialogue"), ("fx", "FX", "03 FX"), ("music", "Music", "04 Music")):
        ends, L = [], []
        for c in sorted(b.abs_cues(track, starts), key=lambda c: c["t"]):
            it = imp(binname, b.path_of(c["asset"]), c["asset"])
            fl = fr(media_len(b.path_of(c["asset"])))   # Resolve reports no frame count for mp3: measure the file
            sf = fr(c.get("src_in", 0)); ef = min(fl - 1, fr(c["src_out"]) - 1 if "src_out" in c else fl - 1)
            if c.get("dur"): ef = min(ef, sf + fr(c["dur"]) - 1)
            if c.get("tempo"): retimed.append(f"{c['asset']} at {c['t']:.1f}s (tempo {c['tempo']})")
            rec = fr(c["t"] + off); end = rec + (ef - sf + 1)
            k = next((i for i, e in enumerate(ends) if e <= rec), None)
            if k is None: ends.append(end); L.append([]); k = len(ends) - 1
            else: ends[k] = end
            L[k].append((it, sf, ef, rec))
        for i, cl in enumerate(L or [[]]): lanes.append((label if i == 0 else f"{label} {i+1}", cl))
    mp.SetCurrentFolder(rb); t = mp.CreateEmptyTimeline(b.P.get("name", "timeline")); proj.SetCurrentTimeline(t)
    anames = [n for n, _ in lanes] + ["Stem: dialogue", "Stem: fx", "Stem: music"]
    while t.GetTrackCount("video") < 5: t.AddTrack("video")
    while t.GetTrackCount("audio") < len(anames): t.AddTrack("audio", "stereo")
    for i, n in enumerate(["Picture", "VFX", "Titles", "Captions", "Reference (engine cut)"], 1): t.SetTrackName("video", i, n)
    for i, n in enumerate(anames, 1): t.SetTrackName("audio", i, n)
    st0 = t.GetStartFrame()
    nv = na = 0
    for s, ts in tl:
        if s["kind"] == "film": p = b.film_src(s)
        else: p = f"{b.cache}/{s['id']}.{b.shot_key(s)}.mp4"
        if not os.path.exists(p): die(f"missing media for {s['id']}: build first")
        it = imp("01 Shots", p, s["id"]); n = fr(s["dur"])
        info = {"mediaPoolItem": it, "startFrame": 0, "endFrame": n - 1, "trackIndex": 1, "recordFrame": st0 + fr(ts + off), "mediaType": 1}
        if p.endswith(".png"): info.pop("mediaType")
        nv += bool(mp.AppendToTimeline([info]))
    ncues = 0
    for ti, (label, cl) in enumerate(lanes, 1):
        for it, sf, ef, rec in cl:
            ncues += 1
            na += bool(mp.AppendToTimeline([{"mediaPoolItem": it, "startFrame": sf, "endFrame": ef, "trackIndex": ti, "mediaType": 2,
                                              "recordFrame": st0 + rec}]))
    T = fr(LB["duration"])
    ref = imp("05 Reference", LB["output"], "engine cut (reference)")
    mp.AppendToTimeline([{"mediaPoolItem": ref, "startFrame": 0, "endFrame": T - 1, "trackIndex": 5, "mediaType": 1, "recordFrame": st0}])
    ns = len(lanes)
    for ti, k in ((ns + 1, "dialogue"), (ns + 2, "fx"), (ns + 3, "music")):
        it = imp("05 Reference", LB["stems"][k], f"stem {k}")
        mp.AppendToTimeline([{"mediaPoolItem": it, "startFrame": 0, "endFrame": T - 1, "trackIndex": ti, "mediaType": 2, "recordFrame": st0}])
    for kind, idx in (("video", 5), ("audio", ns + 1), ("audio", ns + 2), ("audio", ns + 3)):
        try: t.SetTrackEnable(kind, idx, False)
        except Exception: pass
    pm.SaveProject()
    os.makedirs(os.path.join(root, "resolve"), exist_ok=True); drp = os.path.join(root, "resolve", f"{name}.drp")
    ok = pm.ExportProject(name, drp, False)
    rep = {"project": f"{a.folder}/{name}", "timeline": t.GetName(), "size": f"{b.W}x{b.H}@{fps}",
           "video": {t.GetTrackName("video", i): len(t.GetItemListInTrack("video", i) or []) for i in range(1, 6)},
           "audio": {t.GetTrackName("audio", i): len(t.GetItemListInTrack("audio", i) or []) for i in range(1, len(anames) + 1)},
           "shots_placed": f"{nv}/{len(tl)}", "cues_placed": f"{na}/{ncues}", "set_speed_in_resolve": retimed, "drp": drp if ok else None}
    print(json.dumps(rep, indent=1))

if __name__ == "__main__": main()
