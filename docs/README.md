# Video pipeline — Prakash Tiwari

Topic → script → ElevenLabs voice → Google Flow @me clips → rendered scenes → watermarked videos for every platform
+ captions + thumbnails + chapters + DaVinci Resolve project + post text per platform.

## Formats (engine/formats.json)
| Format | Length | Purpose | Main video | Also produced | Where it goes |
|---|---|---|---|---|---|
| **Pulse** | 45–90 s | Reach & follows: one idea, one takeaway, one question | 4:5 | 9:16 | LinkedIn, Instagram Reels, YouTube Shorts, X |
| **Brief** | 3–5 min | Authority: one framework properly explained, with an example and an opinion | 16:9 | 4:5 full version, 1–2 clips (9:16 + 4:5) | YouTube; LinkedIn native; X (Premium, else clip); Reels/Shorts teaser |
| **Deep Dive** | 15–20 min | Trust & leads: a playbook or case study a CTO can act on | 16:9 + chapters | 3–4 clips (4:5 + 9:16) | YouTube; clips on LinkedIn, X, Reels, Shorts |
| **Masterclass** | 30–40 min | Flagship & lead magnet: evergreen chapter-by-chapter course (also for prakash-tiwari.com) | 16:9 + chapters | 5–6 clips (4:5 + 9:16) | YouTube + website; clip series everywhere else |

Face time: Pulse = standard intro only. Brief = standard intro + outro. Deep Dive / Masterclass = intro, a 6–8 s @me opener per chapter (48 Flow credits each), outro.
Spoken-word budget (~150 wpm): Pulse 100–200 · Brief 420–720 · Deep Dive 2,100–2,900 · Masterclass 4,200–5,800.

## Folders
| Path | What |
|---|---|
| `engine/` | `reel.py` (new / scaffold / preview / render / thumbs / check), `subs.py` (word timings → cue-timed reveals + .srt), `resolve_build.py`, `vo_fetch.sh`, `vo_check.py`, `make_assets.py`, `brand.json`, `formats.json` |
| `assets/` | Reusable brand kit: `intro_standard.mp4` (9:16) · `intro_standard_16x9.mp4` · `outro_standard_16x9.mp4` · `watermark_badge.png` (per-aspect `watermark_<aspect>.png` are built automatically) · `headshot.jpg` · `music_bed.m4a` (10 min) · `music_bed_long.m4a` (45 min) · `source/flow_takes/` (alternate Flow takes) |
| `refs/v2/`, `refs/v2-16x9/` | 8 baseline camera angles (9:16, 16:9) — Flow references and thumbnail photos; `flow-shot-bible.md` has the prompts |
| `projects/NNN-slug/` | one folder per video (layout below) |
| `_tests/` | engine test projects (not for publishing) · `_archive/` old versions |

## Project layout
```
projects/002-event-driven/
  draft.json              the whole script (format, scenes, cut-downs, thumbnail) → `reel.py scaffold` splits it up
  project.json            format, aspects, series, cut-downs
  posts/                  youtube.md · linkedin.md · x.md · instagram.md · youtube_shorts.md   ← written for every project
  scenes/
    scene-00-intro/       Flow clip: video.mp4 (own lip-synced voice) · video_edit_<aspect>.mp4 (Resolve) · prompt.md
    scene-01-hook/        scene.json (what is drawn) · vo_script.md (ElevenLabs text) · prompt.md
                          take_a.mp3 · take_b.mp3 · audio.mp3 (chosen) · video_<aspect>.mp4 (rendered) · preview_<aspect>.png
    scene-07-ch2-opener/  Flow clip with a lower third ("CHAPTER 2 — …"); prompt.md says exactly what to generate
  build/words/            word timings per scene (subs.py)
  build/<variant>/        timeline.json (Resolve), voice.wav, watermark.mov
  final/
    <slug>_16x9.mp4 + .srt        <slug>_4x5.mp4 + .srt        clips/<slug>_clip1_9x16.mp4 + .srt …
    thumbnail_16x9.jpg · cover_4x5.jpg · cover_9x16.jpg · chapters.txt · posts/ (chapters filled in)
    PUBLISH.md            what to upload where: video, captions, thumbnail and text per platform
  resolve/<slug>.drp      Resolve backup (live project: Project Manager › "Reels"; one timeline per variant)
```

