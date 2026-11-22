# Modular production framework (the rule for every video from now on)

**Never edit or regenerate what was not asked to change.** Every production is built from small,
independent, cached units. A change touches one unit; the build reuses everything else.

## The four layers

| Layer | What it is | Where it lives | Changes only when |
|---|---|---|---|
| 1. **Assets** | Every source file: Flow clips, ElevenLabs speech/music/SFX, Kokoro lines, stock, plates, character sheets | `assets.json` (id → path, origin, prompt/line, generation ids, credits, licence, `locked`) | Someone regenerates **that one asset** on purpose |
| 2. **Derived assets** | Deterministic processing of one asset (keyed talent, cleaned voice, upscaled clip) | `assets.json` entries with `derive: {op, from, …}`; output cached in `~/VideoStudioRuntime/cache/<production>/cache/` | Its source asset or its recipe changes |
| 3. **Shots** | One shot = one short encoded file (base picture + layers + text) | `production.json → shots[]`, cache `~/VideoStudioRuntime/cache/<production>/cache/<id>.<hash>.mp4` | That shot's spec or one of its inputs changes |
| 4. **Sound stems + mix + mux** | `dialogue`, `fx`, `music` stems built from cues; one mix; stream-copied final | `production.json → audio{}`; cues are **anchored to shots** (`{"shot":"S12","at":0.2}`) | A cue on that track changes, or the shots it is anchored to move |

Cache key = hash(spec + engine version + identity of every input file). Same key → reuse.

## Hard rules

1. **The build never generates AI media.** It only reads assets. Generation is a separate,
   explicit act on one asset id, and its prompt, generation ids and credits are written into
   `assets.json`. Generated assets are `locked: true`; they are never overwritten. A re-take
   is a **new asset id** (e.g. `flow_S37b_v2`), and the shot is pointed at it.
2. **One generated clip = one shot.** Long speeches are split into parts (S37a, S37b, S37c) so a
   wording change re-takes one 8-second clip, not the whole speech.
3. **Talent is shot on green/blue, plates separately.** Re-lighting or replacing a set never
   re-generates a performance, and a new performance never touches the set.
4. **Cues are anchored to shots, not to absolute time.** Lengthening a shot moves only the cues
   after it; nothing is regenerated.
5. **Final assembly is stream copy.** Concat with `-c copy`, mux with `-c:v copy`.
6. **Plan before spending.** `build.py <dir> --plan` lists exactly which shots will render. Any AI
   generation is listed (asset id, tool, credits) before it is run.
7. **Every build reports** what was rebuilt and what was reused (`build/last_build.json`).

## Commands

```
python3 engine/build.py <production_dir> --plan          # what would render
python3 engine/build.py <production_dir>                 # build, reusing everything unchanged
python3 engine/build.py <production_dir> --force S12     # rebuild one unit even if unchanged
python3 engine/keyer.py in.mp4 out.mov                   # hole-free green/blue keyer (ProRes 4444 + alpha)
```

## Keyer (why it exists)
Plain colour keys punch holes wherever the subject has screen-like colours (grey hair, cream
blouse, glasses picking up spill). `keyer.py` treats only screen colour **connected to the frame
edge** as background, fills everything enclosed by the subject, softens only the outer rim and
removes spill. Result: a solid matte with no missing pieces of head or body.

## Measured (AGENTIC trailer, 43 shots, 3:06)
- First full build: 54 s. Re-build with nothing changed: 4 s. One title card changed: 4.4 s (1 shot rebuilt).

## Dialogue-first sound (every video, from 2026-11-21)
**Rule: a spoken line is never covered by music or effects.**
- **Levelling:** every dialogue line is brought to the same speech level (-20 dBFS average speech) inside the dialogue stem, so quiet takes and loud takes sit together.
- **Ducking (`engine/mixer.py`):** the mixer finds every spoken line in the dialogue stem and pulls the *whole* background (music + effects together) down until it sits `bed_margin` (default 19 dB) under that line's average level. The duck starts 0.15 s before the first word, falls in 60 ms and recovers over 0.5 s after the line. Between lines the score plays at full energy. Per-production override: `production.json → mix.bed_margin`.
- **Loudness:** two-pass loudnorm to -16 LUFS integrated, true peak <= -1.5 dBTP (limiter), applied as one linear gain so the voice/background balance is not changed.
- **Audio QC gate (`engine/audio_qc.py`, runs automatically at the end of every build):**
  - For every line: dialogue-to-background ratio over the line, pass >= 12 dB, warn 8-12, fail < 8.
  - For every line: intelligibility. The line is transcribed from the clean dialogue and from the FINAL mix; the words must match >= 90 % (fail < 80 %). The report shows what was actually heard.
  - Programme: -16 +/- 1.5 LUFS, true peak <= -1 dBTP.
  - Any failure exits the build with "AUDIO QC FAILED" and a per-shot list in `build/audio_qc.json`. **Nothing is published with a failing QC.**
