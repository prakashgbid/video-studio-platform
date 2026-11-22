#!/bin/bash
# One-time install of the studio GPU worker onto the persistent network volume (build plan 2.3).
# Runs ON the pod:  bash /workspace/studio/setup_worker.sh
# Installs ComfyUI + the custom nodes we use + AI Toolkit into /workspace (survives pod restarts).
# Pinning: if /workspace/studio/lock.json exists, those exact commits are checked out; otherwise the current
# heads are installed and written to lock.json (commit that file back into 00 Platform/worker/).
set -euo pipefail
WS=/workspace; ST=$WS/studio; mkdir -p "$ST"; cd "$WS"
LOCK="$ST/lock.json"
repo() {  # repo <dir> <url>
  local d="$1" u="$2"
  [ -d "$d/.git" ] || git clone --filter=blob:none "$u" "$d"
  local want; want=$(python3 -c "import json,sys;print(json.load(open('$LOCK')).get('$d',''))" 2>/dev/null || true)
  if [ -n "$want" ]; then git -C "$d" fetch -q origin && git -C "$d" checkout -q "$want"; fi
}
repo ComfyUI https://github.com/comfyanonymous/ComfyUI
mkdir -p ComfyUI/custom_nodes
repo ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper https://github.com/kijai/ComfyUI-WanVideoWrapper
repo ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite
repo ComfyUI/custom_nodes/ComfyUI-KJNodes https://github.com/kijai/ComfyUI-KJNodes
repo ai-toolkit https://github.com/ostris/ai-toolkit
python3 -m venv "$WS/venv" 2>/dev/null || true; . "$WS/venv/bin/activate"
pip install -q --upgrade pip
pip install -q -r ComfyUI/requirements.txt
for n in ComfyUI/custom_nodes/*/; do [ -f "$n/requirements.txt" ] && pip install -q -r "$n/requirements.txt" || true; done
pip install -q -r ai-toolkit/requirements.txt || true
python3 - <<'PY'
import json, subprocess, os
L = {}
for d in ["ComfyUI", "ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper", "ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite",
          "ComfyUI/custom_nodes/ComfyUI-KJNodes", "ai-toolkit"]:
    L[d] = subprocess.run(["git", "-C", "/workspace/" + d, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
p = "/workspace/studio/lock.json"
if not os.path.exists(p): json.dump(L, open(p, "w"), indent=1); print("wrote lock.json", L)
else: print("lock.json kept")
PY
echo "worker installed"