## Where things live
- **Platform (source of truth):** Google Drive › `14 Video Studio`. `~/Movies/video-pipeline` is a symlink to it, so every path below still works.
- **Local runtime (never on Drive, rebuildable):** `~/VideoStudioRuntime/` → `venv/` (Python env), `kokoro-venv/` (Kokoro TTS), `cache/<production>/cache/` (build cache: keyed clips, shots, stems, mixes), `reel-cache/`. Delete any of it to free disk; the next build regenerates what it needs.
- Keys stay in `~/.config/video-pipeline/keys.json` (never in the Drive folder).

## Commands (on the Mac)
```bash
. ~/VideoStudioRuntime/venv/bin/activate; cd ~/Movies/video-pipeline/engine
python3 reel.py new deepdive "How I'd modernise a bank's core platform"   # → projects/00N-…/draft.json (audience: tech)
python3 reel.py new brief everyday "AI will do your shopping soon"        # audience before the title
python3 reel.py scaffold ../projects/00N-slug        # after writing the scenes into draft.json
./vo_fetch.sh ../projects/00N-slug/scenes/scene-01-hook a "<elevenlabs url>"
python3 vo_check.py ../projects/00N-slug --takes     # spoken words vs script
python3 subs.py ../projects/00N-slug                 # word timings (after voice + Flow clips are in)
python3 reel.py preview ../projects/00N-slug 16x9    # build/sheet_16x9.jpg  (also 4x5 / 9x16)
python3 reel.py render  ../projects/00N-slug         # every variant; --only 16x9 / clips ; --force re-renders all
python3 reel.py check   ../projects/00N-slug         # length vs format, loudness, chapters, captions, posts, pending clips
/opt/homebrew/bin/python3 resolve_build.py ../projects/00N-slug   # Resolve must be open
```
Render speed on this Mac: ~1 min of work per minute of 16:9 video per aspect (held frames are cached). Only changed scenes re-render.

## Narration standard (all videos, from 2026-11-20)
Voice: ElevenLabs **Multilingual v2**, voice `prakash-professional-30min`, settings in `engine/brand.json` → `voice.voice_settings` (stability 0.5, similarity 0.85, style 0.4, speaker boost on, speed 1.2): professional documentary energy at ~165 words/min. Chosen from the samples in `assets/voice_tests/` (sample H).
- Generate with a node per scene (`creative_add_flow_node` with `model_parameters.voice_settings` from brand.json, then `creative_run_flow_nodes`): `creative_generate_speech` cannot pass the settings.
- Scripts: plain sentences. **No audio tags** (`[warmly]`, `[dramatically]` …), no CAPS for stress, no dramatic `...`; use full stops and commas for rhythm. Write numbers as words when they are spoken ("a hundred and fifty dollars").
- `vo_fetch.sh` cleans every take with `voice.clean_filter`: low cut at 70 Hz, a fast compressor on the band below 160 Hz (plosive "pha" pops), a soft expander that lowers breaths between phrases by about 10 dB, and a light de-esser. Pitch and tone are untouched; the untouched download stays next to it as `raw_a.mp3` / `raw_b.mp3`.

## Audiences (engine/audiences.json)
Pick one per video with `new <format> <audience> "<title>"`. Each sets the series name, writing rules, pacing (wpm), CTA and a **footage target** — the share of screen time that should be real-life footage or recorded screens rather than graphics. `reel.py check` reports `footage_share` against it.

| Key | Series | Who it's for | Footage target |
|---|---|---|---|
| everyday | Everyday Explainer | general public, YouTube viewers | 70% |
| business | For Business Leaders | non-tech managers and owners | 50% |
| boardroom | Boardroom Brief | CEOs, boards, CXOs | 35% |
| tech | (format series) | CTOs, engineering leaders | 20% |
| founders | Founder's Playbook | founders and SMB owners (consulting clients) | 50% |
| leadership | Leadership Lens | hiring boards and recruiters (future employers) | 45% + 30% face |
| academy | Architect's Academy | aspiring and practising architects | 25% |

Publishing: LinkedIn and YouTube get the long versions; X and Instagram get the 9:16 / 4:5 clips.

