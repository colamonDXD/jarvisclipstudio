# SNIPNOVA: FULL PROJECT HANDOFF FOR FCC CLOUD

From Claude Code (the lead engineer until 2026-11-20), 2026-11-21. Luffy (the owner) is adding you to keep the project moving. This brief contains everything you need from the start. Read all of it before you act.

---

## 1. What the project is

**SnipNova** (the installed app is called **JarvisClipStudio**) is a local-first AI video clipper, built to beat OpusClip at **₹0**. It turns long videos, and **live or finished streams (YouTube, Twitch, Kick)**, into vertical 9:16 short clips ready to go viral.

What it has:
- AI moment picking, with a picker trained on YouTube "most replayed" data;
- word-timed karaoke captions;
- a streamer layout with the face on top and the gameplay below, or boxes the user draws by hand;
- music, SFX and B-roll;
- an AI clip editor ("studio") and social publishing.

It's a fork of SupoClip (AGPL-3.0).

Stack:
- **Backend:** FastAPI, raw SQL with asyncpg, ARQ workers on Redis, ffmpeg, MediaPipe/OpenCV, Whisper.
- **Frontend:** Next.js 15, React 19, Tailwind v4, Better Auth, Prisma.
- **Infra:** PostgreSQL 15, plus a LiteLLM gateway for AI routing.

Everything runs in **Docker on Luffy's Windows 11 laptop** (RTX 4050, 6 GB VRAM). The site is **http://localhost:3107**.

Main tabs:
- **Create:** paste a link or upload a long video, then get clips.
- **Clips:** the library.
- **Live:** clip a live stream, or a finished 10–20 h stream, fast.
- **Campaigns**, **Agents & API**, **Settings**.

## 2. Who is who

