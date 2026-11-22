#!/usr/bin/env python3
"""Rented GPU control for the studio (build plan 2.1, 2.6). RunPod REST API v2 (https://api.runpod.io/v2).

    python gpu.py check                         # key present? API reachable? cap and month-to-date spend
    python gpu.py gpus [--max 1.5]              # GPU types with VRAM and $/h (secure / community)
    python gpu.py list                          # our pods, status, $/h, uptime
    python gpu.py start [--gpu "NVIDIA GeForce RTX 4090"] [--cloud COMMUNITY] [--image IMG] [--volume VOLID]
    python gpu.py wait <pod>                    # until RUNNING (timeout 15 min)
    python gpu.py exec <pod> -- <cmd...>        # run a command in the pod
    python gpu.py stop <pod> | terminate <pod>
    python gpu.py volume-create <name> <GB> <dataCenter>
    python gpu.py idle-watch [--minutes 10]     # stop any of our pods whose GPUs sat idle that long (watchdog)
    python gpu.py cost                          # sessions this month from the local ledger
Add --dry-run to any command to print the HTTP request instead of sending it (works without a key).

Safety: every pod we create is named "studio-*"; we never touch other pods. A start is refused when the hourly rate
exceeds max_hourly_usd or when month-to-date + one hour would pass monthly_cap_usd (config below). Every start and
stop is logged to ~/VideoStudioRuntime/gpu/sessions.jsonl for the cost ledger.
Key: ~/.config/video-pipeline/keys.json "runpod" (never in the studio folder).
Config: ~/.config/video-pipeline/gpu.json {"monthly_cap_usd": 50, "max_hourly_usd": 1.5, "idle_minutes": 10,
        "default_gpu": "NVIDIA GeForce RTX 4090", "cloud": "COMMUNITY", "image": "", "volume_id": "", "volume_path": "/workspace"}
"""
import os, sys, json, time, argparse, urllib.request, urllib.error
API = "https://api.runpod.io/v2"
CFG_DIR = os.path.expanduser("~/.config/video-pipeline")
RT = os.path.join(os.environ.get("VIDEO_RUNTIME", os.path.expanduser("~/VideoStudioRuntime")), "gpu"); os.makedirs(RT, exist_ok=True)
SESS = os.path.join(RT, "sessions.jsonl")
DEFAULTS = {"monthly_cap_usd": 50, "max_hourly_usd": 1.5, "idle_minutes": 10, "default_gpu": "NVIDIA GeForce RTX 4090",
            "cloud": "COMMUNITY", "image": "", "volume_id": "", "volume_path": "/workspace", "disk_gb": 40}
DRY = False

def cfg():
    p = os.path.join(CFG_DIR, "gpu.json"); d = dict(DEFAULTS)
    if os.path.exists(p): d.update(json.load(open(p)))
    return d

def key():
    p = os.path.join(CFG_DIR, "keys.json")
    k = json.load(open(p)).get("runpod") if os.path.exists(p) else None
    if not k and not DRY: sys.exit("No RunPod key: add \"runpod\" to ~/.config/video-pipeline/keys.json (build plan 0.4)")
    return k or "DRY-RUN"

def call(method, path, body=None):
    url = API + path; data = json.dumps(body).encode() if body is not None else None
    if DRY: print(f"DRY RUN  {method} {url}\n{json.dumps(body, indent=1) if body is not None else ''}"); return {}
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: txt = r.read().decode(); return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e: sys.exit(f"RunPod {method} {path}: HTTP {e.code} {e.read().decode()[:400]}")

def items(resp, k="pods"):
    if isinstance(resp, list): return resp
    return resp.get(k) or resp.get("data") or resp.get("items") or []

