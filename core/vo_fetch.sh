#!/bin/bash
# usage: vo_fetch.sh <scene_dir> <a|b> <url>
#   downloads an ElevenLabs take into the scene folder as take_<a|b>.mp3:
#   edge silence trimmed, then cleaned with brand.json voice.clean_filter
#   (plosive "pha" pops tamed in the low band, breaths between phrases lowered ~10 dB, gentle de-ess;
#   pitch and tone untouched). The untouched download is kept as raw_<a|b>.mp3.
#   Take "a" also becomes audio.mp3 unless audio.mp3 already exists. Use `vo_use.sh <scene_dir> b` to switch.
set -e; D="$1"; T="$2"; mkdir -p "$D"; HERE="$(cd "$(dirname "$0")" && pwd)"
curl -sS --fail --max-time 60 -o "$D/raw_$T.mp3" "$3"
CLEAN=$(python3 -c "import json;print(json.load(open('$HERE/brand.json'))['voice'].get('clean_filter','anull'))")
TRIM="silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08,areverse"
ffmpeg -y -loglevel error -i "$D/raw_$T.mp3" -filter_complex "[0]$TRIM,$CLEAN" -ar 44100 -b:a 192k "$D/take_$T.mp3"
if [ "$T" = "a" ] && [ ! -f "$D/audio.mp3" ]; then cp "$D/take_a.mp3" "$D/audio.mp3"; fi
printf "%s take_%s %.2fs\n" "$(basename "$D")" "$T" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$D/take_$T.mp3")"
