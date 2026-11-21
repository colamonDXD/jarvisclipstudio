# Wave 4 — YouTube Channel Monitoring, Stage 1: §9 Report

**State: BUILT, TESTED, LIVE-FEED-VERIFIED. Not merged, not packaged, not uploaded.**

Branch `feature-channel-monitoring` off next-milestone @ `4327791` (HEAD verified
before branching), worktree `~/workspace/jcs-chanmon`, tip `e1db037`, tree clean.
Built exactly to the lane brief in `SCOPING.md` §5; deviations are named at the end.

## Commits (8)

| Commit | Change |
|---|---|
| `efbd991` | Migration `20261111_0001_channel_monitoring.sql` — `channel_monitor_settings`, `channel_monitors`, `channel_monitor_videos` (semicolon-free comments, `IF NOT EXISTS`, reversible note) |
| `6ce8b9c` | `backend/src/youtube_feed.py` — RSS fetch + stdlib Atom parse + handle/URL/ID resolution with validation fetch; feed tests |
| `d4106b9` | `channel_monitor_repository.py` — settings/monitors CRUD + claim-first video dedupe mirroring the watch-folder idioms |
| `83d5860` | `channel_monitor_service.py` — watcher service + loop, baseline seeding, production task creator; service/route/creator tests |
| `1f03918` | Routes (`/channel-monitors` CRUD + settings), `CHANNEL_MONITOR_POLL_SECONDS` config (default 900, min 60), lifespan start/stop beside the watch-folder watcher, `.env.example` |
| `9aef011` | Frontend BFF proxies under `src/app/api/channel-monitors/` + `channelMonitors` i18n keys in all 6 locales (en real English, others interim English) |
| `1bd27f3` | "YouTube channels" section on the Autopilot settings page (master toggle, add form with per-channel defaults, channel list with health, activity list) + component tests + CHANGELOG entry |
| `e1db037` | Fix found by the live check: resolver yt-dlp options mirror the metadata path's proxy-tolerant options (`nocheckcertificate`) |

## What Stage 1 does

- **Detection:** RSS uploads feed (`feeds/videos.xml?channel_id=UC…`), stdlib
  ElementTree parse, no new dependency; poll cadence 900 s = YouTube's own feed
  cache window. Resolution at add time only: bare `UC…` ID and `/channel/UC…`
  URLs directly, `@handle` / `/c/` / `/user/` via one flat yt-dlp extraction,
  always validated by a real feed fetch before any row is written.
- **Dedupe/discipline:** sibling of `watch_folder_service.py` — claim-first
  `INSERT … ON CONFLICT DO NOTHING` on `(user_id, video_id)`, bounded retry
  (3 attempts, 300 s backoff) → parked, F-D1-style stale-claim sweep
  (`CHANNEL_MONITOR_STALE_CLAIM_SECONDS`, default 1800), claims of a deleted
  monitor parked by a per-cycle sweep with `monitor_removed` on the row.
- **Task creation:** mirrors the manual YouTube flow —
  `BillingService.assert_can_create_task` → yt-dlp duration preflight →
  `TaskService.create_task_with_source` → `TaskRepository.save_source_metadata`
  (with `"channel_monitor_video": <video id>`) → `JobQueue.enqueue_processing_job`.
- **Defaults per channel** (all validated with the create route's own
  validators): `template_mode` stored explicitly, **plain** by default;
  `creation_mode` **full_auto** by default, **manual_cutter refused (422)**;
  niche / music mode / music track / clip count / processing mode carried.
- **Baseline-on-add:** every current feed entry is written as a `baseline` row
  — seen, never tasked. The only exception is the explicit, default-off
  "Also clip the newest video now" option, which runs exactly the newest
  eligible entry through the normal claim path at add time.
- **Shorts:** excluded by default — feed link-form filter plus a ≤61 s
  duration backstop at preflight. **Daily cap:** default 3 per channel
  (1–20 or NULL = off); cap hits are terminal `skipped/daily_cap` rows.
  **Over-duration:** terminal `skipped/over_duration_cap`, no task, no minutes
  burned. **Feed outage:** monitor health fields only (`last_error`,
  `consecutive_failures`), zero video rows, channel never auto-disabled.
- **Settings UI:** new section on the existing Autopilot page — master toggle,
  add form (with the baseline rule spelled out before he commits), channel
  list (enabled toggle, last-polled, failure state, remove), and an activity
  list with plain-language state/reason text and task links.

## Proof

