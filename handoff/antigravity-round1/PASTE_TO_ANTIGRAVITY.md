# Antigravity: two jobs from Claude Code (lead). Paste this whole file.

## STRICT RULES (breaking any = the work is thrown away)

1. **Work only in your own clone:**
   ```
   git clone D:/SnipNova-Dev/v12-base D:/SnipNova-Dev/antigravity-v12
   cd D:/SnipNova-Dev/antigravity-v12 && git checkout -b lane-c/round1
   ```
2. **NEVER touch:**
   - `D:\EditedTool\JarvisClipStudio` (Luffy's live app), its `.env`, Docker containers or volumes;
   - `D:\SnipNova-Dev\v12-base`, `D:\SnipNova-Dev\claude-code`, `D:\SnipNova-Dev\opencode*` (other agents' work).
   - No `docker` commands at all.
3. **Edit ONLY the files listed under each job.** Need another file? **STOP** and write the reason in your report. Do not "improve", refactor, rename, reformat or "clean up" anything else, even if it looks wrong.
4. **No new dependencies**, except the ones a job explicitly allows.
5. Never delete, skip, `.only`, xfail or weaken an existing test. Never change what an existing test asserts unless the job says so.
6. **New behaviour is OFF by default** behind the switch named in the job.
7. **₹0:** no paid APIs, no sign-ups, no API keys. Free, open-licence assets only, with the licence recorded.
8. **No `git push`.** Commit locally with `type(scope): summary`.
9. **Don't claim it works** without pasting the test output. "Should work" is not accepted.
10. **When blocked or unsure:** STOP, write the question in `REPORT_<job>.md`, and wait. Do not guess and do not do something else instead.

**Report folder:** `D:\SnipNova-Dev\handoff\antigravity-round1\`. Per job:
- `REPORT_<job>.md`: files changed, test output (pasted), what is NOT done;
- `series_<job>/`: the output of `git format-patch lane-c/round1-base..HEAD` for that job only. Before starting, run `git tag lane-c/round1-base`.

**Backend tests:**
- `cd backend`
- `set NO_PROXY=localhost` and `set PYTHONPATH=../tools/phase2/_winshim`
- `D:\SnipNova-Dev\opencode\backend\.venv\Scripts\python.exe -m pytest <files> --no-cov -p no:cacheprovider -q`

---

## Job E: emoji stickers that pop on reactions (backend module + assets, NOT wired in)

**Goal:** OpusClip-style emoji stickers. 😂 pops when the streamer laughs, 💀 on a fail, 🔥 on hype, 😱 on a scream, at the moment, with a short pop/bounce. Claude wires it into the renderer later. You build the pieces.

**Files you may create (only these):**
- `assets/stickers/emoji/*.png`:
  - about 40 reaction emoji from **Google Noto Emoji** (Apache-2.0, `googlefonts/noto-emoji` on GitHub, the `png/512` folder);
  - only reaction-type emoji: 😂🤣💀🔥😱😭😡🤯👀💯🙏😳🥶🤡👑💸🚨❗❓✅❌🎯🏆💥⚡🫡😤😎🤔🙄😬🥲😈👻🧠🫠🗿 (those that exist).
- `assets/stickers/STICKERS_MANIFEST.json`: per file `{"file", "emoji", "source_url", "license": "Apache-2.0", "sha256"}`.
- `backend/src/media/stickers.py`, with:
  - `stickers_enabled()`: env `STICKERS`, default **off**.
  - `choose_stickers(signals_by_time, words, duration, max_per_clip=4, min_gap=4.0) -> list[dict]`:
    - `signals_by_time` is a list of `{"t": sec, "signals": ["laugh"|"scream"|"f0"|"vocal"], "strength": float}`;
    - `words` is a list of `{"text", "start", "end"}`;
    - rules: laugh → 😂/🤣, scream → 😱, words like "dead/died/rip/fail" → 💀, "fire/insane/crazy/let's go" → 🔥, "money/cash/paid" → 💸, a question at the end of a line → 🤔;
    - returns `[{"t", "emoji", "file", "x": 0..1, "y": 0..1, "scale": 0..1, "dur": sec}]`, never covering the centre caption band (y 0.40-0.60), with at most `max_per_clip`, at least `min_gap` apart, and the strongest first;
    - deterministic: the same input gives the same output (seed by the clip id argument `seed: str`).
  - `sticker_overlay_args(events, width=1080, height=1920, assets_dir=None) -> tuple[list[str], str, str]`:
    - the ffmpeg `-i` inputs plus a `filter_complex` fragment that overlays each PNG over `[0:v]` from `t` to `t+dur` with a pop-in (scale 0.6 → 1.1 → 1.0 over 0.25 s) and a fade-out over the last 0.2 s;
    - returns `(inputs, graph, out_label)`.
- `backend/tests/unit/test_stickers.py`:
  - rules → stickers;
  - the caption band is avoided;
  - max count and spacing;
  - determinism;
  - an ffmpeg graph that **actually renders**: build a 3 s 1080x1920 black clip with `ffmpeg -f lavfi`, apply the graph with 2 stickers, assert the output exists and that the frames at the sticker times differ from the black frame;
  - manifest: every PNG listed, sha256 matching, licence Apache-2.0.
- `tools/assets/fetch_noto_stickers.py`: downloads the PNGs and writes the manifest. Raw GitHub URLs only.

**Allowed dependency:** none (numpy, cv2 and ffmpeg are already there).

**Proof:** test output pasted, plus `evidence/sticker_demo.mp4`: 5 s, 2 stickers popping on a test clip. Put a 3-frame strip PNG in the report.

---

## Job F: frontend tests for Claude's new UI (tests only)

**Context:** Claude changed the Create page and added a Live page:
- **Create page:**
  - `frontend/src/components/layout-picker.tsx` (5 picture cards: Auto, Face on top red/gold, Full screen, Crop);
  - in `frontend/src/components/home-app.tsx`, the old Framing toggle and the Gaming-only "Gaming layout" row were REPLACED by `<LayoutPicker>`;
  - a "Title on clips" `<Switch>` sends `hook_title_enabled`;
  - `layout_template` is now sent for any niche, not only gaming.
- **Live page:** `frontend/src/app/live/page.tsx`.

**Files you may edit or create (only these):**
- Update to the new UI, keeping every behaviour they assert:
  - `frontend/src/components/home-app-framing.test.tsx`
  - `frontend/src/components/home-app-gaming-layout.test.tsx`
  - `frontend/src/components/home-app-template.test.tsx`
  - `frontend/src/components/home-app-broll.test.tsx`
- New:
  - `frontend/src/components/layout-picker.test.tsx`
  - `frontend/src/app/live/page.test.tsx`

**Rules for F:**
- **Do NOT edit any non-test file.** If a component is untestable, STOP and report.
- **Behaviours that must stay asserted** (same meaning, new clicks):
  - framing auto/fit/crop is sent as `framing_mode`;
  - `layout_template` is sent only when a face-on-top card is picked, and the credit only then;
  - `template_mode` is always sent.
- **New assertions:**
  - picking "Full screen" sends `framing_mode: "fit"` with no `layout_template`;
  - picking "Face on top · gold" sends `layout_template: "facecam_gold"` and `framing_mode: "auto"`;
  - the "Title on clips" switch OFF sends `hook_title_enabled: false` (ON by default sends `true`);
  - Live page: `/api/live/status` disabled shows "not switched on"; a 422 with `detail.message` shows that message in a toast; a session in `mode: "vod"` shows its `progress_message`; "Skipped moments (n)" lists reasons.
- **Setup:** `cd frontend && pnpm install && pnpm vitest run <files>`. Paste the full output, and run the whole suite once too (`pnpm vitest run`), pasting the summary.
