#!/usr/bin/env python3
"""GPU job runner (build plan 2.5, 2.7): run a ComfyUI workflow on the studio pod and bring the results home.

    python jobs.py bootstrap <pod>                         # copy worker/, install it on the volume, fetch cleared models
    python jobs.py run <pod> <workflow_api.json> --inputs a.png b.mp4 --out <dir> [--minor] [--set node.input=value ...]
    python jobs.py wipe <pod>                              # delete ComfyUI input/output/temp on the pod and verify

Transport: SSH only (pod exposes 22/tcp). Files go by scp; ComfyUI's HTTP API is reached through an SSH tunnel
(localhost:18188 -> pod:8188), so nothing is ever on a public URL.
A job with --minor (any child in the shot) always ends with a verified wipe, even if the job fails.
Every job is logged to ~/VideoStudioRuntime/gpu/jobs.jsonl (pod, workflow, seconds, GPU $/h).
"""
import os, sys, json, time, uuid, argparse, subprocess, urllib.request, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import gpu
WORKER = os.path.join(os.path.dirname(HERE), "worker")
REG = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(HERE))), "02 Libraries", "_catalog", "model_licences.json")
LPORT = 18188

def pod_ssh(pod):
    p = gpu.call("GET", f"/pods/{pod}")
    if not gpu.ours(p): sys.exit(f"REFUSED: {pod} is not a studio pod")
    d = (p.get("ssh") or {}).get("direct") or {}
    if not d.get("host"): sys.exit("pod has no direct SSH endpoint yet (is it RUNNING?)")
    return d["host"], str(d["port"]), d.get("username") or "root", p

def ssh_base(host, port, user): return ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes", "-p", port, f"{user}@{host}"]
def rsh(pod, cmd, check=True):
    h, pt, u, _ = pod_ssh(pod); return subprocess.run(ssh_base(h, pt, u) + [cmd], capture_output=True, text=True, check=check)
def scp(pod, srcs, dst, up=True):
    h, pt, u, _ = pod_ssh(pod); base = ["scp", "-q", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes", "-P", pt]
    if up: subprocess.run(base + ["-r"] + list(srcs) + [f"{u}@{h}:{dst}"], check=True)
    else: subprocess.run(base + [f"{u}@{h}:{s}" for s in srcs] + [dst], check=True)

class Tunnel:
    def __init__(self, pod): self.pod = pod
    def __enter__(self):
        h, pt, u, _ = pod_ssh(self.pod)
        self.p = subprocess.Popen(ssh_base(h, pt, u)[:-1] + ["-N", "-L", f"{LPORT}:localhost:8188", f"{u}@{h}"])
        for _ in range(60):
            try: urllib.request.urlopen(f"http://localhost:{LPORT}/system_stats", timeout=3); return self
            except Exception: time.sleep(2)
        self.p.terminate(); sys.exit("ComfyUI did not answer through the tunnel (is start_comfy.sh running?)")
    def __exit__(self, *a): self.p.terminate()

def api(path, body=None, files=None):
    url = f"http://localhost:{LPORT}{path}"
    if files:   # multipart upload
        bnd = uuid.uuid4().hex; parts = []
        for name, (fn, data) in files.items():
            parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{bnd}--\r\n".encode())
        req = urllib.request.Request(url, data=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    elif body is not None: req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    else: req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as r: d = r.read()
    try: return json.loads(d)
    except Exception: return d

def wipe(pod):
    """Delete everything ComfyUI holds for jobs, then prove it is gone (build plan 2.7)."""
    dirs = "/workspace/ComfyUI/input /workspace/ComfyUI/output /workspace/ComfyUI/temp /workspace/studio/jobs"
    rsh(pod, f"for d in {dirs}; do mkdir -p $d && find $d -mindepth 1 -delete; done")
    left = rsh(pod, f"find {dirs} -mindepth 1 | wc -l").stdout.strip()
    if left != "0": sys.exit(f"WIPE NOT VERIFIED: {left} files remain on pod {pod}")
    print(f"wiped and verified: pod {pod} holds no job inputs or outputs")
    with open(os.path.join(gpu.RT, "jobs.jsonl"), "a") as f: f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "pod": pod, "event": "wipe_verified"}) + "\n")

