#!/usr/bin/env python3
"""Model licence register (build plan 1.9). Register: 02 Libraries/_catalog/model_licences.json

    python licences.py list                     # every model, status and licence
    python licences.py check <model> [...] [--production <folder>]   # exit 1 if any is not cleared

In code:  import licences; licences.require(["wan2.2-i2v-a14b"], production_root)  -> raises SystemExit if refused.
A production may allow a non-cleared model only with a written reason: production.json "allow_models": {"name": "why"}.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402
import os, sys, json, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(os.path.dirname(os.path.realpath(HERE)))
REG = os.path.join(STUDIO, "02 Libraries", "_catalog", "model_licences.json")

def load(): return {k: v for k, v in json.load(open(REG)).items() if not k.startswith("_")}

def require(models, production=None):
    R = load(); allow = {}
    if production and os.path.exists(os.path.join(production, "production.json")):
        allow = json.load(open(os.path.join(production, "production.json"))).get("allow_models", {})
    bad = []
    for m in models:
        e = R.get(m)
        if e and e["status"] == "cleared": continue
        if m in allow and allow[m]: print(f"  allowed by production: {m} ({allow[m]})"); continue
        bad.append(f"{m}: {'not in register' if not e else e['status'] + ' — ' + e['licence']}")
    if bad: raise SystemExit("LICENCE REFUSED:\n  " + "\n  ".join(bad))
    return True

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list"); c = sp.add_parser("check"); c.add_argument("models", nargs="+"); c.add_argument("--production")
    a = ap.parse_args()
    if a.cmd == "list":
        for k, v in sorted(load().items(), key=lambda kv: (kv[1]["status"], kv[0])): print(f"{v['status']:10} {k:22} {v['licence']}")
    else:
        require(a.models, a.production); print("cleared")

if __name__ == "__main__": main()