## Footage system (documentary-style visuals)
Scene type `broll`: a list of `shots`, each with `src`, an optional spoken `cue` word (the shot cuts when that word is said), an optional `label` chip, and options `in`, `speed`, `y` (crop) and `zoom` (stills). Narration plays over it; the scene `caption` sits in a lower panel.

Shot sources:
- `screen:<name>` — a recorded browser screen. Pages live in `assets/broll/screens/src/*.html` (fictional store "Lumen & Oak", fictional assistant "Aria" — never copy real ChatGPT/Gemini UIs). Each page exposes `window.DURATION` and `window.seek(t)`; `engine/screens.py record <name|all> [aspect|all]` films it with headless Chrome (separate temporary profile) for 16:9, 4:5 and 9:16 (phone layout). Current pages: product, browse, checkout, assistant, agent-checkout, agent-compare. On vertical formats keep content above the bottom ~25% (watermark and label chip live there).
- `lib:<id>` — a clip or photo in `assets/broll/lib/<id>/` with `meta.json` (source, licence, author). Add stock with `engine/broll.py search pexels|pixabay video|photo "<query>"` (contact sheet in `assets/broll/_search/`), then `broll.py get <provider>:<kind>:<id> --tags …`. Add own files (Flow, phone footage) with `broll.py add <file> video|photo "<source>" --tags …`. API keys live only in `~/.config/video-pipeline/keys.json` (`{"pexels": "…", "pixabay": "…"}`).
- a path to any image or video.

Flow B-roll: Google Flow in the user's real Chrome (never the Claude app browser), text-to-video 16:9, 8 s, 1080p download, then `broll.py add`. Costs 24 credits per take at x1 — never buy credits without approval. Product photos for screens: Flow image mode (Nano Banana, 0 credits), saved to `screens/src/img/`.

## Scene types (fields in reel.py's docstring)
`hook` `title` `flow` `cards` `compare` `stat` `tip` `cta` (short-form) · `chapter` (chapter card + agenda) · `bullets` · `quote` ·
`diagram` (boxes + arrows from x/y positions; left-to-right diagrams turn top-to-bottom in portrait) · `timeline` ·
`image` (a still — e.g. a free Nano Banana image from Flow — with slow zoom) · `clip` (Flow @me video, optional lower third).
Items in `bullets`, `cards`, `diagram`, `timeline` can carry a `"cue"` phrase: the item appears when that phrase is spoken.
`"cutdown_only": true` keeps a scene (e.g. a clip-specific CTA) out of the full-length video.

## Editing later (precise edits only)
- **Change words on screen:** edit that scene's `scene.json` → `reel.py render` (only that scene re-renders) → in Resolve, *Replace clip*.
- **Change one voice line:** regenerate it in ElevenLabs → `vo_fetch.sh … b` → copy to `audio.mp3` → `subs.py` → render.
- **Fine timing, trims, B-roll, colour, titles:** in the Resolve project; deliver from Resolve at the timeline's size.
- `resolve_build.py` never overwrites an existing Resolve project — it adds a dated copy, so manual edits are safe.
- **Audacity** is available for cleaning a noisy take before it becomes `audio.mp3`.

## Modular framework (all productions, from 2026-11-21) — read engine/MODULAR.md
Every production is small cached units: assets (assets.json) → derived assets → one file per shot → dialogue/fx/music stems → mix → stream-copied final.
`python3 engine/build.py <dir> --plan` shows what will render; a build rebuilds only units whose spec or inputs changed and reports the rest as reused.
The build never generates AI media: a re-take is a new asset id, registered with prompt, generation ids and credits. One generated clip = one shot.
Green/blue-screen talent is keyed with `engine/keyer.py` (hole-free matte). First user: series/agentic/trailer.
Sound is dialogue-first: `engine/mixer.py` ducks all music and effects under every spoken line, and `engine/audio_qc.py` checks every line's clarity (level margin + transcription match) and programme loudness after every build; a failing QC blocks publishing.

## Rules for every project
- Watermark (headshot + name + title + prakash-tiwari.com) lower-right on every frame, intro included.
- All posts in `posts/` written (no TODO) and `reel.py check` shows `"ok": true`.
- Every spoken line transcription-checked; every fact and number sourced.
