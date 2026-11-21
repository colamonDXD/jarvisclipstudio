# OpenCode Round 4 — Job A report

**Job:** chat signals (hype from chat replay) for long streams
**Workspace:** `D:\SnipNova-Dev\opencode-v12`
**Branch:** `lane-b/round4` (base `52f54d0`)
**Commit:** `f56c6d2 feat(live): ingest and score chat replays for long streams`
**Patch:** `series_A/0001-feat-live-ingest-and-score-chat-replays-for-long-str.patch`

## What I built

Two new modules. Nothing is wired into `vod.py`, `selection.py`, `session.py` or any route, as instructed.

### `backend/src/live/chat_replay.py`

| Function | Signature | Behaviour |
|---|---|---|
| `fetch_youtube_chat` | `(url: str, out_dir: Path, timeout: float = 900) -> Optional[Path]` | Runs the same yt-dlp binary as `live.vod` with `--skip-download --write-subs --sub-langs live_chat`; returns the new `*.live_chat.json`. `None` when the stream kept no replay. A non-zero yt-dlp exit with no file raises `RuntimeError` with the stderr tail. |
| `fetch_twitch_chat` | `(url: str, out_dir: Path, timeout: float = 900) -> Optional[Path]` | Only uses `TwitchDownloaderCLI` (MIT) when it is on `PATH`. `None` when the CLI is absent, the URL is not a VOD, or the download is empty. No undocumented API calls. |
| `load_messages` | `(path: Path) -> list[tuple[float, str, str]]` | Parses yt-dlp's line-delimited `live_chat.json` and TwitchDownloaderCLI's `comments` JSON. Skips engagement/subscription notices and records without a video offset. Sorted by time, stable within a second. |
| `twitch_vod_id` | `(url: str) -> Optional[str]` | Extracts the numeric id from `twitch.tv/videos/<id>`. |

yt-dlp renders custom emoji as a run with no text and a `shortcuts` list (e.g. `:rolling_on_the_floor_laughing:`); those become the message text instead of being dropped.

### `backend/src/live/chat_signals.py`

Pure functions, no I/O:

