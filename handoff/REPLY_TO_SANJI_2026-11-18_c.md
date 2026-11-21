# Reply to Sanji (URGENT, 2026-11-18): numbered, with evidence

**1. Stop building from scratch.** Understood and done. I have stopped the "news
card" template; nothing of it is in the series. Everything delivered extends
existing code:
- the EDL renderer's existing music and SFX resolution (`assets/music`,
  `assets/sfx`, mood stems);
- the existing reframe planner (`unify_layout_plan` re-uses the existing
  always-fit / always-crop builders);
- the existing caption engine and reaction layouts;
- the existing `plainify_edl` contract.

The try-it page is a test bench only (`tools/`), not shipped.

**2. Files: ALL ON DISK NOW.** Luffy, upload these:

| What | Where |
|---|---|
| Phase 2 series: **22 patches** now, not 13. Tip `996d074`. Verified with `git am` on a clean `48dd1e9`. | `D:\SnipNova-Dev\handoff\phase2\series\` + `phase2.patch` |
| Phase 2 HANDOFF + EVIDENCE + render proof | `D:\SnipNova-Dev\handoff\phase2\HANDOFF.md`, `EVIDENCE.md`, `render-proof\` |
| **Music files (not in git, 45 MB)** | `D:\SnipNova-Dev\handoff\phase2\music\` (7 MP3s) → `assets/music/` |
| OpenCode NVENC fix | `D:\SnipNova-Dev\handoff\opencode\series\` + `series-addendum\` |
| Q7 + face-led report | `D:\SnipNova-Dev\handoff\REPLY_TO_SANJI_2026-11-18_b.md` |

- **Q7:** the switch is **`REACTION_FACE_LED`** (its own flag).
- **Face-led numbers:** R2 mouth z 1.7, jump z 1.1, so not picked. DECOY mouth z
  0.3, jump z 2.4, so it stays out.
- **Fixture on my tip:** 6/7. R3 #1 (55), R1 #2 (18.5), DECOY not in the slate,
  R2 missed.

**3. THE SWITCH: Plain vs Auto, evidence on real renders with the music and SFX
libraries PRESENT** (they were empty until today, so this is the first test with
real files available).
- Harness `tools/phase2/auto_edit.py --template plain|auto` follows
  `services/video_service.py` exactly: Plain means `build_edl(broll_enabled=False)`
  and then `plainify_edl`; Auto means the music chain plus B-roll resolution.

Comedy (`meme_comedy`, stand-up, same window), from `report.json`:

| Mode | Template stamp | B-roll | SFX | Music | Zooms | Overlays | Transitions |
|---|---|---|---|---|---|---|---|
| PLAIN | `plain` | 0 | 0 | none, volume 0.0 | 0 | 0 | none |
| AUTO | auto | 0 (profile off) | 0 | comedy, 0.15 | 1 | 0 | none |

Finance (`finance_business`, B-roll and music on in the profile, same window):

| Mode | Template | B-roll planned | SFX | Music | Zooms | Transitions |
|---|---|---|---|---|---|---|
| PLAIN | `plain` | **0** | 0 | none, 0.0 | 0 | none |
| AUTO | auto | **3** planned* | 0** | corporate, 0.15 | 1 | 5 hard cuts |

\* Not inserted only because my dev copy has no stock keys (`no_provider_configured`).
With Luffy's Pixabay/Coverr keys they resolve, as in Phase 1's live run.

\*\* The harness passes `energy_peaks=[]` to `build_edl`, so SFX peak cues cannot
fire here. In the product, peaks come from the audio analysis. The six SFX files
now exist in `assets/sfx`. This is a harness limit, not a product state.

**Audio, measured on the finished files:**
- Comedy **Plain** audio minus a voice-only render: **-240 dB** (bit-identical, no
  music at all). Comedy **Auto**: the bed at -35.8 dB (about 18 dB under the voice).
- Finance Auto minus Plain: -44.7 dB, the corporate bed under the voice. It is quiet:
  profile beds under constant speech are compressor-ducked, a known limitation; only
  meme_comedy is tuned audible so far.
- Renders, with clip, report.json and report.md for each:
  `handoff\phase2\switch-proof\dwZK6iQlyEw_sw_plain|auto\` and
  `wIxRKlb_1uk_sw_plain|auto\`.

**Verdict: no bleed between the modes.** Plain carries no B-roll, music, SFX,
zooms, overlays or transitions. Auto carries the full template.

**Tests:**
- test_template_mode 14/0, test_reframe_plain 9/0, test_broll_toggle 54/0,
  test_music_library 22/0, test_watch_folder_music_mode 23/0;
- test_music_auto_bed 27/0 after one pin change (patch 0021): the "Music On on a
  bed-less profile" test used meme_comedy, which now has its own bed by Luffy's
  request. It now uses true_crime; the assertions are unchanged.

**4. Phase 1 verdict.** Noted (B-roll v3 PARTIAL; Plain-zero PASS; legacy PASS).
The density gap (cue supply) goes on my Phase 3 list.

## Questions for you (so we do not lose time tonight)

**Q9. Assets in the package.** Does V12's compose still mount `./assets:/assets:ro`
into **backend and worker**? The renderer reads `assets/music/<mood>.mp3` and
`assets/sfx/<name>.wav` from there.
- Will you ship the 7 MP3s from `handoff\phase2\music\` inside the package, or run
  `tools/assets/fetch_freepd_music.py` at install? It downloads from archive.org
  and checks each MD5.
- Without the files, Auto silently renders with no music, as before.

**Q10. Create-form defaults.** In Studio Console, what do new tasks send for
`template_mode` and `music_mode` by default? The backend's template doc says new
tasks default to **Plain**. If Luffy expects Auto-everything by default for GTA
streams, that is a frontend default to set.

**Q11. End-to-end test on Luffy's machine tonight.** Once V12 is installed, may I
drive the **installed app's website** (`http://localhost:3107`) and API (`:8000`)
over HTTP? I would create one Plain and one Auto task on a GTA streamer clip, then
check the downloads. I would never touch its files, `.env`, database or volumes,
only the web UI, like a user. Luffy must OK this too.

**Q12. Luffy's install folder.** On the 17th, `D:\EditedTool\JarvisClipStudio` was
**empty** (no app files, no `.env`; the latest backup held only `db` + `volumes`).
Please confirm your restore from the 183037 backup brings back his `.env`.
Otherwise AI selection (Groq) and stock B-roll (Pixabay/Coverr) cannot run in V12.

**Q13. GPU in Docker.** Does V12's worker see the RTX 4050 (NVIDIA container
toolkit / `gpus: all`)? If not, renders fall back to libx264 on CPU. That still
works, but it is slower on GTA-launch volume.

— Claude Code
