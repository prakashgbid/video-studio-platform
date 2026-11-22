#!/usr/bin/env python3
"""Build a DaVinci Resolve Studio project from a rendered project (needs Resolve running,
Preferences > System > General > External scripting = Local).

  /opt/homebrew/bin/python3 resolve_build.py <project>

Creates Resolve project "<slug>" in the Project Manager folder "Reels" (if the name exists, a dated copy is made —
an existing project, which may hold manual edits, is never overwritten) with:
  Media Pool bins: 01 Scenes/<scene> (one video per aspect + its voice line), 02 Music, 03 Brand
  One timeline per rendered variant (build/<variant>/timeline.json), each at its own resolution:
    "<slug> 16x9", "<slug> 4x5", "<slug> clip1_9x16", ...
    V1 scenes · V2 watermark · A1 Flow clips (own voice) · A2 ElevenLabs voice · A3 music (ducked)
Exports a backup to <project>/resolve/<slug>.drp.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import glob, json, os, sys, time

API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ.setdefault("RESOLVE_SCRIPT_API", API)
os.environ.setdefault("RESOLVE_SCRIPT_LIB", "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so")
sys.path.append(os.path.join(API, "Modules"))
import DaVinciResolveScript as dvr  # noqa: E402


def die(msg): print("ERROR:", msg); sys.exit(1)


def main():
    root = os.path.abspath(os.path.expanduser(sys.argv[1]))
    meta = json.load(open(os.path.join(root, "project.json"))); slug = meta["slug"]
    tls = [json.load(open(f)) for f in sorted(glob.glob(os.path.join(root, "build", "*", "timeline.json")))]
    if not tls and os.path.exists(os.path.join(root, "build", "timeline.json")):  # v2 projects
        t0 = json.load(open(os.path.join(root, "build", "timeline.json"))); t0.setdefault("name", "v1"); tls = [t0]
    if not tls: die("nothing rendered yet — run reel.py render first")
    prim = meta.get("primary"); tls.sort(key=lambda t: (t.get("name") != prim, t.get("kind") != "full", t.get("name", "")))
    resolve = dvr.scriptapp("Resolve")
    if not resolve: die("Resolve is not running or external scripting is off")
    pm = resolve.GetProjectManager(); pm.GotoRootFolder()
    if "Reels" not in (pm.GetFolderListInCurrentFolder() or []): pm.CreateFolder("Reels")
    pm.OpenFolder("Reels")
    name = slug if slug not in (pm.GetProjectListInCurrentFolder() or []) else f"{slug} {time.strftime('%Y-%m-%d %H%M')}"
    proj = pm.CreateProject(name)
    if not proj: die(f"could not create project {name}")
    fps = tls[0]["fps"]
    for k, v in {"timelineResolutionWidth": str(tls[0]["width"]), "timelineResolutionHeight": str(tls[0]["height"]),
                 "timelineFrameRate": str(fps), "timelinePlaybackFrameRate": str(fps)}.items():
        proj.SetSetting(k, v)
    mp = proj.GetMediaPool(); rootbin = mp.GetRootFolder()
    b_sc = mp.AddSubFolder(rootbin, "01 Scenes"); b_mu = mp.AddSubFolder(rootbin, "02 Music"); b_br = mp.AddSubFolder(rootbin, "03 Brand")
    bins, items = {}, {}

    def imp(folder, path, label):
        if path in items: return items[path]
        mp.SetCurrentFolder(folder); got = mp.ImportMedia([path]) or []
        if not got: die(f"import failed: {path}")
        got[0].SetClipProperty("Clip Name", label); items[path] = got[0]; return got[0]

    report = []
    for tl in tls:
        vname = tl.get("name", "v1"); aspect = vname.split("_")[-1]
        t = None
        mp.SetCurrentFolder(rootbin); t = mp.CreateEmptyTimeline(f"{slug} {vname}")
        if not t: die(f"could not create timeline {vname}")
        proj.SetCurrentTimeline(t)
        t.SetSetting("useCustomSettings", "1")
        t.SetSetting("timelineResolutionWidth", str(tl["width"])); t.SetSetting("timelineResolutionHeight", str(tl["height"]))
        t.SetSetting("timelineFrameRate", str(fps))
        while t.GetTrackCount("video") < 2: t.AddTrack("video")
        while t.GetTrackCount("audio") < 3: t.AddTrack("audio", "stereo")
        t.SetTrackName("video", 1, "Scenes"); t.SetTrackName("video", 2, "Watermark")
        t.SetTrackName("audio", 1, "Flow clips (own voice)"); t.SetTrackName("audio", 2, "Voice (ElevenLabs)"); t.SetTrackName("audio", 3, "Music (ducked)")
        start = t.GetStartFrame(); total = int(round(tl["total"] * fps))
        for sc in tl["scenes"]:
            if sc["name"] not in bins: bins[sc["name"]] = mp.AddSubFolder(b_sc, sc["name"])
            vid = imp(bins[sc["name"]], sc["video"], f"{sc['name']} (video {aspect})")
            n = int(round(sc["duration"] * fps))
            info = {"mediaPoolItem": vid, "startFrame": 0, "endFrame": n - 1, "trackIndex": 1, "recordFrame": start + int(round(sc["start"] * fps))}
            if not sc["has_own_audio"]: info["mediaType"] = 1
            mp.AppendToTimeline([info])
            if sc.get("audio"):
                aud = imp(bins[sc["name"]], sc["audio"], f"{sc['name']} (voice)")
                an = int(round(float(aud.GetClipProperty("Frames") or 0))) or n
                mp.AppendToTimeline([{"mediaPoolItem": aud, "startFrame": 0, "endFrame": an - 1, "trackIndex": 2, "mediaType": 2,
                                      "recordFrame": start + int(round(sc["audio_offset"] * fps))}])
        music = imp(b_mu, tl["music"], f"music bed (ducked) {vname}")
        mp.AppendToTimeline([{"mediaPoolItem": music, "startFrame": 0, "endFrame": total - 1, "trackIndex": 3, "mediaType": 2, "recordFrame": start}])
        wm = imp(b_br, tl["watermark"], f"watermark {vname} — Prakash Tiwari · prakash-tiwari.com")
        mp.AppendToTimeline([{"mediaPoolItem": wm, "startFrame": 0, "endFrame": total - 1, "trackIndex": 2, "mediaType": 1, "recordFrame": start}])
        report.append({"timeline": t.GetName(), "size": f"{tl['width']}x{tl['height']}", "frames": total,
                       "video_items": len(t.GetItemListInTrack("video", 1) or []), "voice_items": len(t.GetItemListInTrack("audio", 2) or []),
                       "watermark_frames": sum(i.GetDuration() for i in (t.GetItemListInTrack("video", 2) or []))})
    proj.SetCurrentTimeline(proj.GetTimelineByIndex(1))
    pm.SaveProject()
    os.makedirs(os.path.join(root, "resolve"), exist_ok=True)
    drp = os.path.join(root, "resolve", f"{slug}.drp")
    ok = pm.ExportProject(name, drp, False)
    print(json.dumps({"project": name, "timelines": report, "drp": drp if ok else None}, indent=2))


if __name__ == "__main__":
    main()
