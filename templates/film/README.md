# {{TITLE}} ({{CODE}})

Made from `00 Platform/templates/film` by `new_film.py`.

| Folder | Holds |
|---|---|
| story/ | script.md, logic_ledger.md, story bible notes |
| cast/ | cast.json, one folder per person: photos/, video/, voice/, _model/ (identity model) |
| sets/ | set briefs, reference boards, world exports |
| props/ | hero props and their states |
| shots/boards → layout → render → photoreal → final | one stage per folder; file names `<shot>_<stage>_vN.ext` |
| audio/ | dialogue, music, sfx |
| build/ | engine output (rebuildable; cleaned by the keep policy) |

Shot entry in production.json (stages are cached by input fingerprint, like every build step):

    {"id": "SC01_010", "dur": 4.0, "kind": "film", "set": "home_night", "cast": ["prakash"],
     "camera": "35mm, eye level, slow push", "action": "...", "lines": ["prakash_010"],
     "stages": {"layout": "unreal", "photoreal": "wan_vace", "facelock": true, "upscale": "4k"}}

Rules: reuse first (`catalog.py need` before any generation, `catalog.py register` after);
the reuse gate is strict for this production; content standards are checked on every script change.
