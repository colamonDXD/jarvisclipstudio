# V2 Fixes — the 2026-11-10 evening fix wave

**This build:** SnipNova Consolidated **v2** — branch `next-milestone`
@ `4eda669bededc653b256747986c86f645e7d26f5`. It is the Consolidated
build (`8eeeb46`, installed and verified on Luffy's machine
2026-11-10: verify.ps1 **126 passed, 0 failed**) plus the six merges
below, and nothing else. The first fix wave (auto-niche classifier
chain, B-roll relevance, Music Auto fallback, rerank fix, info-card
fix, watch-folder orphan recovery, Brand Kit bumpers, SFX upload,
NVENC pre-stage, per-clip timings, i18n backfill) is recorded in
`CONSOLIDATED_FIXES.md` and is included unchanged.

**Gate this build ships on** (merged tree @ `4eda669`, adjudicated):
effective backend **3,585 passed / 0 failed / 2 skipped**, frontend
**827 passed / 0 failed**, `tsc --noEmit` 11 errors byte-identical to
the freeze baseline, migration safety PASS, Reference Styles bridge
4/4. The main gate run's 26 backend non-passes were two sandbox
artefacts (the 512 MB `/tmp` tmpfs tripping the production
disk-space guard in the media-replication tests, and ffmpeg SIGKILL
under overlapped runs); both affected files were re-proven **37
passed / 0 failed** in a clean serial re-run. No product code changed
during adjudication. Full record: `GATE_ADJUDICATION_4EDA669.md`.

