# Niche profiles

The 14 editing profiles the Auto-Director classifies into and renders
with. Source of truth: `backend/src/auto_director/profiles/*.json`
(loaded by `edl.load_profile`). Eleven are auto-detected by the
classifier; three are manual-only (an editor picks them explicitly —
the classifier never returns them). The per-clip Viral Intelligence
Engine (see `docs/viral-intelligence.md`) layers clip-level decisions
on top of these defaults; anything the plan does not decide, the
profile decides.

Fields used below: pacing = target cuts per minute; transition =
default join + duration; punch = punch-in zoom scale + trigger; SFX =
whether accents may come from the user's own SFX library; B-roll =
stock cutaways per minute.

## Auto-detected profiles

### podcast_news
- **Understands:** spoken discussion, interviews, news-style talk.
- **Editing grammar:** 10–15 cuts/min; hard cuts (0.05s); punch 1.25 on emphasis; hormozi captions; no LUT.
- **Hook preferences:** statement titles, 2s.
- **Sound policy:** SFX off — the voice carries the clip; B-roll 0.5/min allowed.
- **Music policy:** none. No bed under speech by design.

### hindi_podcast
- **Understands:** Hindi/Hinglish conversation; captions are rendered as Roman Hinglish.
- **Editing grammar:** 10–16 cuts/min; hard cuts (0.05s); punch 1.15 on emphasis; hormozi captions.
- **Hook preferences:** statement titles, 2s.
- **Sound policy:** SFX from the user library on; B-roll 0.5/min.
- **Music policy:** calm bed at 0.15, ducking −12 dB.

### true_crime
- **Understands:** narrated crime/mystery storytelling.
- **Editing grammar:** 10–15 cuts/min; hard cuts (0.05s); punch 1.25 on emphasis; classic_box captions.
- **Hook preferences:** statement titles, 2s.
- **Sound policy:** SFX on but near-dry by design — a single low `sting` on the final reveal peak; B-roll 0.5/min.
- **Music policy:** none.

### meme_comedy
- **Understands:** jokes, deadpan delivery, punchline timing.
- **Editing grammar:** 15–25 cuts/min; instant cuts (0.0s), zoom_bounce on punchlines; punch 1.35 on punchline; large_centre_pop captions; punchy LUT.
- **Hook preferences:** contrast titles, 2s.
- **Sound policy:** SFX from the user library on.
- **Music policy:** none.

### gaming
- **Understands:** gameplay footage and streamer reactions.
- **Editing grammar:** 15–30 cuts/min; instant cuts; punch 1.2 on action; tiktok_animated captions; punchy LUT.
- **Hook preferences:** statement titles, 2s.
- **Sound policy:** SFX from the user library on.
- **Music policy:** energetic bed at 0.18, ducking −12 dB.

### sports
- **Understands:** match footage, highlights, sports talk.
- **Editing grammar:** 12–20 cuts/min; cuts (0.05s); punch 1.2 on energy peaks; bold_uppercase captions; punchy LUT.
- **Hook preferences:** statistic titles, 3s.
- **Sound policy:** SFX from the user library on.
- **Music policy:** energetic bed at 0.2, ducking −10 dB.

### education_tutorial
- **Understands:** teaching, explainers, step-by-step content.
- **Editing grammar:** 12–18 cuts/min; cuts (0.05s); punch 1.15 on key concepts; hormozi captions.
- **Hook preferences:** question titles, 2s.
- **Sound policy:** SFX off; B-roll 0.5/min.
- **Music policy:** calm bed at 0.1, ducking −15 dB (deepest ducking of any profile — speech clarity first).

### finance_business
- **Understands:** money, markets, business analysis.
- **Editing grammar:** 8–12 cuts/min (slowest spoken profile); hard cuts (0.05s), slide on topic change; punch 1.1 on key metrics; hormozi captions.
- **Hook preferences:** statistic titles, 2s.
- **Sound policy:** SFX off; no B-roll.
- **Music policy:** corporate bed at 0.15, ducking −12 dB.

### vlog_lifestyle
- **Understands:** personal vlogs, day-in-the-life, lifestyle content.
- **Editing grammar:** 15–25 cuts/min; cuts (0.05s), fade on topic change; punch 1.1 on personal moments; tiktok_animated captions; punchy LUT.
- **Hook preferences:** story titles, 2s.
- **Sound policy:** SFX from the user library on; B-roll 1.0/min.
- **Music policy:** upbeat bed at 0.2, ducking −12 dB.

### travel_vlog
- **Understands:** travel footage where the place is the content.
- **Editing grammar:** 12–20 cuts/min; wipes (0.4s — real crossfades); gentle punch 1.08 on scenic reveals plus a whole-clip Ken Burns drift; classic_box captions; warm_soft LUT.
- **Hook preferences:** question titles, 3s.
- **Sound policy:** SFX off; B-roll 2.0/min (densest of any profile).
- **Music policy:** calm bed at 0.25 (loudest bed of the spoken profiles), ducking −12 dB.

### music_artist
- **Understands:** music performance, where the track itself is the content.
- **Editing grammar:** 15–25 cuts/min; instant cuts; punch 1.2 on energy peaks; neon captions; punchy LUT.
- **Hook preferences:** statement titles, 2s.
- **Sound policy:** SFX off — nothing over the performance.
- **Music policy:** none added. The clip's own audio is the music.

## Manual-only profiles

### football_edit
- **Understands:** beat-driven football edits (skills, goals, aura edits). Chosen by the editor, never auto-detected.
- **Editing grammar:** 25–40 cuts/min; instant cuts; punch 1.3 on energy peaks; bold_uppercase captions; punchy LUT.
- **Hook preferences:** statement titles, 2s.
- **Sound policy:** SFX from the user library on.
- **Music policy:** phonk bed at 0.3, ducking −6 dB (music rides high — it is half the edit).

### japan_travel_cinematic
- **Understands:** cinematic Japan travel footage; image-first, grade-led.
- **Editing grammar:** 10–20 cuts/min; wipes (0.4s); softest punch of any profile (1.05 on scenic reveals) plus Ken Burns; classic_box captions; warm_soft LUT.
- **Hook preferences:** statement titles, 2s.
- **Sound policy:** SFX off; B-roll 2.0/min.
- **Music policy:** calm bed at 0.25, ducking −12 dB.

### anime_funk_edit
- **Understands:** anime edits cut to Brazilian funk — visual/beat-driven, not transcript-driven.
- **Editing grammar:** 60–120 cuts/min (fastest profile by far); instant cuts; punch 1.3 on energy peaks; minimal captions; punchy LUT.
- **Hook preferences:** statement titles, 2s.
- **Sound policy:** SFX from the user library on.
- **Music policy:** funk bed at 0.3, ducking −6 dB.