def log(ev):
    ev["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(SESS, "a") as f: f.write(json.dumps(ev) + "\n")

def month_spend():
    """USD this month from the local ledger: each start->stop interval at the pod's hourly cost (running pods count to now)."""
    if not os.path.exists(SESS): return 0.0
    m = time.strftime("%Y-%m"); open_, total = {}, 0.0
    for l in open(SESS):
        e = json.loads(l)
        if e["event"] == "start": open_[e["pod"]] = (time.mktime(time.strptime(e["time"], "%Y-%m-%dT%H:%M:%S")), e.get("cost", 0), e["time"][:7])
        elif e["event"] in ("stop", "terminate") and e["pod"] in open_:
            t0, c, mm = open_.pop(e["pod"]); t1 = time.mktime(time.strptime(e["time"], "%Y-%m-%dT%H:%M:%S"))
            if mm == m: total += (t1 - t0) / 3600 * c
    for t0, c, mm in open_.values():
        if mm == m: total += (time.time() - t0) / 3600 * c
    return total

def ours(p): return str(p.get("name", "")).startswith("studio-")

def pubkey():
    for n in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub"):
        f = os.path.expanduser(f"~/.ssh/{n}")
        if os.path.exists(f): return open(f).read().strip()
    sys.exit("No SSH public key in ~/.ssh: the worker is reached only over SSH")

def cmd_start(a):
    C = cfg(); gpu = a.gpu or C["default_gpu"]; cloud = a.cloud or C["cloud"]
    rate = None
    if not DRY:
        cat = items(call("GET", "/catalog/gpus"), "gpus")
        g = next((x for x in cat if x.get("id") == gpu or x.get("name") == gpu), None)
        if not g: sys.exit(f"GPU type {gpu!r} not in the catalog; see `gpu.py gpus`")
        rate = (g.get("price") or {}).get(cloud.lower())
        if rate is None: sys.exit(f"{gpu} is not offered on {cloud} cloud")
        if rate > C["max_hourly_usd"]: sys.exit(f"REFUSED: {gpu} costs ${rate}/h, above max_hourly_usd ${C['max_hourly_usd']}")
        spent = month_spend()
        if spent + rate > C["monthly_cap_usd"]: sys.exit(f"REFUSED: month-to-date ${spent:.2f} + 1 h would pass the ${C['monthly_cap_usd']} cap")
    body = {"name": f"studio-{time.strftime('%m%d-%H%M')}", "cloud": cloud, "gpu": {"id": gpu, "count": 1},
            "image": a.image or C["image"] or "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
            "disk": C["disk_gb"], "ports": ["22/tcp"], "env": {"STUDIO": "1", "PUBLIC_KEY": pubkey()}, "startSsh": True}
    # only SSH is exposed: ComfyUI (no login) is reached through an SSH tunnel, never a public URL (children's data)
    vol = a.volume or C["volume_id"]
    if vol: body["mounts"] = {"network": [{"volumeId": vol, "path": C["volume_path"]}]}
    p = call("POST", "/pods", body)
    if p: log({"event": "start", "pod": p["id"], "gpu": gpu, "cloud": cloud, "cost": p.get("cost", rate)}); print(f"started {p['id']} {gpu} ${p.get('cost', rate)}/h")

def cmd_action(pod, action):
    p = call("GET", f"/pods/{pod}") if not DRY else {"name": "studio-dry"}
    if not ours(p): sys.exit(f"REFUSED: pod {pod} is not a studio pod")
    call("POST", f"/pods/{pod}/action", {"action": action})
    if not DRY: log({"event": action, "pod": pod})
    print(f"{action}: {pod}")

def cmd_wait(pod, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = call("GET", f"/pods/{pod}")
        if DRY: return
        st = p.get("status"); print(f"  {int(time.time()-t0):4d}s {st}")
        if st == "RUNNING": return p
        if st in ("ERROR", "TERMINATED", "EXITED"): sys.exit(f"pod {pod} is {st}")
        time.sleep(10)
    sys.exit("timed out waiting for the pod")

def cmd_idle(minutes):
    """Watchdog: GPU utilisation 0 for `minutes` consecutive checks -> stop. Run every minute (launchd) while a pod is up."""
    state_p = os.path.join(RT, "idle_state.json"); state = json.load(open(state_p)) if os.path.exists(state_p) else {}
    for p in items(call("GET", "/pods")):
        if not ours(p) or p.get("status") != "RUNNING": state.pop(p.get("id"), None); continue
        full = call("GET", f"/pods/{p['id']}"); g = ((full.get("runtime") or {}).get("gpus") or [])
        busy = any((x.get("util") or 0) > 5 for x in g)
        state[p["id"]] = 0 if busy else state.get(p["id"], 0) + 1
        print(f"{p['id']} util {[x.get('util') for x in g]} idle {state[p['id']]} min")
        if state[p["id"]] >= minutes: cmd_action(p["id"], "stop"); state.pop(p["id"])
    json.dump(state, open(state_p, "w"))

def main():
    global DRY
    argv = sys.argv[1:]; DRY = "--dry-run" in argv; argv = [x for x in argv if x != "--dry-run"]
    extra = []
    if "--" in argv: i = argv.index("--"); extra = argv[i + 1:]; argv = argv[:i]
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    for c in ("check", "list", "cost"): sp.add_parser(c)
    g = sp.add_parser("gpus"); g.add_argument("--max", type=float)
    s = sp.add_parser("start"); s.add_argument("--gpu"); s.add_argument("--cloud", choices=["SECURE", "COMMUNITY"]); s.add_argument("--image"); s.add_argument("--volume")
    for c in ("wait", "stop", "terminate", "exec"): sp.add_parser(c).add_argument("pod")
    v = sp.add_parser("volume-create"); v.add_argument("name"); v.add_argument("gb", type=int); v.add_argument("dc")
    w = sp.add_parser("idle-watch"); w.add_argument("--minutes", type=int)
    a = ap.parse_args(argv); C = cfg()
    if a.cmd == "check":
        k = os.path.exists(os.path.join(CFG_DIR, "keys.json")) and json.load(open(os.path.join(CFG_DIR, "keys.json"))).get("runpod")
        print(f"key: {'present' if k else 'MISSING (task 0.4)'} · cap ${C['monthly_cap_usd']}/month · max ${C['max_hourly_usd']}/h · idle stop {C['idle_minutes']} min")
        print(f"month-to-date (local ledger): ${month_spend():.2f}")
        if k: n = len(items(call("GET", "/pods"))); print(f"API OK · {n} pods on the account")
    elif a.cmd == "gpus":
        for x in sorted(items(call("GET", "/catalog/gpus"), "gpus"), key=lambda x: (x.get("price") or {}).get("community") or 99):
            pr = x.get("price") or {}
            if a.max and (pr.get("community") or 99) > a.max: continue
            print(f"{x.get('id', ''):38} {x.get('memory', '?'):>3} GB  secure ${pr.get('secure')}  community ${pr.get('community')}")
    elif a.cmd == "list":
        for p in items(call("GET", "/pods")):
            if ours(p): print(f"{p['id']:16} {p.get('name', ''):18} {p.get('status', ''):12} ${p.get('cost')}/h  {(p.get('gpu') or {}).get('id', '')}")
    elif a.cmd == "start": cmd_start(a)
    elif a.cmd == "wait": cmd_wait(a.pod)
    elif a.cmd in ("stop", "terminate"): cmd_action(a.pod, a.cmd)
    elif a.cmd == "exec":
        r = call("POST", f"/pods/{a.pod}/exec", {"command": extra or ["nvidia-smi"]}); print(json.dumps(r, indent=1)[:4000] if r else "")
    elif a.cmd == "volume-create": print(call("POST", "/network-volumes", {"name": a.name, "size": a.gb, "dataCenter": a.dc}))
    elif a.cmd == "idle-watch": cmd_idle(a.minutes or C["idle_minutes"])
    elif a.cmd == "cost": print(f"month-to-date ${month_spend():.2f} of ${C['monthly_cap_usd']} cap")

if __name__ == "__main__": main()
