# Video Studio Platform

A modular, DaVinci Resolve–centred platform for making AI-assisted films, documentary series, trailers and reels.
The pipeline prepares clean, reusable ingredients (keyed talent, plates, voices, music, effects, documents) and lays
them out on editable Resolve timelines. Nothing that a person might want to change is baked into pixels.

This repository holds **only the platform code and docs**. Media, cast data, keys and per-production files live in
the studio folder on Google Drive and never enter Git.

## Principles

1. **Resolve is the master.** Titles, lower thirds, captions, grade and effects stay editable in Resolve.
2. **Nothing is baked in.** Generated clips are stored raw; anything that can be a Resolve element is one.
3. **Micro-components.** Every clip, plate, voice line, cue, title and document page is its own file with its own record.
4. **Small modules.** Productions are built from 3–5 minute modules, each with its own timeline.
5. **Generate once, edit forever.** Paid AI generation happens only for genuinely new or wrong components, and only
   after a reuse-first search of the catalog and free stock.
6. **Safe by default.** Plans before actions, checksums before deletions, dated copies instead of overwrites.

## Layout of the studio (on Google Drive)

```
14 Video Studio/
├── 00 Platform/        this repository: core/, frameworks/, resolve-templates/, docs/, tools/
├── 01 Brand/           presenter studio angles, portrait, intro/outro, watermarks, music beds
├── 02 Libraries/       stock/, plates/, sfx/, music/, screens/ + LICENCES.csv + _catalog/ (asset catalog)
├── 03 Series/          one folder per series (cast, episodes as modules)
├── 04 Productions/     one-off videos and films
└── 90 Archive/         retired code, experiments, tests
```

Machine-specific, rebuildable things (Python environments, render caches, model weights, embeddings) live in
`~/VideoStudioRuntime` on the editing machine, never on Drive. `core/compat_links.py` rebuilds
`~/Movies/video-pipeline`, a local folder of shortcuts that maps older paths onto this layout.

## Core modules (`core/`)

| Module | What it does |
| --- | --- |
| `build.py` | Modular, cached builder: assets → derived assets → shots → stems → mix → stream-copy mux. Rebuilds only what changed. |
| `keyer.py` | Border-connected chroma keyer with feathered rim and despill (ProRes 4444 + alpha). |
| `mixer.py` | Dialogue-first mixer: ducks music and effects under every spoken line, loudness to −16 LUFS. |
| `audio_qc.py` | Per-line dialogue-to-background ratio and speech-recognition intelligibility check; fails the build if a line is unclear. |
| `titles.py` | Title-card renderer (flash, tracking, metallic fill, light sweep, grain). |
| `catalog.py` | Asset catalog: one record per file, word + picture search (local CLIP), duplicates, and the reuse-first `need` gate. |
| `cleanup.py` | Safe cleanup of old revisions: reviewed plan, protected classes, checksum checks, deletion log. |
| `intake_check.py` | Checks a cast member's photos, videos and voice before identity training. |
| `reel.py`, `subs.py`, `broll.py`, `screens.py`, `resolve_build.py`, `vo_*` | Reel/explainer framework, captions, licensed stock search, screen renders, Resolve project builder, voice checks. |

See `docs/MODULAR.md` for the build rules and commands.

## Requirements

macOS or Linux, Python 3.12+, ffmpeg, DaVinci Resolve Studio (for the Resolve bridge). Python packages: numpy,
opencv-python-headless, onnxruntime, tokenizers, faster-whisper, Pillow. API keys are read from
`~/.config/video-pipeline/keys.json` and are never stored in the repository.