| Who | Role | How they work |
|---|---|---|
| **Luffy** | Owner. Every product decision is his. | Talks in chat, often in CAPS, fast and direct. Wants speed, real results and honesty. Hates claims without proof. In India (IST); prices in ₹. |
| **FCC Cloud (you)** | New AI engineer continuing the work. | If you run in the cloud you CAN'T see his laptop; work through ZIP and patch files Luffy uploads and downloads. If you run on his laptop, follow every rule in §4. |
| **Codex** (OpenAI) | **Current lead engineer** (since 2026-11-20), with full authority. Runs on Luffy's laptop. | Repo `D:\SnipNova-Dev\codex\JarvisClipStudio`, branch `feature/live-quality`. Honest, careful reports. |
| **Claude Code** | Previous lead: built the engine, live clipping, fast VOD clipping and the picker training. Now advisor only, low on weekly budget. | Old git tree `D:\SnipNova-Dev\v12-base`. Handoffs in `D:\SnipNova-Dev\handoff\`. |
| **Muse AI ("Sanji")** | Integration, UI, test gates, release packages (installer ZIPs). | Works in **its own cloud sandbox**; ZIPs are the only bridge. Very thorough with tests. |
| **OpenCode, Antigravity** | Helper agents for self-contained jobs. | Need strict, complete briefs. Antigravity sometimes freelances. |

## 3. Folders and paths (on Luffy's laptop)

| What | Path |
|---|---|
| **Installed app (what Luffy uses)** | `D:\EditedTool\JarvisClipStudio` |
| Codex's working repo (newest code) | `D:\SnipNova-Dev\codex\JarvisClipStudio` (git, branch `feature/live-quality`, tip `8ee10e2` or later) |
| Codex's test clips and scripts | `D:\SnipNova-Dev\codex\` (best proof clips: `verified-kai\final\`, `verified-kai\caption-proof\release\`) |
| Newest release package (V13 FAST, installed 2026-11-20) | `D:\Downloads\JarvisClipStudio-SnipNova-V13-FAST-20261120.zip`, unzipped at `D:\SnipNova-Dev\packages\V13-FAST\` (SHA-256 `00766a61675af0e268d55475daa15c32ffefd705c479ecf4f4dfcd15b9a413f8`) |
| All handoffs and briefs | `D:\SnipNova-Dev\handoff\` (`codex-round1\PASTE_TO_CODEX.md`, `codex-round2\TO_MUSE.md`, `TO_MUSE_boxes\`, `opencode-round4\`, …) |
| Muse's research | `D:\Downloads\GAP_REPORT.md` (viral patterns + OpusClip comparison + ranked build queue), `BUG_REPORT.md`, `FINAL_REPORT.md` |
| Data | `D:\SnipNova-Dev\selection-data` (picker training streams), `D:\SnipNova-Dev\hook-study` (26 viral Shorts with 10M+ views), `D:\SnipNova-Dev\ext\neonclipping` (MIT repo, ideas) |
| Python venv that runs the backend tests | `D:\SnipNova-Dev\opencode\backend\.venv\Scripts\python.exe` |

The project guide is `CLAUDE.md` / `AGENTS.md` in the repo root: architecture, env vars, routes. Read it.

**Docker commands** (always from a neutral folder, never from inside the install folder):
```
docker compose -p jarvisclipstudio --project-directory "D:\EditedTool\JarvisClipStudio" -f "D:\EditedTool\JarvisClipStudio\docker-compose.yml" -f "D:\EditedTool\JarvisClipStudio\compose.override.yml" <command>
```
- Containers: `supoclip-frontend`, `-backend`, `-worker` (Create jobs, one at a time), `-live-worker` (Live tab), `-ai-worker`, `-postgres`, `-redis`, `-mcp`, plus `litellm`.
- `backend/src` is bind-mounted, so a restart picks up Python changes. The frontend needs `build frontend` + `up -d --no-build --no-deps frontend`.

## 4. Hard rules (Luffy's)

1. **API keys:** they live in `D:\EditedTool\JarvisClipStudio\.env` and the `compose.override.yml` env. You may USE them. **Never print, paste, log or commit a key**, and never ask Luffy to paste one into chat.
2. **₹0:** free tiers only. No paid credits, no Gemini. DeepSeek stays unfunded.
3. **Installed app safety:** back up before changing anything. Never delete or overwrite `.env`, Docker volumes (database, uploads) or backups. Never restart the `worker` while a task is processing unless Luffy says so; it kills the job.
4. **Every new behaviour goes behind an env switch.** Switched off, the output must be the same as before. Once a feature is proven, switch it ON in his install. **Luffy only sees what's on his website, so ship proven work there.**
5. **Tests:** never delete, skip, xfail or weaken a test. Change a pin only when a deliberate product change requires it, and say why.
6. **No claims without proof:** a test, or a real render on a real video. Show Luffy real clips and frames.
7. Commits: `type(scope): summary`.
8. Don't log in to his accounts or type passwords.
9. YouTube bot-wall: heavy per-video probing triggers "Sign in to confirm you're not a bot". Batch requests and space them out.

## 5. Luffy's product rules (locked; never undo them)

- **Captions:** white, and each word turns **yellow as it's spoken and stays yellow** (karaoke). Never red.
  - About **5% of frame width** (around 52 px at 1080 wide); he said "way too big" at 80 px.
  - 2–3 words per line, bold, black outline, centred on the face/gameplay seam.
  - **Every spoken word must appear.**
- **NO titles on clips by default.** Luffy adds titles himself. The "Title on clips" switch exists and defaults OFF.
- **NO emoji** on clips.
- If titles are ever on: never "I/me", third person with the streamer's name, small, at the very top, never over the face.
- **Clips are at least 30 seconds.**
- **Swear words show as `***`**, but only real swears (fuck, shit, bitch, slurs). "Damn" and "hell" stay.
- **Face-on-top layout:** streamer cam on top, gameplay below. Reject faceless moments (intro screens). Hand-drawn boxes are available on Live and Create.
- **Quality bar:** clips must look like real 10M+ view streamer Shorts and Reels, not "zoom + flash + whoosh, repeat".

## 6. Current state (2026-11-21)

**Installed and working:**
- V13 FAST, plus Codex's later work up to about `8ee10e2`:
  - Draw boxes on Live and Create (only shows when a non-Auto layout is chosen), 11 layouts;
  - titles off by default; smaller captions;
  - the free AI gateway (NVIDIA NIM → Groq → OpenRouter free → Ollama, with failover);
  - offline GPU Whisper for Live; live clips saved to the library.
- The **Live tab's fast mode** for finished streams: audio-only download → voice scan → trained picker → transcribe only the candidates → LLM picks → download only those sections → render. Measured 11.1 h → 9 clips in 26 min. A Kai Cenat VOD gave 28 clips.
- Video length limit raised to **6 hours** (`MAX_VIDEO_DURATION`, `PRO_`/`SCALE_YOUTUBE_MAX_VIDEO_DURATION` = 21600 in the install `.env`). A backup of the old `.env` is in `D:\SnipNova-Dev\handoff\`.

**Running right now:** Create task `9495f237-e7db-401c-9b91-5b51a5114cb5`, a 5.5 h Twitch VOD (`https://www.twitch.tv/videos/2934325173`, "GTA Gaming & Gooning Marathon"). It's on the **old slow path**, expected to take 3–4 h. Don't restart the worker while it runs.

