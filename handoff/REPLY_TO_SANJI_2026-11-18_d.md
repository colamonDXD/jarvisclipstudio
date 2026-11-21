# To Sanji, from Claude Code (2026-11-18, numbered)

**1. Luffy's quality complaint (clip1.mp4): root cause found, fix under way.**
Full report: `D:\SnipNova-Dev\handoff\quality-clip1\ROOT_CAUSE.md`, with frames.
- The clip came from my test page. It uses the product's own `build_edl` and reframe planner.
- **The moment:** a fixed fallback window, because there was no AI key. It cut off the payoff (the winner announcement).
- **The edit:** `build_edl` slices clips into **equal intervals** by cuts per minute: Gaming gave 21 slices in 58 s. It also:
  - snaps them to the beats of the **music bed the app adds**;
  - punches in every 12 s ("rehook");
  - pushes in over the opening;
  - flashes on the bed's beats.
- **The blur:** "fit" pads with a blurred copy of the frame. On 4K wide shots the face probe finds nobody, so most shots fell back to fit.
- **The captions:** the transcription looped one phrase over crowd noise.
- **Step 1 is in progress** (`EDL_CONTENT_CUTS`, default on, `off` restores the old edit):
  - cuts only at source shot changes or sentence ends, with cuts per minute as a ceiling;
  - no interval or opening zooms;
  - punches only on speech, at most one per 8 s;
  - the signature transition only on a real shot change;
  - no snapping or flashing to the added bed.
- **Next:** steps 2 to 5 (no blur filler; screen-as-subject layout; selection that sees non-speech events; caption loop filter). Luffy watches the corrected render before anything is called fixed.

**2. Asset library (Luffy: download wide, ship clean).** It's built and gated:
- `tools/assets/check_licences.py` prints `ship-clean` or blocks.
- Every shipped file has an entry in `assets/ASSET_MANIFEST.json`: source, author, licence (CC0, PDM or original), sha256.
- Sources:
  - FreePD: 267 tracks verified against archived freepd.com pages and the archive MD5s;
  - Kenney: CC0 packs;
  - Openverse: CC0 Freesound SFX;
  - our own synthesised meme pack.
- Not used: myinstants, Pinterest and klipy (no licence), and Pixabay Music and Mixkit (no redistribution).
- New switches:
  - `ASSET_VARIANTS` (each clip gets its own track and SFX variant);
  - `BROLL_OPENVERSE` (B-roll with **no API key**: CC0 photos as Ken Burns clips, the same relevance gate and vision veto).
- Docs: `docs/assets/ASSET_LIBRARY.md`. The patches follow once the curation finishes.

**3. OpenCode Round 2 is ready for you.** `D:\SnipNova-Dev\handoff\opencode-r2\`:
- a 248-entry CPU transition library and a burned-in caption detector;
- 2 patches; I re-ran their tests here: 259/259 pass;
- library only, nothing wired into rendering.
Round 3 (a transition catalogue API with previews, and an OFL font pack with a licence manifest) is running.

## Questions
**Q14. Package size.** Curated music is about 8 tracks × 12 moods (≈ 150–250 MB at 160 kbps, first 120 s each), plus about 300 small SFX. Which do you want?
- (a) Ship it all inside V12.
- (b) Ship the core 7 + 6 + the meme pack, and run a one-command fetch at install (it verifies sha256 against the manifest).

**Q15. Freeze.** Steps 1, 2 and 5 can land by 19 Nov; steps 3 and 4 need 19–20 Nov.
- Will you take quality fixes that miss 20 Nov 10:00 IST as a V12.1 patch, with switches off until Luffy approves the render?

**Q16. Defaults in Studio Console.** Luffy's bad clip used niche **Gaming on an IRL awards vlog**.
- Does the create form auto-suggest the niche?
- If not, can Auto (no niche) be the default, so a vlog never gets Gaming pacing?

**Q17. RAM on Luffy's PC.** A `llama-server` process held 4.2 GB, leaving 0.4 GB free (16 GB machine), and a render test failed with an x264 out-of-memory error.
- Is a local LLM part of V12?
- If yes, V12's workers and it will compete for memory during renders. Worth a cap, or a note in the install guide.

— Claude Code
