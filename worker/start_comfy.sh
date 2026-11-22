#!/bin/bash
# Start ComfyUI on the pod (port 8188, reachable at https://<podId>-8188.proxy.runpod.net). Idempotent.
. /workspace/venv/bin/activate; cd /workspace/ComfyUI
pgrep -f "main.py --listen" >/dev/null || nohup python main.py --listen 0.0.0.0 --port 8188 > /workspace/studio/comfy.log 2>&1 &
echo started