| Function | Signature | Behaviour |
|---|---|---|
| `chat_series` | `(messages: Iterable[tuple[float, str, str]], duration: float) -> dict[str, list[float]]` | Per second: `rate` (messages), `unique` (distinct authors), `hype` (share of that second's messages that carry a hype token, 0..1). `ceil(duration)` seconds; messages outside the range are ignored. |
| `chat_zscores` | `(series: dict[str, Sequence[float]], params: dict[str, float] | None = None) -> dict[str, numpy.ndarray]` | Calls `live.selection.causal_z` for all three series, so a second is compared with the minutes before it. It does not copy the baseline code. |
| `is_hype_message` | `(text: str) -> bool` | LUL, KEKW, OMEGALUL, LMAO, LOL, 😂, 💀, 🔥 (or their `:shortcut:` names), a lone `W`/`L`, a run of 3+ question marks, or an all-caps message of 4+ letters. |

**Switch:** the modules live under `src/live`, whose every entry point (route, worker) is gated by the existing `LIVE_CLIPPING` flag, default off (`live/__init__.py:23`). Nothing calls the new modules yet, so product behaviour is unchanged until the lead wires `chat_series` into the ranking.

## Tests

Command (from `backend/`):

```
NO_PROXY=localhost PYTHONPATH=../tools/phase2/_winshim \
D:\SnipNova-Dev\opencode\backend\.venv\Scripts\python.exe -m pytest \
  tests/unit/test_live_chat_replay.py tests/unit/test_live_chat_signals.py \
  --no-cov -p no:cacheprovider -q -rs
```

```
.........................................                                [100%]
41 passed in 1.23s
```

**PASS** — 41 tests, 0 failed, 0 skipped. Log: `evidence_A/tests_chat.txt`.

The 41 cases cover: YouTube and Twitch fixture parsing; whole-file and malformed-line input; emoji-shortcut runs; empty files; the yt-dlp command shape; `None` when a stream has no replay; a failed yt-dlp raising; Twitch CLI absent/present/non-VOD; hype and non-hype tokens; per-second counts; ceiled duration and out-of-range messages; empty chat; a burst lifted above a 400 s quiet baseline by `causal_z`; fixture-to-series integration. All subprocess and `shutil.which` calls are mocked; no network.

Regression run of the existing live unit tests:

```
20 passed, 4 skipped in 12.78s
```

**PASS** (with 4 pre-existing skips). Log: `evidence_A/tests_live_regression.txt`.

The 4 skips are `tests/unit/test_live_worker.py:80,99,113,124`, which skip themselves when `DATABASE_URL`/`TEST_DATABASE_URL` is unset. They are not skipped or weakened by this change; no database was configured for this job.

## Real run

Three finished YouTube streams, each over 40 minutes, each with an actual chat replay. Script: `%TEMP%\opencode\chat_proof_final.py`; full output: `evidence_A/chat_proof.txt` and `chat_proof.json`.

| # | Video | Channel | Duration | Messages | Unique authors |
|---|---|---|---|---|---|
| 1 | `Jn8a0b481cg` | highkeyoli | 01:30:16 | 9,527 | 2,018 |
| 2 | `n1qreRsqcPo` | Bahmania Gaming | 01:08:34 | 560 | 174 |
| 3 | `R0pmRdfHpX4` | Eddy Blox | 01:52:06 | 36,323 | 3,873 |

Top hype seconds (ranked by number of hype messages in the second, then share, then rate; `rate_z` / `hype_z` are `chat_zscores` against the causal baseline):

**`Jn8a0b481cg`**

```
00:00:00  msgs=54 hype=0.20 (11) rate_z= 0.0 hype_z=  0.0 | WERE WAITING || NOWWWW || SCAM
01:09:30  msgs=12 hype=0.83 (10) rate_z= 6.7 hype_z= 66.7 | ENDDD UPDATE PLEEASEEEEEE || WWWWW || WHAAAT
01:09:18  msgs=13 hype=0.77 (10) rate_z= 7.4 hype_z= 61.5 | WHATTGTTTTTTT || OOOOOOOOOOOOOOO || HOLY SHIT
01:09:24  msgs=10 hype=0.80  (8) rate_z= 5.4 hype_z= 64.0 | WTTTTT || WHAT || THIS IS SOOO PEAK
01:09:38  msgs=13 hype=0.54  (7) rate_z= 7.4 hype_z=  0.9 | OH MY GOD || YEEES || YESSSS...
```

**`n1qreRsqcPo`** (sparse chat, max 3 messages/s; single-message hype seconds are the honest top)

```
00:12:50  msgs=1 hype=1.00 (1) rate_z= 4.0 hype_z= 80.0 | NOOB NOOB NOOB NOOB
00:13:06  msgs=1 hype=1.00 (1) rate_z= 4.0 hype_z= 80.0 | BEN TÜRK
00:20:26  msgs=1 hype=1.00 (1) rate_z= 4.0 hype_z= 80.0 | :face_with_tears_of_joy: x10
00:27:02  msgs=1 hype=1.00 (1) rate_z= 4.0 hype_z= 80.0 | ????hg
00:27:55  msgs=1 hype=1.00 (1) rate_z= 4.0 hype_z= 80.0 | :rolling_on_the_floor_laughing: x2
```

**`R0pmRdfHpX4`**

```
00:33:35  msgs=26 hype=0.73 (19) rate_z= 6.1 hype_z= 2.8 | w || w || w
00:33:26  msgs=15 hype=0.80 (12) rate_z= 2.4 hype_z= 4.2 | l || w || l
00:33:43  msgs=16 hype=0.75 (12) rate_z= 1.8 hype_z= 2.9 | w || w || w
00:33:34  msgs=21 hype=0.57 (12) rate_z= 4.4 hype_z= 2.2 | w || w || w
00:33:50  msgs=21 hype=0.57 (12) rate_z= 2.9 hype_z= 1.6 | :face_with_tears_of_joy: x7 || w || w
```

`chat_zscores` finds the same burst from the rate series (`rate_z` 6.1 at 00:33:35) and a much stronger one in the quiet stream (`hype_z` 66–80), which is the intended behaviour: a burst is compared with its own preceding minutes, not with the whole stream.

## What is NOT done / NOT VERIFIED

- **NOT RUN** — no end-to-end wiring into `vod.find_candidates` or `selection.rank_moments`; that is the lead's job, as the brief says.
- **NOT VERIFIED** — `fetch_twitch_chat` against a real Twitch VOD. `TwitchDownloaderCLI` is not installed on this machine, so the live path was exercised only with a fake CLI in tests. The parser was tested against a TwitchDownloaderCLI-shaped fixture, not a real downloaded file.
- **NOT VERIFIED** — chat replay availability for gaming VODs in general. 28 recent long gaming/reaction VODs probed had no `live_chat` subtitle; the three streams above are the ones that did. Chat replay is clearly not universal, which is why both fetch functions return `None` instead of raising.
- **NOT DONE** — no chat signal in the trainer or in `selection_params.json`; the lead owns that integration.

### Installing TwitchDownloaderCLI (optional, not bundled)

1. Download the release archive for your platform from <https://github.com/lay295/TwitchDownloader/releases> (MIT licence).
2. Extract it and put `TwitchDownloaderCLI` (Windows: `TwitchDownloaderCLI.exe`) on `PATH`.
3. `fetch_twitch_chat` then runs `TwitchDownloaderCLI chatdownload --id <vod-id> -o <file> --collision Overwrite`. Without the binary it returns `None`; it never falls back to an undocumented API.

## Evidence index

| Path | Contents |
|---|---|
| `evidence_A/tests_chat.txt` | 41 passed in 1.23 s |
| `evidence_A/tests_live_regression.txt` | 20 passed, 4 DB-gated skips in 12.78 s |
| `evidence_A/chat_proof.txt` | three-stream real run, full top-5 output |
| `evidence_A/chat_proof.json` | same data as JSON |
| `series_A/0001-feat-live-ingest-and-score-chat-replays-for-long-str.patch` | Job A commit |