def bootstrap(pod):
    import shutil, tempfile
    t = tempfile.mkdtemp(); d = os.path.join(t, "studio"); shutil.copytree(WORKER, d)
    shutil.copy(REG, os.path.join(d, "licences.json"))
    rsh(pod, "mkdir -p /workspace"); scp(pod, [d], "/workspace/")
    k = json.load(open(os.path.join(gpu.CFG_DIR, "keys.json"))).get("huggingface", "")
    for step in ("bash /workspace/studio/setup_worker.sh", f"HF_TOKEN={k} /workspace/venv/bin/python /workspace/studio/fetch_models.py",
                 "bash /workspace/studio/start_comfy.sh"):
        print(">", step.split("HF_TOKEN=")[0] or "fetch models"); r = rsh(pod, step, check=False); print(r.stdout[-1500:], r.stderr[-800:])
    scp(pod, ["/workspace/studio/lock.json"], os.path.join(WORKER, "lock.json"), up=False); print("lock.json copied into 00 Platform/worker")

def run_job(a):
    wf = json.load(open(a.workflow)); t0 = time.time(); outs = []
    for kv in a.set or []:
        k, v = kv.split("=", 1); node, inp = k.split(".", 1)
        try: v = json.loads(v)
        except Exception: pass
        wf[node]["inputs"][inp] = v
    try:
        with Tunnel(a.pod):
            for f in a.inputs or []: api("/upload/image", files={"image": (os.path.basename(f), open(f, "rb").read())})
            pid = api("/prompt", {"prompt": wf, "client_id": "studio"})["prompt_id"]; print("queued", pid)
            while True:
                h = api(f"/history/{pid}")
                if pid in h and h[pid].get("outputs") is not None and h[pid].get("status", {}).get("completed", True): break
                if pid in h and h[pid].get("status", {}).get("status_str") == "error": sys.exit(f"job failed: {json.dumps(h[pid]['status'])[:600]}")
                time.sleep(5)
            os.makedirs(a.out, exist_ok=True)
            for node, o in h[pid]["outputs"].items():
                for kind in ("images", "gifs", "videos", "audio"):
                    for it in o.get(kind, []):
                        q = urllib.parse.urlencode({"filename": it["filename"], "subfolder": it.get("subfolder", ""), "type": it.get("type", "output")})
                        data = api(f"/view?{q}"); p = os.path.join(a.out, it["filename"]); open(p, "wb").write(data); outs.append(p)
    finally:
        if a.minor: wipe(a.pod)
    _, _, _, pinfo = pod_ssh(a.pod); sec = time.time() - t0
    e = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "pod": a.pod, "workflow": os.path.basename(a.workflow), "seconds": round(sec),
         "usd": round(sec / 3600 * (pinfo.get("cost") or 0), 3), "outputs": [os.path.basename(x) for x in outs], "minor": a.minor}
    with open(os.path.join(gpu.RT, "jobs.jsonl"), "a") as f: f.write(json.dumps(e) + "\n")
    print(json.dumps(e, indent=1)); return outs

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("bootstrap").add_argument("pod"); sp.add_parser("wipe").add_argument("pod")
    r = sp.add_parser("run"); r.add_argument("pod"); r.add_argument("workflow"); r.add_argument("--inputs", nargs="*")
    r.add_argument("--out", required=True); r.add_argument("--minor", action="store_true"); r.add_argument("--set", nargs="*")
    a = ap.parse_args()
    {"bootstrap": lambda: bootstrap(a.pod), "wipe": lambda: wipe(a.pod), "run": lambda: run_job(a)}[a.cmd]()

if __name__ == "__main__": main()
