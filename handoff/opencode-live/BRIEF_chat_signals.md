# OpenCode brief: live chat signals library (START ONLY AFTER LUFFY APPROVES THE LIVE ARCHITECTURE)

Lead: Claude Code. You own ONLY the files listed here. No other agent edits them.

## Where
- Worktree: `D:\SnipNova-Dev\opencode`. Create branch `lane-b/live-chat` from `lane-b/round3`.
- Report folder: `D:\SnipNova-Dev\handoff\opencode-live\`. Put `REPORT.md` there, plus `series\` (`git format-patch b0965c8..HEAD`, your new commits only, listed in REPORT) and `evidence\`.

## You own (create)
- `backend/src/live/__init__.py`
- `backend/src/live/chat_signals.py`: pure Python, no I/O.
- `backend/src/live/twitch_chat.py`: read-only Twitch IRC recorder.
- `backend/tests/unit/test_live_chat_signals.py`
- `backend/tests/unit/test_live_twitch_chat.py`
- `tools/live/record_twitch_chat.py`: CLI wrapper for the recorder.

## You must NOT touch
- Anything else under `backend/src/` (no services, media, auto_director, api, workers, config, migrations).
- `frontend/`, `docker-compose*.yml`, `init.sql`, `.env*`, `D:\EditedTool\JarvisClipStudio`, `CHANGELOG.md`.

## Hard rules
- ₹0. No API keys, no sign-ups, no OAuth.
- Twitch chat is read **anonymously** over IRC: `irc.chat.twitch.tv:6697` (TLS), `NICK justinfan<random digits>`, `CAP REQ :twitch.tv/tags twitch.tv/commands`. Read only: never send PRIVMSG.
- No YouTube scraping libraries (pytchat etc.). YouTube chat is out of scope here.
- Never delete, skip, xfail or weaken existing tests. No merge, rebase or push.

## Contract

### Message (input), one JSON object per line (`chat.jsonl`)
`{"ts": <float epoch seconds from tmi-sent-ts/1000>, "user": "<login>", "text": "<message>", "emotes": ["<emote name>", ...], "kind": "msg"|"raid"|"sub"|"notice"}`

### `twitch_chat.py`
- `parse_irc_line(line) -> Optional[dict]` maps a raw IRC line to the message above. `PRIVMSG` → `msg`; `USERNOTICE` with `msg-id=raid` → `raid`; sub/resub → `sub`; else None.
- `record(channel, out_path, stop_after=None)`: connect, `JOIN #channel`, answer `PING`, append JSONL, reconnect with backoff on drop.

### `chat_signals.py`
- `rate_series(messages, bin_sec=1.0) -> list[(t, count, unique_users)]`
- `detect_spikes(messages, *, baseline_sec=120, bin_sec=2.0, z=3.0, min_unique_ratio=0.35, raid_mute_sec=60, lexicon=DEFAULT_HYPE) -> list[Spike]`, where
  `Spike = {start, peak, end, rate_z, unique_ratio, emote_burst: {emote: count}, hype_terms: {term: count}, score 0-1, reasons: [..], rejected: Optional[str]}`
- **Filters** (keep rejected spikes in the output, with `rejected` set, so they're auditable):
  - low unique-chatter ratio → `"spam_or_bots"`;
  - within `raid_mute_sec` after a raid → `"raid"`;
  - a single repeated message from few users → `"copypasta"`.
- `DEFAULT_HYPE`: the hype lexicon (LMAO*, LOL+, WHAT+, NO WAY, OMEGALUL, KEKW, LUL, PogChamp, Pog, W, L, ???, !!!); case-insensitive; elongations normalised ("LMAOOOO" → "lmao").
- `chat_window_for(spike, preroll=(15, 45), postroll=(10, 30)) -> (start, end)`: a suggested video window (the fuser refines it later). Configurable, not fixed at 30 s.

## Tests (required)
- Synthetic chat with a calm baseline, a real spike (many unique users, hype terms), a bot burst (2 users × 200 messages), a raid burst, and a copypasta. Exactly one accepted spike; the others rejected with the right reason.
- An elongation/emote parsing table. IRC parsing of real-format lines (tags, emotes, raid USERNOTICE). Window bounds.
- Recorder: a fake socket server test (localhost) for connect/JOIN/PING/reconnect. **No real network in unit tests.**

## Evidence (required)
- Record **10 minutes** of real public chat from a busy live Twitch channel with `tools/live/record_twitch_chat.py` (read-only, anonymous). Then run `detect_spikes` on it and save:
  - `evidence/chat_sample.jsonl` (first 200 lines only);
  - `evidence/spikes.json`;
  - `evidence/rate_plot.png` (messages/sec with spikes marked).
- Report the channel name, time and counts. Do not save more chat than needed.

## REPORT.md
- A status table: literal PASS / FAIL / NOT RUN / NOT VERIFIED.
- Exact test counts and timings, the commits, `git diff --stat`.
- What the real-chat run found: spikes accepted and rejected, with reasons.
- Known limits.

## Integration (lead's job, not yours)
Claude Code wires `chat_signals` into the live fuser and confirms every spike with audio/visual evidence. You do not touch the pipeline.
