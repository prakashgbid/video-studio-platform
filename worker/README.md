# GPU worker

The worker lives on a RunPod network volume, not in a custom image: a stock PyTorch image + `setup_worker.sh` once.
1. `gpu.py volume-create studio-models 200 <dataCenter>` and put the id in ~/.config/video-pipeline/gpu.json
2. `gpu.py start` → `gpu.py wait <pod>` → `jobs.py bootstrap <pod>` (copies this folder + licences.json, runs setup, fetches models)
3. Commit the `lock.json` it writes back into this folder: later pods reproduce exactly that install.
Jobs: `jobs.py run <workflow.json> --inputs a.png b.mp4 --out <dir>` (upload → queue → poll → download → wipe → verify).
Children's data is wiped from the pod after every job that used it, and the wipe is verified (build plan 2.7).
