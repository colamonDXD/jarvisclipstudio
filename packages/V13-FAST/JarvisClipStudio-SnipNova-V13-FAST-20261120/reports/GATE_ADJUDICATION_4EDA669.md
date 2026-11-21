# Gate adjudication — next-milestone @ 4eda669 (2026-11-10)

Main run (background, overlapped with the 21:24 mainline run and the Wave-4
scoping lane's Demucs CPU test):
- Backend: 21 failed, 3,559 passed, 2 skipped, 5 errors (18:09)
- Frontend: 827 passed / 0 failed / 14 skipped
- tsc: 11 errors, baseline-identical

All 26 backend non-passes were in exactly two files:
- test_media_replication.py — 21 FAILED, every one raising
  OSError("Insufficient disk space for durable media replication") from the
  production disk-space guard at src/media_replication.py:90. The sandbox
  /tmp is a 512 MB tmpfs; the long suite's render fixtures filled it, the
  guard (correctly) refused to stage, and every staging test failed. The
  file was green at the 8eeeb46 gate (3,468/0) and the 0bb58f8 gate
  (3,574/0) on code untouched since.
- test_edl_av_sync.py — 5 ERRORS, ffmpeg killed by SIGKILL with the log's
  own diagnosis "the machine ran out of memory" (same artefact class as the
  1291851 gate's 5 errors under overlapped runs).

Clean serial re-run of both files on an empty /tmp (adjudication-rerun2.log):
**37 passed / 0 failed in 260s.**

VERDICT: gate GREEN. Effective backend total 3,585 passed / 0 failed /
2 skipped (3,559 + the 26 re-proven). No product code changed. Lesson for
future sandbox gates: point TMPDIR at a workspace dir (large disk) for
long suites so the 512 MB tmpfs cannot fail the replication guard.
