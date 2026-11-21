# CODEX — YOU ARE NOW THE LEAD ENGINEER OF SNIPNOVA

From Claude (the lead until now), 2026-11-20. Luffy (the owner) is handing lead authority to you, with full permission on his laptop. Read this whole brief before you touch anything.

---

## 1. The project in one paragraph

**SnipNova / JarvisClipStudio** is a local-first AI video clipper, built to beat OpusClip at ₹0. It turns long videos and **live or finished streams** into 9:16 viral clips. It has:
- word-timed karaoke captions;
- a face-on-top / gameplay-below streamer layout;
- AI moment picking;
- music, SFX and B-roll;
- an AI clip editor;
- social publishing.

It's a fork of SupoClip (AGPL-3.0). Stack:
- **Backend:** FastAPI, raw SQL with asyncpg, ARQ workers on Redis, ffmpeg, MediaPipe/OpenCV.
- **Frontend:** Next.js 15, React 19, Tailwind v4, Better Auth, Prisma.
- **Infra:** PostgreSQL 15, plus a LiteLLM gateway for AI routing. Everything runs in Docker on Luffy's Windows 11 laptop (RTX 4050).

## 2. Who is who

| Who | Role | How they work |
|---|---|---|
| **Luffy** | Owner. Every product decision is his. | Talks in chat, often in CAPS. Wants speed, real results and honesty. Hates claims without proof. |
| **Codex (you)** | **Lead engineer from now on.** Full authority over code, the build, deploying to his installed app, and briefing the other agents. | Runs on Luffy's laptop. |
| **Claude Code** | The lead until now; built the engine, backend, live clipping and fast VOD clipping. Out of weekly budget, so steps back to advisor. | Same laptop. Work history is in `D:\SnipNova-Dev\v12-base` (git) and `D:\SnipNova-Dev\handoff\`. |
| **Muse AI ("Sanji")** | Integration, UI, test gates, packaging (builds the release ZIPs). | Works in **its own cloud sandbox**. It CANNOT see this laptop. The only bridge is ZIP files Luffy uploads and downloads. Strong on tests and honest reports. |
| **OpenCode, Antigravity** | Extra helper agents. | Given self-contained briefs (examples: `D:\SnipNova-Dev\handoff\opencode-round4\`, `antigravity-round1\`). Antigravity sometimes freelances, so give it strict rules. |

## 3. Folders (exact paths)

| What | Path |
|---|---|
| **Installed app (what Luffy uses, http://localhost:3107)** | `D:\EditedTool\JarvisClipStudio` |
| **Newest package from Muse: V13 FAST (the ZIP)** | `D:\Downloads\JarvisClipStudio-SnipNova-V13-FAST-20261120.zip` |
| The same package, already unzipped (read it, don't edit it) | `D:\SnipNova-Dev\packages\V13-FAST\JarvisClipStudio-SnipNova-V13-FAST-20261120\` |
| Read these package notes first | `FAST_BUILD_NOTICE.md`, `KNOWN_LIMITATIONS_V13_FAST.md`, `INSTALLATION_GUIDE.md`, `install.ps1` / `verify.ps1` / `rollback.ps1` |
| **YOUR working repo (create it, see Job 0)** | `D:\SnipNova-Dev\codex\JarvisClipStudio` |
| Your briefs to other agents, reports, handoffs | `D:\SnipNova-Dev\handoff\codex-roundN\` |
| Claude's older git tree (reference only; its features are all in V13) | `D:\SnipNova-Dev\v12-base` |
| Muse's research reports (the build queue comes from these) | `D:\Downloads\GAP_REPORT.md`, `BUG_REPORT.md`, `FINAL_REPORT.md` |
| Data you can reuse | `D:\SnipNova-Dev\selection-data` (15 streams, used to train the picker), `D:\SnipNova-Dev\hook-study` (26 viral Shorts with 10M+ views), `D:\SnipNova-Dev\ext\neonclipping` (MIT repo with ideas) |
| Python venv that already runs the test suite | `D:\SnipNova-Dev\opencode\backend\.venv\Scripts\python.exe` |

The project's own guide is `CLAUDE.md` / `AGENTS.md` in the repo root. Read it: it covers the architecture, every env var and every route.

## 4. Rules. Luffy set these; they are not optional.

1. **Keys.** You may USE the API keys on this laptop: `D:\EditedTool\JarvisClipStudio\.env` and the `compose.override.yml` env. **Never print, echo, log, paste or commit a key.** Read keys from the env files, never from chat. Never put a key in a test, a report or a git commit.
2. **₹0 spending.** Free tiers only. No paid credits, no sign-ups that need payment. No Gemini. DeepSeek stays unfunded (no credit).
3. **Installed app safety.** Before any install, back up the app (`install.ps1` does this). **Never delete or overwrite its `.env`, Docker volumes (database, uploads) or backups.** Run `docker compose` from a neutral cwd, using: `docker compose -p jarvisclipstudio --project-directory "D:\EditedTool\JarvisClipStudio" -f "D:\EditedTool\JarvisClipStudio\docker-compose.yml" -f "D:\EditedTool\JarvisClipStudio\compose.override.yml" ...`. Never set your terminal's working directory INSIDE the install folder: a locked folder broke an installer once.
4. **Every new behaviour goes behind an env switch.** Switched off, the output must be the same as before. Once a feature is proven, switch it ON in Luffy's install.
5. **Tests.** Never delete, skip, xfail or weaken a test. If a test pin contradicts a deliberate product change, prove it from the code, update the pin, and say why.
6. **No claims without proof.** "Works" means a test passed or a real render on a real video showed it. Show Luffy actual clips or frames.
7. **Commits:** `type(scope): summary`, one logical change per commit.
8. **Don't log in to his accounts** (Instagram, YouTube and so on) and don't type passwords.
9. **YouTube bot wall.** Heavy per-video probing makes YouTube ask this IP to "Sign in to confirm you're not a bot". Batch requests and space them out.

## 5. Luffy's product rules (locked; never undo them)

- **Captions:** white, and each word turns **yellow as it's spoken and stays yellow** (karaoke fill). Never red.
- **NO emoji** anywhere on clips. Luffy adds his own.
- **Titles:** never "I/me". Write them in third person with the streamer's name ("Kai Cenat…"). Small, at the **very top**, never over the face. A title on/off switch exists; respect it.
- **Clips are at least 30 seconds** (strict).
- **Swear words show as `***`** (full stars).
- **Face-on-top layout:** streamer cam on top, gameplay below. Reject moments where the streamer's face isn't on camera (no intro screens, no faceless clips). Users can also draw the face and gameplay boxes by hand.
- Quality goal: clips that look like real 10M+ view streamer Shorts, not "zoom + flash + whoosh, repeat".

## 6. What exists now (V13 FAST)

Shipped and proven:
- **Live clipping** at `/live`, while a stream is still live.
- **Fast VOD clipping of finished 10–20 h streams** (audio first, then only the chosen sections are downloaded). Measured: 11.1 h → 9 clips in 26 min. A Kai Cenat VOD gave 28 clips with Groq captions.
- A trained moment picker (YouTube "most replayed" heatmap labels, +68% on held-out streams).
- Hand-drawn boxes; karaoke captions; quote titles; swear masking; the 30 s floor; the funny voice changer; a 310-SFX / 123-music library; B-roll; the AI editor.

In V13 but switched **OFF**:
- `HOOK_FINISH_V2` (the opening: 1.3× face zoom that settles, a sound hit in the first second, title on frame 1);
- `VIEW_SEQUENCING` (split → full gameplay → full face → split);
- `LOOP_ENDINGS`, `PAYOFF_TIGHT`, `COMMENT_BAIT`, `BEAT_DENSITY`, `CAPTION_WORD_FIX`, `TRANSLIT_HINGLISH`;
- `SENSEVOICE_FEATURES` stays off. It's unproven.

Known bug: with `VIEW_SEQUENCING` on and no recorded peak, the sequence anchors about 4.8 s early. Muse has a fix in progress.

---

## 7. YOUR JOBS, in order

### Job 0: set up and install V13 (do this first)
1. **Your repo:** copy the package's `JarvisClipStudio\` folder to `D:\SnipNova-Dev\codex\JarvisClipStudio`. Run `git init`, then commit the baseline as `chore: baseline V13 FAST (land/wave2 35975edf + swear fix)`. All your work happens here, on branches.
2. **Install V13 into Luffy's app** following `INSTALLATION_GUIDE.md` (`install.ps1` backs everything up first, then `verify.ps1`).
   - The SHA-256 checksum comes from Muse's delivery message. Ask Luffy for it, or compute it and show him.
   - Keep his `.env`, volumes, music and SFX.
   - If any step's output doesn't match the guide, STOP and tell Luffy.
3. **Prove it works:** open http://localhost:3107, run one finished-stream job on the Live page with the Kai Cenat VOD (https://www.youtube.com/watch?v=VZqAPdUvqnQ, 3 clips) and show Luffy the clips.
4. **Turn the wave-2 switches on one at a time** in the install env (not `SENSEVOICE_FEATURES`). Render the same clip before and after each one, and keep a switch on only if the after-clip is clearly better. Report a table: switch, on/off, evidence.

### Job 1: AI connections (Luffy asked for this directly)
Right now Groq (`openai/gpt-oss-120b`) is effectively the only brain, and it has a 20 requests/min limit.
- Route ALL text-LLM calls through the **LiteLLM gateway** that's already in the stack (`litellm_config.yaml`, service `litellm`, `OPENAI_BASE_URL=http://litellm:4000/v1`). Use an ordered fallback list:
  1. **NVIDIA NIM**: Nemotron and the other free NIM models (key already in the env);
  2. **Groq**;
  3. **OpenRouter free models** (`:free` variants only);
  4. local **Ollama** last.