- Audio has its own version (`AUDIO_VERSION`): changing the mixer rebuilds only stems and mix, never a picture.

Trailer, 21 Nov: before = 25 of 29 lines failed (music up to 11 dB *louder* than the voice; "Meg, it's working" 29 % understandable). After = 31 of 31 pass, median 15 dB of clearance, -15.9 LUFS. The gate also caught one line that was unclear for a non-music reason (a temp voice running "Meg, it's" together); it was re-voiced as a new asset.

## Characters on screen (every video, from 2026-11-21)
**Rule: a character is never shown as a still image. Every character appearance is a filmed performance.**
- **Performance:** each line is performed in Flow from the character's sheet, on green screen, medium close-up, looking straight into the lens, with the emotion written into the prompt. One line = one clip = one asset (`flow_<shot>_<character>`), recorded in `assets.json` with its prompt line and credits. One take per line (24 credits); the audio QC decides if a re-take is needed.
- **Keying:** `talent_<shot>_<character>` is a derived asset (`engine/keyer.py`): hole-free matte, spill removed. Clips delivered with black bars use `"unletterbox": true` (bars cropped, frame rescaled) so no edge artefacts. Keyer output is versioned by `KEYER_VERSION`, so improving the tool never forces finished clips to re-key unless their output really changes.
- **Fitting:** a talent layer with `"fit": true` is trimmed to the exact first and last spoken word (word timestamps, cached); picture and sound are trimmed identically, so lip-sync cannot drift. The clip's own audio becomes the shot's dialogue cue automatically.
- **Cast registry:** `series/<name>/cast.json` holds every character's on-screen name and title (from the bible). Productions point to it with `"cast"`.
- **Lower thirds:** the first time a character appears in a production, a documentary lower third (name + title, amber accent bar, fade in/out, 0.4 s in, ~3 s) is drawn automatically. Per shot: `"lower_third": false` to suppress (e.g. a silent establishing shot), `true` to force.
- **Montages** use short slices of the keyed performances, not stills.
- `--plan` never builds anything (not even keys). Every build prunes superseded cache files.

## Added 2026-11-21 (evening)
- **Keyer `adaptive` option** (per asset, `derive.adaptive: true`): measures the screen's own saturation/brightness and keys only colours that really match it, so grey, dark or greenish props (sofas, chairs, desks) stay solid. Used for Maya S01/S02.
- **Derived audio (`op: audio_filter`)**: deterministic processing of an existing sound (reverse, trim, EQ) instead of paying to regenerate. Example: the opening build is ElevenLabs take B played in reverse so it rises out of silence into the first scene.
- **Cue `fade_in`** for sounds that should grow in (e.g. rain under the disclaimer).
- **Paragraph cards (`block: true`, `size`, `wrap`)**: text is centred as one block, horizontally and vertically. Used for the disclaimer.
- **Every production opens with sound**: an ambient bed starts under the disclaimer and builds into the first scene; no video starts in dead silence.
- **QC text normalisation**: "A.I." and "AI", "U.S." and "US" count as the same word.
- **Title cards (`op: title`, `engine/titles.py`, `TITLES_VERSION`)**: animated trailer-style cards from a JSON spec in `assets.json`: white flash on the cut, letter tracking that slowly opens, brushed-silver fill, glow, light sweep, growing amber rules, film grain, optional photo background with push-in and reveal. Every trailer and episode opens: disclaimer → series title → "by" → Prakash Tiwari over the poster portrait (`portrait_prakash_poster`). Changing one card's spec re-renders only that card (≈17 s).

## Asset catalog and reuse first (catalog.py)

Every file in the studio has one record in `catalog/catalog.jsonl` (on Drive, plain text): checksum, size, media
details, role (source / derived / final / test / archive / preview), production, origin (Flow, ElevenLabs, stock,
own render), prompt or spoken line, licence, credits, tags, description, which registries use it, and duplicates.
Picture embeddings (CLIP ViT-B/32, MIT licence, runs locally) live in `~/VideoStudioRuntime/catalog/` and can be
rebuilt at any time.

