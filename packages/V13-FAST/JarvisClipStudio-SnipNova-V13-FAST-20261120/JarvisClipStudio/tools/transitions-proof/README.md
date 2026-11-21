# Transition proof reel

Evidence for the 58 xfade names in `backend/src/media/edl_render.py`'s
`XFADE_TRANSITIONS` (the catalogue `TRANSITION_MAP` exposes to profiles).
This tool does not change product code.

For every name it renders a two-shot join at 1080x1920: a blue "A" card
and an orange "B" card, each 1.2 s, joined by a 0.5 s `xfade` (output
1.9 s). The cards carry the transition name and a white box at opposite
corners, so direction and motion are visible.

Then it:

- ffprobes each output (geometry, duration);
- extracts the frame at the transition midpoint (frame 28 at 30 fps);
- computes a 32x32 grey midpoint signature and a three-frame
  (25/50/75%) 32x32 signature;
- flags variants whose signatures are near-identical (mean absolute
  grey difference below the threshold);
- writes one contact sheet per transition family;
- writes `table.md` (name, rendered OK, duration, distinct-from) and
  `signatures.json` (signatures, thresholds, failures, groups).

## Run

```powershell
cd D:\SnipNova-Dev\opencode
.\backend\.venv\Scripts\python.exe tools\transitions-proof\make_reel.py
```

Options:

- `--out DIR` — output directory (default `tools/transitions-proof/out`,
  git-ignored).
- `--only fade,wipeleft` — render a subset.
- `--threshold 1.0` — midpoint near-identical threshold (mean absolute
  difference, 0..255). The three-frame threshold is fixed at 1.0.

Exit code is 1 if any transition fails to render or validate.

## Reading the result

`Distinct-from` lists other variants that look the same at the
midpoint. `table.md` also lists the three-frame near-identical groups;
those are the variants that cannot be told apart even across the whole
transition. A low count is evidence, not a judgement: a transition can
render correctly and still be visually redundant with another.

Requires `ffmpeg`/`ffprobe` on PATH and a bold font (Windows Arial Bold
or a common DejaVu/Liberation path; without one, drawtext falls back to
fontconfig).
