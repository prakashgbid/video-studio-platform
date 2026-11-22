#!/usr/bin/env python3
"""Download the cleared models onto the network volume (build plan 2.4). Runs ON the pod:
    HF_TOKEN=... python fetch_models.py [model keys]      (default: all in models.json)
Refuses any model whose key is not 'cleared' in licences.json (a copy of the studio register shipped beside it)."""
import os, sys, json
H = os.path.dirname(os.path.abspath(__file__))
M = {k: v for k, v in json.load(open(os.path.join(H, "models.json"))).items() if not k.startswith("_")}
L = json.load(open(os.path.join(H, "licences.json")))
from huggingface_hub import snapshot_download
for k in (sys.argv[1:] or list(M)):
    if (L.get(k) or {}).get("status") != "cleared": print(f"REFUSED {k}: not cleared in the licence register"); continue
    d = os.path.join("/workspace", M[k]["dest"]); os.makedirs(d, exist_ok=True)
    print("fetching", k, "->", d); snapshot_download(M[k]["repo"], local_dir=d, token=os.environ.get("HF_TOKEN"))
print("done")