| # | Item | Merge | Proof |
|---|---|---|---|
| 1 | **Face-in-blur guard** (self-run audit #5 / KNOWN_ISSUES Q10) | `bad2c81` | The reframe planner refuses any fit that places a grounded face inside a blurred pad band; the guard is judged on the **observed** face boxes, not the median box (the median understated the true extent — caught and fixed inside the lane). Lane commits `e6aaab3`, `325adca`, `b13a93f`, `c352fdb`, `427f775`. Japan harness, same fixture: the defective range's subject coverage **17.28 → 18.36**; r2 17.16 → 16.90 (−0.26, documented measurement-variance class); r3 6.83, r4 18.51, r5 23.84 unchanged; all renders ok. Post-merge reframe family (guard + wire + editor) **74 passed / 0 failed**; after-frame visually verified (split layout, both faces fully sharp, no face in any blur band). Report: `research_notes/face-in-blur/report.md`. |
| 2 | **Reference Style label confidence** (self-run audit #4 / KNOWN_ISSUES R1) | `38909ee` | Both audit cases reproduced on the clean branch first: Japan slice → japan_travel_cinematic 0.2422 over travel_vlog 0.3211 (**margin 0.0789**); funk 3.2 cpm → podcast_news / true_crime **tied at 1.1618 (margin 0.0)**. The API and UI now expose winner + margin + runner-up + close-call flag; close-call threshold **margin < 0.25** (0.5 was measured and rejected — it would leave "confident" reachable only at 80 cpm). Confident: "Built on the {niche} style". Close: "Closest pacing match: {niche} — nearly tied with {runner-up}". Scorer, winner and all 14 profiles **unchanged**. Lane commits `8acb11f`, `d690d87`, `5ded0e5`; backend reference-style 48/0, neighbours 53/0, frontend 13/13, tsc 11 baseline-identical; post-merge backend **48 passed**. Report: `research_notes/sprint-7day/REF_LABEL_FIX_REPORT.md`. |
| 3 | **Thumbnail intelligence — scored cover-frame selection** | `5e98c9f` | The bundle cover (made in exactly one place, `write_deliverable_bundle`) used a blind fixed-moment grab. New module `backend/src/media/cover_selection.py` routes that timestamp through `choose_cover_timestamp()`: ~1 fps sampling capped at **24 candidates + the historical frame** on a 480-wide analysis copy; YuNet face detection with Haar fallback; Laplacian sharpness; exposure rejection (mean luma outside [10, 245] can never win); ranking face → sharpness → face area; a challenger must be *clearly* better. **Hard fallback: the chooser never raises — any failure returns the historical timestamp byte-identical.** Lane commits `3a9531f`, `39fec10`, `157a079`. Real-render proof on the Japan fixture (identical inputs, old code = `bad2c81`): r1 cover face area **2.2% → 14.2%**, sharpness 586 → 1578; r2 **1.6% → 17.2%**, 328 → 913; a no-face punch-in cover replaced by a face frame. 23 new tests; neighbouring suites 119/0; brand-kit render 6/6 on the scored frame; post-merge **34 passed / 0 failed** (cover + bundle + brand-kit render). Report: `research_notes/thumbnail-intelligence/report.md`. |
| 4 | **Parkfix — Find Moments tasks auto-proceed** | `de4d2a8` | Luffy's live complaint (2026-11-10): a Find Moments task sat ~20 minutes at "waiting for your pick" (19 candidates) and would have waited forever. The select-moments logic was **extracted** (not forked) into `TaskService.apply_moment_selection(...)`; a new arq cron (`auto_pick_stale_selections`, every minute) sweeps tasks still in `awaiting_selection` past **`TASK_SELECTION_AUTOPICK_MINUTES`** (default 15) and picks the top-ranked slate via the shared path. Race-safe three ways (advisory lock + status re-read + conditional flip; a user's pick always wins; a second sweep no-ops). Honest marker in the checkpoint, the task metadata, and the progress message: **"Auto-picked the top moments — no selection was made within 15 minutes."** Tasks parked before the fix are rescued via the `updated_at` fallback. Both Find Moments cards and the MomentPicker now state the deadline up front. Lane commits `25b4a1a`, `255aaa3`, `6c320f3`, `3838d60`, `5423b8b`, `032b423`; lane backend 75 + 63 + 8, frontend 20 + 314; post-merge backend **75 passed / 0 failed**, frontend **20 passed / 0 failed** (incl. changelog test after the union-resolved CHANGELOG conflict). Report: `research_notes/parkfix/report.md`. |
| 5 | **Multitrack Stage B — Overlays lane (second visual lane)** | `0bb58f8` | Premise code-verified first: the studio editor had exactly one visual lane (full-frame stock B-roll); no overlay field existed anywhere. Built: `OverlayItem {id, start, end, asset, name, x, y, scale}` with B-roll's semantics (0.2 s minimum, non-overlap, cut-aware mapping); upload endpoint (magic-byte-sniffed PNG/JPEG/WebP ≤ 8 MB, server-generated asset names, ffprobe-verified, ownership-checked); renderer compositing order **base → B-roll → overlays → captions**; timeline lane + inspector + preview layer in the same order. Lane commits `d52206d`, `1763c1a`, `2934116`, `bcced28`, `a2e8d20`, `c6f932b`. **Parity: full ffmpeg argv byte-identical vs base `de4d2a8`** for plain and B-roll drafts. Render proof: red base + blue B-roll (1–4 s) + lime overlay (2–3 s) — centre pixel blue at 1.5 s, **lime at 2.5 s with the corner still blue**, blue at 3.5 s, red at 5.0 s; frame visually verified. Backend new suite 32/32, editor family 171 green; frontend full 814 passed / 0 failures; post-merge backend **102 passed / 0 failed**, frontend **22 passed / 0 failed**. Report: `research_notes/multitrack/report-stage-b.md`. |
| 6 | **Multitrack Stage C — per-lane hide/lock controls** | `4eda669` | All six studio lanes (video, B-roll, overlays, captions, hook, audio) gain hide/show and lock. Hidden lanes' items are excluded from exports (never deleted); locked lanes disable their panel, inspector and shortcut edits. Document-model fields are additive with defaults reproducing today's behaviour. Lane commits `2035db4`, `4b5422f`, `17ef33a`, `29e0bfc`, `9c5aee3`, `85f0ea1`. Parity byte-identical when all lanes are visible and unlocked; post-merge backend **105 passed / 0 failed**, frontend **19 passed / 0 failed**. Report: `research_notes/multitrack/report-stage-c.md`. |

**Deliberately not in this build:** Stage A of multitrack (closed as
satisfied-by-existing-structure — music and SFX already live as
separate panels with separate state in the clip editor; the studio
audio lane is a source-waveform lane with nothing to split, and
building lanes there would have been fake UI); the deferred six
(adaptive VRAM manager, YouTube channel monitoring, voice/music
separation, reference font/caption-text reading, viral scout pack
completion, keyframes/arbitrary-lane multitrack); the F-S1
scheduled-publishing kill-switch (needs Luffy's decision);
auto-niche **live** proof — still pending his fresh clip (tracker row
1.7 stays UVP; checklist item 1).

**Five states for this wave:** IMPLEMENTED yes (all six) ·
INDIVIDUALLY TESTED yes (lane suites + render/frame proofs above) ·
INTEGRATED yes (merged into `next-milestone`, full adjudicated gate
green on the merged tree) · CROSS-FEATURE VERIFIED partially
(overlays over B-roll under captions proven; parkfix cross-checked
against pipeline/resume/billing suites; Brand-Kit bumper over an
overlay export not exercised) · USER-MACHINE VALIDATION PENDING
(checklist items 12–15).
