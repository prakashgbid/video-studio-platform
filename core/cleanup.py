"""Safe cleanup of old revisions (build plan 1.16).

    python cleanup.py plan [--tests] [--archive]     # dry run: writes catalog/cleanup_plan_<time>.json + a summary
    python cleanup.py apply <plan.json>             # deletes exactly what that reviewed plan lists, after re-checking

Policy (catalog/keep_policy.json overrides the defaults below):
  * Revisions: in each folder, files that are versions of one another (name_v2, name_v3, take_a/b, dated copies,
    _old_v4 folders) form a family. Keep the CURRENT and the PREVIOUS one; the rest are deleted.
  * Build intermediates: video/audio under a production's build/ folder that is older than that production's
    final output can be rebuilt, so they go.
  * Exact duplicates: an identical copy that nothing references goes; the referenced or source copy stays.
  * Unused generated takes (Flow, ElevenLabs, GPU ...) that nothing references and nobody approved are kept
    `unused_take_days` (90) days; then the file goes and its catalog record stays: prompt, model, generation id
    and platform link, so it can be found again or re-downloaded from the platform.
  * Approved masters (`catalog.py approve <file>`) are kept forever.
  * Old cleanup plans: only the last 3 are kept.
  * Tests (_tests/) and archive (_archive/) are only touched with --tests / --archive.

Never deleted: anything a production references (assets.json, production.json, scene.json, meta.json ...),
anything marked locked, cast intake and identity models, files changed in the last 24 hours, and finals
(except old versions beyond current + previous). Keepers are checked with ffprobe before anything beside them goes.

Deleting inside the Google Drive folder moves the file to Drive's Trash, where it can be restored for 30 days.
Every deletion is logged in catalog/deletions.jsonl (path, checksum, size, reason, restore-by date) and its
catalog record is marked deleted, so we always know what we had.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402  logical $PWD for path args
import os, re, sys, json, time, argparse, subprocess, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog as C

POLICY = {"keep_revisions": 2, "min_age_hours": 24, "media_kinds": ["video", "audio", "image"],
          "protect_prefixes": ["00 Platform/", "01 Brand/", "02 Libraries/_catalog/"],
          "protect_brand_kit": True,
          "max_delete_gb_without_confirm": 5, "unused_take_days": 90, "keep_plans": 3}
VER = re.compile(r"(?:[_\-\. ](?:v(?P<v>\d+)|rev(?P<r>\d+)|take[_\-]?(?P<t>[a-z])|(?P<d>\d{8})(?:[_\-]?\d{4,6})?|old|copy|\((?P<c>\d+)\)))+$", re.I)

def policy():
    p = os.path.join(C.CAT, "keep_policy.json")
    if not os.path.exists(p): json.dump(POLICY, open(p, "w"), indent=1)
    d = dict(POLICY); d.update(json.load(open(p))); return d

def stem_and_version(path):
    base, ext = os.path.splitext(os.path.basename(path))
    m = VER.search(base)
    if not m: return base.lower(), (1, 0)
    stem = base[:m.start()].lower() or base.lower()
    g = m.groupdict()
    if g.get("v"): n = int(g["v"])
    elif g.get("r"): n = int(g["r"])
    elif g.get("t"): n = ord(g["t"].lower()) - 96
    elif g.get("d"): n = int(g["d"])
    elif g.get("c"): n = int(g["c"]) + 1
    else: n = 0          # "_old" / "_copy": older than the unversioned file
    return stem, (n, 0)

def valid(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try: return float(r.stdout.strip() or 0) > 0
    except ValueError: return False

def plan(tests=False, archive=False):
    P = policy(); recs = C.scan(embed=False, quiet=True)
    now = time.time(); act = {r: x for r, x in recs.items() if x.get("status") == "active"}
    refs = C.code_refs()
    def protected(r, x):
        if x.get("used_by"): return "referenced by " + x["used_by"][0]
        if P.get("protect_brand_kit") and r.startswith("01 Brand/"): return "brand kit (01 Brand)"
        if "/cast/" in f"/{r}": return "cast (intake, sheets, identity)"
        if any(rx.search(os.path.basename(r)) for rx in refs) and x["role"] not in ("derived", "test", "preview"):
            return "loaded by platform code"
        if (x.get("source") or {}).get("locked"): return "locked"
        if x.get("keep") == "master": return "approved master (kept forever)"
        if any(r.startswith(p) or f"/{p}" in f"/{r}" for p in P["protect_prefixes"]): return "protected folder"
        if x["kind"] == "model": return "identity model"
        if now - os.path.getmtime(os.path.join(C.STUDIO, r)) < P["min_age_hours"] * 3600: return "changed in the last 24h"
        if x["role"] == "archive" and not archive: return "archive (use --archive)"
        if x["role"] == "test" and not tests: return "test (use --tests)"
        return None
    media = {r: x for r, x in act.items() if x["kind"] in P["media_kinds"]}
    delete, kept, keepers = {}, {}, set()
    # 1. revision families (files) + _old* folders
    fam = {}
    for r, x in media.items():
        d = os.path.dirname(r); parts = d.split(os.sep)
        oldfolder = next((i for i, p in enumerate(parts) if re.fullmatch(r"_?old(?:[_\-]?v?\d+)?", p, re.I)), None)
        if oldfolder is not None:      # a file inside an _old_vN folder belongs to the same file in the parent folder
            home = os.sep.join(parts[:oldfolder]); vm = re.search(r"\d+", parts[oldfolder]); n = int(vm.group()) if vm else 0
            stem, _ = stem_and_version(r); fam.setdefault((home, stem, x["ext"]), []).append((-1000 + n, x, r))
        else:
            stem, (n, _) = stem_and_version(r)
            fam.setdefault((d, stem, x["ext"]), []).append((n, x, r))
    for key, items in fam.items():
        if len(items) <= P["keep_revisions"]:
            if len(items) > 1: keepers.update(t[2] for t in items)   # a family already at current + previous
            continue
        items.sort(key=lambda t: (t[0], os.path.getmtime(os.path.join(C.STUDIO, t[2]))))
        keep = items[-P["keep_revisions"]:]; old = items[:-P["keep_revisions"]]
        keepers.update(k[2] for k in keep)
        if not all(valid(os.path.join(C.STUDIO, k[2])) for k in keep if k[1]["kind"] in ("video", "audio")):
            for _, x, r in items: kept[r] = "family kept: a current revision failed the playback check"
            continue
        for _, x, r in old:
            why = protected(r, x)
            if why: kept[r] = why
            else: delete[r] = f"old revision (keeping {', '.join(os.path.basename(k[2]) for k in keep)})"
    # 2. build intermediates older than the production's final output
    finals = {}
    for r, x in act.items():
        if x["role"] == "final" and x.get("production") and x["kind"] in ("video",):
            finals[x["production"]] = max(finals.get(x["production"], 0), os.path.getmtime(os.path.join(C.STUDIO, r)))
    for pj in [r for r in act if r.endswith("production.json")]:
        try: out = json.load(open(os.path.join(C.STUDIO, pj))).get("output")
        except Exception: out = None
        if out:
            o = os.path.join(os.path.dirname(pj), "build", out)
            if o in act:
                prod = act[pj]["production"]; finals[prod] = max(finals.get(prod, 0), os.path.getmtime(os.path.join(C.STUDIO, o)))
                act[o]["role"] = "final"; media.get(o, {}).update(role="final")
    for r, x in media.items():
        if r in delete or r in keepers or x["role"] != "derived" or x["kind"] == "image": continue
        prod = x.get("production"); ft = finals.get(prod)
        if not ft: kept.setdefault(r, "build file of a production with no final yet"); continue
        if os.path.getmtime(os.path.join(C.STUDIO, r)) >= ft: kept.setdefault(r, "newer than the final"); continue
        why = protected(r, x)
        if why: kept[r] = why
        else: delete[r] = "build intermediate, older than the final (can be rebuilt)"
    # 3. exact duplicates nothing references
    for r, x in media.items():
        if r in delete or r in keepers or not x.get("dup_of"): continue
        why = protected(r, x)
        twin = x["dup_of"]
        same_folder = os.path.dirname(twin) == os.path.dirname(r)
        if why: kept.setdefault(r, why)
        elif twin in act and (same_folder or x["role"] in ("derived", "test", "preview")):
            delete[r] = f"identical copy of {twin}"
        else: kept.setdefault(r, "identical copy in another source folder: kept (may be loaded by name)")
    # 4. unused generated takes past their keep window: file goes, record + platform link stay
    days = P.get("unused_take_days", 90)
    for r, x in media.items():
        if r in delete or r in keepers or x["role"] != "source": continue
        origin = (x.get("source") or {}).get("origin") or ""
        if not any(origin.startswith(o) for o in C.GENERATED): continue
        if now - os.path.getmtime(os.path.join(C.STUDIO, r)) < days * 86400: continue
        why = protected(r, x)
        if why: kept.setdefault(r, why); continue
        link = (x.get("source") or {}).get("platform_url") or (x.get("source") or {}).get("genid")
        delete[r] = f"unused take older than {days} days (record kept{'; platform link ' + str(link) if link else ''})"
    items = [{"path": r, "size": act[r]["size"], "sha256": act[r]["sha256"], "reason": why} for r, why in sorted(delete.items())]
    total = sum(i["size"] for i in items)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    old_plans = sorted(f for f in os.listdir(C.CAT) if re.fullmatch(r"cleanup_plan_\d{8}-\d{6}\.json", f))
    for f in old_plans[:max(0, len(old_plans) - P.get("keep_plans", 3) + 1)]: os.remove(os.path.join(C.CAT, f))
    pf = os.path.join(C.CAT, f"cleanup_plan_{stamp}.json")
    json.dump({"created": stamp, "policy": P, "tests": tests, "archive": archive, "total_bytes": total, "delete": items,
               "kept_with_reason": dict(sorted(kept.items()))}, open(pf, "w"), indent=1)
    by = {}
    for i in items:
        k = i["reason"].split(" (")[0].split(" of ")[0]; by.setdefault(k, [0, 0]); by[k][0] += 1; by[k][1] += i["size"]
    print(f"PLAN (nothing deleted): {len(items)} files, {total/1e6:.0f} MB")
    for k, (n, b) in sorted(by.items(), key=lambda t: -t[1][1]): print(f"  {n:4} files {b/1e6:8.0f} MB  {k}")
    print(f"  protected or kept: {len(kept)} files")
    print(f"review: {os.path.relpath(pf, C.STUDIO)}\napply:  python cleanup.py apply {os.path.relpath(pf, C.STUDIO)}")
    return pf

def apply(pf, yes=False):
    P = policy(); p = json.load(open(pf if os.path.isabs(pf) else os.path.join(C.STUDIO, pf)))
    if p["total_bytes"] / 1e9 > P["max_delete_gb_without_confirm"] and not yes:
        sys.exit(f"plan deletes {p['total_bytes']/1e9:.1f} GB: re-run with --yes to confirm")
    recs = C.load(); done = skipped = 0; freed = 0
    restore = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    with open(C.DELETIONS, "a") as log:
        for i in p["delete"]:
            full = os.path.join(C.STUDIO, i["path"])
            if not os.path.exists(full): skipped += 1; continue
            st = os.stat(full)
            if st.st_size != i["size"] or C.sha256(full) != i["sha256"]: skipped += 1; print("changed since the plan, kept:", i["path"]); continue
            os.remove(full); done += 1; freed += i["size"]
            entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "plan": os.path.basename(pf), **i,
                     "restore": f"Google Drive Trash until {restore}"}
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if i["path"] in recs: recs[i["path"]].update(status="deleted", deleted_at=entry["time"], restore_until=restore)
    C.save(recs)
    # remove only folders that THIS apply emptied (e.g. an emptied _old_v4), never pre-existing empty scaffolds
    emptied = sorted({os.path.dirname(os.path.join(C.STUDIO, i["path"])) for i in p["delete"]}, key=len, reverse=True)
    for d in emptied:
        if os.path.isdir(d) and not os.listdir(d) and re.search(r"(^|/)_?old", os.path.relpath(d, C.STUDIO)):
            os.rmdir(d)
    print(f"deleted {done} files, freed {freed/1e6:.0f} MB, skipped {skipped}. Restorable from Google Drive Trash until {restore}.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("plan"); s.add_argument("--tests", action="store_true"); s.add_argument("--archive", action="store_true")
    s = sp.add_parser("apply"); s.add_argument("plan"); s.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    plan(a.tests, a.archive) if a.cmd == "plan" else apply(a.plan, a.yes)