- `python catalog.py scan` — incremental; run after adding or generating anything (the build will call it).
- `python catalog.py search "rainy street at night" --kind video` — words and picture similarity together.
- `python catalog.py like <file>` — visually similar assets.
- `python catalog.py need "<what you need>" --kind video --for <production>` — **the reuse-first gate**. It
  searches, logs the query in `catalog/searches.jsonl`, and answers REUSE, REVIEW CANDIDATES, or CHECK STOCK,
  THEN GENERATE. No new AI generation without a logged `need` first; stock is checked next (licence verified),
  generation is last.
- Keep rule: what a production uses is downloaded once at original quality and catalogued with its prompt,
  model, generation id and cost. Unused takes stay on the platform; their record keeps the link.

## Safe cleanup of old revisions (cleanup.py)

- `python cleanup.py plan` — dry run. Writes `catalog/cleanup_plan_<time>.json` with every file it would delete
  and why, and every file it kept and why.
- `python cleanup.py apply catalog/cleanup_plan_<time>.json` — deletes exactly that reviewed list, after checking
  each file is unchanged (checksum) and its keepers still play.
- Keeps the current and previous revision of every file family (`name`, `name_v2` ... , `take_a/b`, dated copies,
  `_old_vN` folders). Removes build intermediates older than their production's final, and identical copies that
  nothing references. `_tests/` and `_archive/` only with `--tests` / `--archive`.
- Never removes: anything a registry references, locked assets, cast intake, identity models, files changed in the
  last 24 hours. Policy: `catalog/keep_policy.json`.
- Redundant files must not take space anywhere: after `apply`, the items it moved to Google Drive Trash are deleted
  forever in the same run (only that run's items; other things in your Trash are never touched). Every deletion is
  logged in `catalog/deletions.jsonl` and the catalog record stays, marked deleted, with its checksum, origin and
  prompt, so we know what we had and could regenerate it.
- Safety comes before deletion instead of after it: the plan is reviewed, protected classes are never candidates
  (registry references, names the platform code loads, the brand kit in `assets/`, originals in `assets/source/`,
  cast and models), and a cross-folder duplicate is only removed when it is a build, test or preview copy.
- Only folders that the apply itself emptied (and that are `_old*` revision folders) are removed; prepared empty
  folders such as a new film's scaffold are never touched.

## Film pipeline (fully digital production)

Start a film: `core/new_film.py F03 "Title" --format feature` (template in `templates/film`). Then:

| Step | Command | What it does |
|---|---|---|
| Pre-flight | `preflight.py --for film` | tools, venv, disk, keys (600, outside Drive), GPU cap, licences, catalog, Resolve, git |
| Script check | `standards.py <film>` | family standards: words, insults, disrespect, clothing, teaching speakers; also runs in every build |
| Reuse first | `catalog.py need "<what>" --kind video --for <film>` → generate → `catalog.py register <file> --ticket ...` | no generation without a logged search; build gate is strict for films |
| Licences | `licences.py check <model>` | every model must be cleared in `02 Libraries/_catalog/model_licences.json` |
| Shot stages | `filmstages.py run <film> [--shots] [--stage] [--backend]` | boards → layout → photoreal → facelock → upscale; fingerprinted, versioned `shots/<stage>/<shot>_vN`, cost ledger |
| Review | `contact.py <film>` | one contact sheet per scene: best frame per shot, stage, version, out-of-date marks |
| Cut | `build.py <film>` | `kind: film` shots use their best stage output; only changed shots re-render |
| Reels | `reelcut.py cut <film>` / `guide` | teaching moments → 9:16 at −14 LUFS + clean dialogue wav; framing-guide preview |
| Resolve | `resolve_film.py <film>` | new dated project: V1 picture … V5 reference; dialogue/FX/music lanes + stems; .drp backup |
| GPU | `gpu.py start/wait/stop/idle-watch/cost` | RunPod v2 API, studio-* pods only, hourly and monthly caps, SSH-only exposure |
| GPU jobs | `jobs.py bootstrap/run/wipe` | ComfyUI over an SSH tunnel; `--minor` jobs end with a verified wipe |
| Worker | `worker/` | setup on the network volume, pinned by `lock.json`; cleared models only |

Rules the code enforces: children's shots never use a hosted backend; blocked licences are refused; the build never
generates media; every GPU minute and stage run is logged.
