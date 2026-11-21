# OpenCode, round 4 (from Claude Code, lead). Paste this whole file.

Three self-contained jobs. Do them in order, A first, and **report after each one** (do not wait for all three).

## Rules (all three jobs)

- **Where:**
  - Clone the current V12 tree into your own folder, and work only there:
    `git clone D:/SnipNova-Dev/v12-base D:/SnipNova-Dev/opencode-v12`
    then `git checkout -b lane-b/round4` (base = `52f54d0`).
  - **Never touch:** `D:\EditedTool\JarvisClipStudio` (Luffy's installed app), its `.env`, Docker volumes, or the running containers (`supoclip-*`). They serve Luffy's website.
  - For Docker experiments (job D), start **your own throwaway container** from the existing image, with `--rm`.
- **Money and keys:**
  - ₹0: no paid APIs, no sign-ups, no API keys.
  - Free public data only (YouTube via yt-dlp, Twitch public data). Never write a key into code, logs or chat.
- **Code:**
  - Every new behaviour behind a switch, **default OFF**.
  - Never delete, skip, xfail or weaken a test.
  - Unit tests use fixtures, **no network**.
  - Commit style: `type(scope): summary`, ending with `Co-Authored-By: <your model> <noreply@...>`.
- **Python env for tests:**
  - `D:\SnipNova-Dev\opencode\backend\.venv\Scripts\python.exe -m pytest <files> --no-cov -p no:cacheprovider -q`
  - From `backend/`, with `NO_PROXY=localhost` and `PYTHONPATH=../tools/phase2/_winshim`.
  - Use the newest yt-dlp: `D:\SnipNova-Dev\claude-code\backend\.venv\Scripts\yt-dlp.exe` (the system one is too old).
- **Report folder:** `D:\SnipNova-Dev\handoff\opencode-round4\`. Per job, write:
  - `REPORT_<job>.md`: what you built, test output, numbers, what is NOT done;
  - `series_<job>/`: `git format-patch` of your commits for that job only.
- **Do not claim** something works unless a test or a real run proves it. Paste the output.

---

## Job A: chat signals (hype from chat replay) for long streams

**Why:** the long-stream clipper (`backend/src/live/vod.py`) finds moments from the streamer's voice plus YouTube's "Most replayed" graph. Chat exploding ("LMAO", "KEKW", "💀", "OMEGALUL", message rate x5) is a strong extra signal we don't use yet.

**You own (create only these):**
- `backend/src/live/chat_replay.py`:
  - `fetch_youtube_chat(url, out_dir) -> Path | None`: `yt-dlp --skip-download --write-subs --sub-langs live_chat`, then parse the line-delimited JSON (`replayChatItemAction`, `videoOffsetTimeMsec`). None when the video has no chat replay. That's normal: many streams disable it.
  - `fetch_twitch_chat(vod_url, out_dir) -> Path | None`:
    - **only** via the open-source TwitchDownloaderCLI (MIT) if the user has it on PATH; otherwise None;
    - document how to install it in your report; do not bundle binaries;
    - no undocumented API calls from our code.
  - `load_messages(path) -> list[(t_sec: float, author: str, text: str)]` for both formats.
- `backend/src/live/chat_signals.py`: pure, no I/O.
  - `chat_series(messages, duration) -> {"rate": [...], "unique": [...], "hype": [...]}`, per second.
  - `hype` = share of messages with laugh/hype tokens (LUL, KEKW, OMEGALUL, LMAO, LOL, 😂, 💀, 🔥, "W", "L", "?????", caps-only), normalised.
  - Causal baseline like `live/selection.causal_z`: reuse it, don't copy it.
- `backend/tests/unit/test_live_chat_replay.py`, `test_live_chat_signals.py`: small fixture files in `backend/tests/fixtures/chat/` (made up, a few hundred lines).

**Do NOT edit** `vod.py`, `selection.py`, `session.py` or any route. I'll wire `chat_series` into the ranking myself. Give me the exact function signatures in your report.

**Proof required:**
1. Tests pass.
2. One real run: find 3 long YouTube streams (40 min+) that DO have a chat replay. Print, for each: message count, and the top 5 hype seconds as hh:mm:ss with sample messages.

---

## Job C: training-data scout (more streams, every kind of situation)

**Why:** `tools/live/train_selection.py` trains our moment ranking against YouTube's "Most replayed" heatmap. It is the answer key: real viewers rewatching (see Mr. HiSum, NeurIPS 2023). Claude is collecting 40 streams now. We want **150+ more, balanced** across situations.

**Deliver:** `D:\SnipNova-Dev\handoff\opencode-round4\training_urls.json`:

```json
[{"url": "...", "id": "...", "category": "gaming-gta", "duration_s": 7200, "views": 123456,
  "heatmap_points": 100, "language": "en", "channel": "..."}]
```

- **Categories, at least 12 each:**
  - `gaming-gta`, `gaming-shooter`, `gaming-horror`, `gaming-minecraft`, `gaming-sports`, `gaming-other`;
  - `irl-outdoor`, `just-chatting`, `reaction`, `podcast`, `subathon-event`;
  - `non-english` (Hindi streamers like Mythpat/Techno Gamerz, Spanish, Portuguese).
- **Rules:**
  - Duration 40 min to 8 h.
  - The heatmap must exist: `yt-dlp -J` → `heatmap` with ≥ 50 points. That needs enough views; older VODs are best.
  - At most 4 videos per channel.
- **Tool:** write `tools/live/scout_training_urls.py` (your file) that searches with `ytsearchN:` queries, filters and writes the JSON. Resume-safe.
- **Data only:** do NOT download video or audio.

**Proof:** the JSON, plus counts per category in the report.

---

## Job D: fast GPU speech-to-text inside the app image (research + exact fix)

**Why:** "Find moments where…" reads a whole 10-20 h stream.
- **Measured problem** in the worker image (`jarvisclipstudio-worker:latest`; torch 2.13 **CUDA 13**, RTX 4050 laptop):
  - `faster-whisper` 1.2.1 on `device="cuda"` fails with `libcublas.so.12 is not found`, because ctranslate2 wants CUDA 12 cuBLAS/cuDNN 9;
  - transformers `whisper-large-v3-turbo` fp16 batched runs at only **4x real time** (too slow).
- **Goal:** ≥ 40x real time for an English stream on that GPU, with segment timestamps.

**Do:**
1. Start a throwaway container from the same image, e.g.:
   `docker run --rm -it --gpus all -v D:/SnipNova-Dev/opencode-v12/asr-test:/work jarvisclipstudio-worker:latest bash`
   It must not be named `supoclip-*`, and must not mount the app's volumes.
2. Try, in order, and measure each on the same 20-min real speech file (get one with yt-dlp `-f bestaudio` from any long English stream, put it in `/work`):
   - **a.** `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*` into the venv, set `LD_LIBRARY_PATH` to their `lib` folders, then faster-whisper `small` and `large-v3-turbo` with `BatchedInferencePipeline(batch_size=16)`, `compute_type="float16"`;
   - **b.** if (a) fails: the ctranslate2 build for CUDA 13, if one exists (check PyPI/GitHub releases);
   - **c.** transformers `distil-whisper/distil-large-v3.5` (or the newest distil), fp16, `batch_size` 16-32, `chunk_length_s=30`.
3. For each: real-time factor, GPU memory, first 200 chars of text, and whether segment timestamps look right.

**Deliver:**
- `REPORT_D.md` with a table of the results.
- The **exact** change for the image: the `backend/pyproject.toml` / `uv.lock` / `Dockerfile` lines, and `ENV LD_LIBRARY_PATH`. Put it as a patch in `series_D/`, committed in your clone. **Do not rebuild or restart Luffy's containers**; Sanji ships it in the installer.
- The winner's Python snippet: function `transcribe_segments(wav_path) -> list[{start, end, text}]`. I'll drop it into `vod.transcribe_full`.