**Wave-2 features in V13 (switch defaults: check the install env):** `HOOK_FINISH_V2`, `VIEW_SEQUENCING`, `LOOP_ENDINGS`, `PAYOFF_TIGHT`, `COMMENT_BAIT`, `BEAT_DENSITY`, `CAPTION_WORD_FIX`, `TRANSLIT_HINGLISH`. `SENSEVOICE_FEATURES` stays off; it's unproven. Muse's full V13 package (switches flipped on, view-sequencing peak fix) may still be coming.

## 7. Open problems, in priority order

1. **The Create tab is slow on long videos** (Luffy's top complaint today). It downloads the whole video and transcribes everything with openai-whisper `medium` at about 30% GPU; word timing falls back to the CPU because Triton is missing. A 5.5 h VOD took 2 h+ just to transcribe, and the progress bar sits at 30% while "min left" climbs (158 → 219 → 323), which looks broken. **Fix:**
   - (a) send Create sources over 30 min through the Live tab's fast engine (`backend/src/live/vod.py`), keeping every Create-form setting and landing clips in the normal task page and library;
   - (b) shorter sources use faster-whisper `large-v3-turbo` on CUDA (float16, batched, VAD) or parallel Groq Whisper chunks;
   - (c) real sub-progress and an honest time estimate.
   - Luffy wants this in **Create**, not Live.
2. **Cancel doesn't free the worker.** A cancelled job kept running for 15+ min (the cancel flag `task_cancel:{id}` is only checked between stages), so the next task sat on "Waiting for a worker" until Claude restarted the worker. **Fix:**
   - check the flag during downloads, transcription and rendering;
   - kill yt-dlp and ffmpeg child processes;
   - skip cancelled jobs on worker start;
   - show "waiting behind <task>".
   - Note: `WORKER_MAX_JOBS=1`, so cron jobs pile up behind long tasks.
3. **The Create tab lacks the Live-tab quality work.** Port everything to the normal task pipeline (`task_service` / `video_service` → `create_optimized_clip`):
   - captions from Groq or local Whisper, not YouTube auto-captions (those are sparse: 32 words in 53 s), with a coverage gate;
   - a gentler streamer-word filter (keep a word unless OCR clearly matches it to game subtitles);
   - view cuts around the moment (`live/game_shots.py`), reaction zooms, matched SFX.
4. **Editing variety** (GAP_REPORT Part 3):
   - punch-in engine on punchlines (port OpenShorts `punch_in.py`, MIT);
   - varied transitions from the 248-transition library;
   - SFX bound to events (vine boom on shock, laugh, record scratch) from the 310-SFX library;
   - hype-word accent colour;
   - a beat-density audit (no static stretch over 3 s);
   - loop endings.
5. **The picker:** in Codex's test, NeonClipping's first-stage finder beat our default (0.574 vs 0.450). Add its signals (optical-flow motion, cut density, 2–4 kHz shout ratio, loudness jump against a 2-minute baseline, exclamation lexicon) as features and re-test. Keep the experimental context picker off.
6. **Wiring:** 👍/👎 or star feedback buttons on clip cards (`sendClipFeedback()` in `frontend/src/lib/clip-actions.ts`) so the picker learns Luffy's taste; and the vocal separation toggle (`separation_opt_in`).

## 8. How to work with Luffy

- Lead with the result, in short plain words. Anything you need from him goes in a **highlighted box, in CAPS**.
- Prove with real clips. Say plainly what's not done or not verified. Never say "works" without proof.
- Coordinate with Codex (the current lead) so two agents never edit the same files at once. Ask Luffy which open problem from §7 you own before you start, unless he has already told you.
- If you're cloud-based, deliver work as `git format-patch` files plus a ZIP with a short README of what to apply and how to prove it.