- Fail over automatically on 429, timeouts and 5xx, with retries and cooldowns. Log which model answered, but never the key.
- Speech-to-text: keep Groq Whisper first and local faster-whisper as the fallback. Fix faster-whisper on the GPU (cuBLAS 12 vs the CUDA 13 image) so the fallback isn't slow on the CPU.
- Prove it: block Groq on purpose (a bad key in a test env only) and show a job still finishing through NIM or OpenRouter.

### Job 2: editing variety (Luffy's #1 complaint: "only zoom, flash, whoosh, repeat")
Build from Muse's `GAP_REPORT.md` Part 3:
- **#8 Punch-in engine:** zoom in (max ~1.12, asymmetric ease) on punchlines and key words, driven by word timings and loudness peaks. Port OpenShorts `punch_in.py` (MIT, credit it in NOTICE).
- **Varied transitions:** pick from the existing 248-transition library by moment type (whip, shake, speed ramp, a hard black cut for dark jokes) instead of the same flash every time. Never the same one twice in a row.
- **Reaction-matched SFX (#21):** bind SFX to events, e.g. vine boom on a shocked face, laugh track on a laugh, record scratch on a sudden stop. Choose by moment type from the 310-SFX library, keep them subtle, and duck them under speech.
- **#13 Hype-word accent:** names, numbers and game titles in a second accent colour (green), with the spoken word still yellow.
- **#6 Styled titles:** proper title typography and auto-fit, not flat white drawtext. Keep the title rules from §5.
- **#5 Beat-density audit:** no stretch of more than 3 s with nothing changing.
- Prove it with 3 real Kai Cenat clips, before and after.

### Job 3: wiring and gaps
- **Live clips → Clips library + AI editor.** Live/VOD clips should show up as a normal task, so they can be edited in the studio (`/tasks/{id}/edit`), get B-roll and be exported.
- **Feedback buttons** (BUG_REPORT P2-1): 👍/👎 or 1–5 stars on clip cards, calling `sendClipFeedback()` in `frontend/src/lib/clip-actions.ts`. Then refit the picker's weights from the ratings (idea: NeonClipping `learn.py`, ridge regression shrunk toward the defaults, after 8 ratings).
- **Vocal separation toggle** (BUG_REPORT P2-2): `separation_opt_in` in the create form.
- **More layouts for drawn boxes:** picture-in-picture in each corner, circle cam, side by side, gameplay only (see NeonClipping `layouts.py`).
- **Kick and Twitch links** for VOD and live, alongside YouTube.

### Job 4: the next package with Muse
When a batch is proven, write `D:\SnipNova-Dev\handoff\codex-roundN\TO_MUSE.md` and export the commits as a patch series (`git format-patch`) plus a ZIP for Luffy to upload. Muse runs the full test gate and builds the installer package. You deploy proven pieces straight into Luffy's install as you go, because he wants changes live on his website, not only in packages.

---

## 8. How to talk to Luffy
- Short, plain words. Lead with the result.
- Anything you need from him goes in a **highlighted box, in CAPS**.
- Show real clips and frames as proof. Say plainly what's not done or not verified.
- He's in India (IST). Prices in ₹.

Good luck. The engine is solid; what's missing is the editing and the variety.
