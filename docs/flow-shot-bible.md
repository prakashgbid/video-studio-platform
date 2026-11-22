# Flow shot bible — Prakash Tiwari (v2, 8-angle consultant studio)

Every Google Flow clip begins with two things attached: one of these eight baseline frames, used as its starting image, and the `@me` avatar, which supplies the face and voice. The prompt text below keeps the room, wardrobe and light consistent.

- Local files, vertical for reels (2K, 1536×2752, 9:16): `~/Movies/video-pipeline/refs/v2/` (+ `contact_sheet.jpg`)
- Local files, horizontal for YouTube (2K, 2752×1536, 16:9): `~/Movies/video-pipeline/refs/v2-16x9/` — same 01–08 names and roles; 02 and 03 keep him on the right third so the left side is free for on-screen graphics
- In Flow: project **"BASELINE - Consultant Studio (8 angles)"** → in any project, **+** → switch the picker's project to BASELINE → pick the frame → Add to prompt. The master image is named **"MASTER set - wide establishing"** (searchable).
- Previous look (white shirt, no blazer): `refs/v1-original/` — retired.

| # | File | Shot | Use it for |
|---|---|---|---|
| 01 | `01_wide_establishing.jpg` | Wide establishing, at desk | Opening scene-setter, long-form chapter cards, reveal |
| 02 | `02_medium_A-cam.jpg` | Medium, straight to lens (main talking head) | **Default intro** ("Hi, I'm Prakash…"), main delivery |
| 03 | `03_closeup.jpg` | Tight close-up to lens | The one key point, emphasis, CTA / sign-off |
| 04 | `04_three-quarter_B-cam.jpg` | 45° B-cam, gesturing, mid-sentence | Cutaway during long talking sections (interview feel) |
| 05 | `05_side_profile_working.jpg` | Side profile at monitors | B-roll: "here's how I'd approach it…" |
| 06 | `06_over_the_shoulder.jpg` | Over-the-shoulder onto architecture diagrams | B-roll before a diagram / screen segment |
| 07 | `07_glass_board_explainer.jpg` | Standing at glass board, pointing | Explaining a framework or model |
| 08 | `08_lounge_chair.jpg` | Lounge chair by window | Long-form intro/outro, reflective or story segments |

## The studio (paste into every prompt)

> Same man, same office, same wardrobe and same lighting as the reference image. Private studio office of an independent Chief Enterprise Architect consultant: large dark walnut executive desk, two slim frameless monitors on a dual arm showing a dark-mode enterprise architecture diagram, slim aluminium laptop, minimalist brass desk lamp, black leather desk mat; floor-to-ceiling walnut shelving with curated hardcover books and warm LED strip lighting; frosted glass board with a hand-drawn architecture sketch; tall fiddle-leaf fig; charcoal acoustic wall panels; large window with soft diffused daylight; cognac leather lounge chair in the reading corner. Wardrobe: tailored navy blazer over a crisp white open-collar dress shirt, dark trousers, silver wristwatch. Soft key light from camera left, warm practicals, gentle bokeh. Photorealistic, cinematic corporate documentary look, vertical 9:16, steady camera, no logos, no on-screen text.

## Video prompt template (Frames → Video, or Ingredients)

Attach the frame + `Me` avatar, then:
```
@me <ACTION for this angle, e.g. "looks into the lens and says">: "<LINE, ≤ 18 words>". Subtle natural movement, slow gentle push-in. [The studio paragraph]
```

## Rules
- One spoken line per clip, ≤ ~18 words (8 s).
- Reels: face only in the 6–8 s intro (02), optional sign-off (03). Long-form: 02/08 intro, 01/04/07 chapter openers, 05/06 B-roll, 03 outro.
- Images: Nano Banana 2.1, 0 credits; video: Omni 1.2 Flash, ~48 credits per x2 run at 720p/8 s.
- Plan: Google AI Ultra (25,000 monthly credits). Downloads: grid → right-click → Download → images 2K (upscaled) / video 1080p (upscaled, free, takes ~1 min; run one upscale at a time) — 4K costs 50 credits.
- Pipeline crops 9:16 → 4:5 biased toward the top (`crop=1080:1350:0:(ih-1350)*0.2`).
- Pick the set by format: reels/LinkedIn → `refs/v2/` (9:16, crop to 4:5); YouTube → `refs/v2-16x9/` (16:9, no crop). In Flow both sets live in the BASELINE project.