- **Backend new tests: 32 passed / 0 failed** — `test_youtube_feed.py` (11:
  parse order/fields, Shorts-link detection, malformed feed, resolution
  inputs incl. junk refusal and unfetchable-feed refusal) and
  `test_channel_monitor.py` (21: baseline add creates **zero** tasks; new
  video → **exactly one** task carrying the channel's defaults; second poll →
  no duplicate; oldest-first queueing; cap enforced + NULL = off;
  over-duration → skipped, no task; Shorts filtered unless included;
  failure → backoff retry → parked after 3 attempts; stale claim recovered on
  the next sweep; feed outage → health set, zero rows, recovery resets;
  removed-monitor claims parked; clip-newest seeding leaves exactly one
  eligible entry un-baselined; production-creator decisions pinned at module
  seams — defaults stamped on `create_task_with_source`, over-duration and
  Short backstop raise the right `SkipVideo` with **no task created**; routes:
  baseline on add, clip-newest claim path, manual-cutter 422, bad input 422
  with no row, duplicate 422, settings toggle + update + delete round-trip).
- **Frontend new tests: 8 passed / 0 failed** — BFF proxy tests (401 without
  session; GET/POST/PUT/DELETE proxied with the right paths and bodies) and
  section component tests (baseline copy rendered; add posts
  `clip_newest_now: false`, `template_mode: plain`, `creation_mode: full_auto`
  by default; remove issues the BFF DELETE and the list empties).
- **Neighbour suites, unmodified, green:** backend batch 82 passed
  (migration-safety — the new migration replays cleanly under the runner's
  semicolon split — watch-folder ×2, tasks helpers, task-source settings, plus
  the two new files); frontend settings-area batch 17 passed (settings page,
  api-keys page, watch-folder BFF, messages parity, changelog).
- **CHANGELOG:** entry added in the same change; `changelog.test.ts` run
  immediately after — **4/4 passed**.
- **tsc:** 11 errors, set byte-identical to the recorded baseline (verified by
  diff against a baseline run; none in new files).
- **App smoke:** `create_app()` builds; 132 distinct paths including
  `/channel-monitors`, `/channel-monitors/settings`,
  `/channel-monitors/{monitor_id}`.
- **LIVE leg (labelled honestly):** from this sandbox,
  `resolve_channel("@MrBeast")` resolved to `UCX6OQ3DkcsbYNE6H8uQQuVA` and a
  live RSS fetch returned exactly 15 entries, with the Shorts link-form flag
  correct on real data (a `/shorts/` upload flagged, long-form uploads not).
  The first live attempt failed only at yt-dlp certificate verification —
  the sandbox's TLS-intercepting egress proxy; the resolver now mirrors the
  app metadata path's own proxy-tolerant options (commit `e1db037`). Tests
  themselves stub all HTTP, per the brief.

## Deviations from the brief (named, with reasons)

1. **`fetch_channel_feed_with_title` helper added** — the §2.4 validation gate
   needs the channel title from the same fetch; `resolve_channel` keeps the
   brief's exact `(source_input) -> (channel_id, channel_title)` signature.
2. **Two feed fetches at add time** (resolution's validation fetch, then the
   baseline fetch) — kept the brief's interfaces separate; add-time only,
   cost is one extra small GET per channel ever added.
3. **`include_shorts_feed` parameter is a documented no-op in Stage 1** —
   the signature is the brief's; the UULF/UUSH playlist feeds are a Stage-2
   knob per the scoping, and Shorts filtering is by link form as specified.
4. **Resolver yt-dlp options gained `nocheckcertificate: True`** — found by
   the live sandbox check; it mirrors `youtube_utils._build_info_options`,
   the app's existing trusted-metadata choice for proxied deployments.

## Five states

- **Implemented:** YES — full end-to-end slice (migration → feed → repository
  → watcher service/loop → routes → BFF → Settings UI → i18n → CHANGELOG).
- **Individually tested:** YES — 32 backend + 8 frontend new tests, all green.
- **Integrated:** YES, on the branch — app builds with the router registered
  and the loop lifespan-wired beside the watch-folder watcher; neighbour
  suites green unmodified; tsc at baseline.
- **Cross-feature verified:** PARTIAL — service↔routes share one repository
  contract (fake-repo tests on both sides), the production creator's
  decisions are pinned at its collaborator seams, and the live RSS
  resolve→fetch path is proven against a real channel. NOT exercised in this
  sandbox: the migration against real Postgres (no Postgres here — the
  migration-safety suite replays the runner's split and the SQL follows the
  watch-folder idioms exactly), a real end-to-end task through the worker
  (no Docker here), and both watcher loops running concurrently in one live
  process.
- **User-machine validation:** PENDING — on his install: add a real channel,
  confirm the baseline is marked seen, and watch the next real upload become
  a task with the channel's defaults.
