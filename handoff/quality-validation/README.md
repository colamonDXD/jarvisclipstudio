# Validation renders: corrected engine on 6 content types (2026-11-18)

**Settings.**
- **Before:** the old-engine renders from 17 Nov.
- **After:** the same fixed, sentence-aligned windows (deterministic from the transcript), so before and after show the same moment.
- **Switches on for "after"** (all are off by default in the product): `EDL_CONTENT_CUTS`, `FIT_PAD=ambient`, `REFRAME_HIRES_PROBE`, `CAPTION_LOOP_FILTER`, `CAPTION_PHRASE_SPLIT`, `SCREEN_SUBJECT`, `CLIP_CAP_KEEP_VISUAL_TAIL`.
- **Off for "after":** `SELECTION_EVENTS`, so the windows match.
- Built on `lane-a/phase2-auto-editor`; fix `02c4503` used for `after_fix`.
- No AI selection (no key in the dev copy): these runs test editing, not moment choice.

**Files per type:** `before_after.mp4` (side by side), `after.mp4`, `before_every3s.png` / `after_every3s.png`, `ffprobe_after.txt`, `render_log.txt`, both reports. Vlog and talking head also have `after_fix*`. Overview: `ALL_before_after_sheet.png`, `vlog_talkinghead_fix_sheet.png`.

| Type | Source | Before | After | Honest verdict |
|---|---|---|---|---|
| Finance (animated explainer) | wIxRKlb_1uk | 6 segments, 5 hard cuts, 1 zoom; blurred bars | 4 segments, 0 zooms; clean tinted bars | **Better** (no blur, calmer). The picture is still letterboxed: the source is 16:9 graphics, so fit is right. |
| Podcast (video call) | 1oXjtK5Nsjw | 9 segments, two people stacked | 7 segments, 0 zooms, same stack | **Same layout, calmer edit.** No regression. |
| Talking head (speech + stock cutaways) | TkHlhgTutFs | Person crops | First run: **small fit (worse)**. After the fix: crops again | **Regression found and fixed** (`02c4503`): a lit object in a cutaway was taken for a screen. After the fix it matches before, with no blur. |
| Vlog | D3cAyFmhi_M | 19 segments, 1 zoom; first shot blurred fit | First run: **small fit (worse)**. After the fix: 12 segments, 0 zooms, crops, no blurred opening | **Better after the fix** (same cause as the talking head). |
| Comedy (stand-up) | dwZK6iQlyEw | 13 segments, 1 zoom, no music | 6 segments, 0 zooms, comedy bed | **Calmer**, same framing. Music on: the meme_comedy profile now has a bed (by Luffy's request). |
| Gaming (facecam stream) | kai_sa_20000_hd | 15 segments, 4 zooms, pixelize | 7 segments, 0 zooms, no pixelize, same facecam stack | **Calmer**, same layout. |

**Not verified here:**
- AI moment selection (no key).
- Step 4 event selection on these types: it was off, to keep the windows equal. On clip1 it found the right moment.
- B-roll inserts (no stock keys in the dev copy; Openverse is off in unit tests, on in the product only when enabled).
- Linux/Docker.
- Viewers' opinion: Luffy watches at 18:00.
