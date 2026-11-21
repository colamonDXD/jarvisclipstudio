# Split-Border Trim — closing the Bert sliver residual

Lane: branch `feature-split-border-trim`, worktree `~/workspace/jcs-border`,
off next-milestone @ `77dbbde`. **NOT merged — main merges.**
Commits: `0c9383a` (detector), `cf8287f` (stamp + apply), `bd8b29f` (tests),
`e2af8af` (CHANGELOG).

Residual being closed: the caption/title face-guard lane
(CAPTION_FACE_OVERLAP_REPORT.md) proved the split-tile "slivers" in
Luffy's Bert clip were NOT a composition bug — tiles compose exactly
1080×960 — but the **source's own border furniture**: the YMH podcast
feeds carry pure-white frame lines (measured on his frame f7 at output
x≈9–16 and x≈1073–1079), and because split tile windows are ~2.8× face
width, a window can outgrow the camera feed and capture the frame line
inside the tile, where it renders as a bright sliver at the tile edge.

## Step 0 — geometry facts (where a trim can enter)

- `build_reframe_plan` (`backend/src/media/reframe_plan.py`) decides
  `split` when two significant faces cannot fit one crop and the shot
  lasts ≥ `SPLIT_MIN_SECONDS` (2.0 s). Tiles come from
  `_split_region(median_box, W, H)`: `tile_w = min(W, max(160,
  face_size·2.8))`, `tile_h = min(H, max(160, face_size·2.4))`, centred
  (half-even) on the face. Tiles are **static source rects**
  `{tile_x, tile_y, tile_w, tile_h}`; `shot["split"] = {"left": …,
  "right": …}` and `shot["face_src"]` holds the per-subject union
  envelopes positionally ([0] = left tile's subject, [1] = right's).
- `_resolve_segment` copies tiles into
  `segment["reframe"]["split_layers"]` (deduped by tile tuple) and
  transforms `face_src` into output-space `face_boxes` via
  `_tile_output_box`.
- The renderer (`edl_render.py`) crops `tile_w:tile_h:tile_x:tile_y`,
  scales each tile to exactly 1080×960 and vstacks — composition is
  exact, so **insetting the tile rect is the only change needed**; no
  renderer change.
- Entry point chosen: `_attach_reframe_plan` already holds the source
  path; `_entry_source_pieces` maps compacted shot spans back to
  source time ranges. Detection runs once per split shot at plan time,
  the trim is stamped on the shot and the resolved layer, and
  `_resolve_segment` insets tiles **before** layer keying and before
  the face-box transform — so the caption guard's boxes stay
  consistent with the rendered (inset) tiles. This mirrors the
  face_boxes stamp pattern exactly.
- **Single-speaker crops scoped OUT, on evidence:** single windows are
  swept x-expression crops (not static rects — a static per-shot trim
  does not compose with a moving window), and no furniture was
  observed in single-speaker renders (Bert f2 is a full-frame close-up
  feed; slivers measured only in split tiles). The detector itself is
  window-generic (`detect_window_trim(frames, face_box)`) so a future
  lane can reuse it. Fit/letterbox mode is deliberate full-frame —
  out of scope. The Plain planner produces no splits.

## Detector design (`backend/src/media/border_trim.py`)

Per tile window, frames are grabbed with ffmpeg at 0.25 / 0.5 / 0.75
of each of the shot's source pieces (crop + rawvideo rgb24, the
broll_vision pattern; failures are skipped, never raised).

A column (row) is **line-like** in a frame when ≥ 90% of its pixels
sit within ±14 of the column median **and** that median is extreme —
all channels ≥ 235 (white furniture) or ≤ 20 (black letterbox-style).
A column is line-like **for the shot** when it is line-like in
≥ 75% of sampled frames (persistence kills one-frame flashes and
passing bright objects).

Trim per edge = everything from the edge through the first
persistent line band's far side, plus ≤ 3 px of further *uniform*
(any-colour) columns — the anti-aliased transition — because the
furniture is by construction everything outside the line. Guards:

- **Cap:** a band whose far edge exceeds 6% of the window's
  width/height yields NO trim for that edge (wide bands are left
  whole rather than guessed at); the trim itself is also clamped
  to the cap.
- **Face-stop:** the shot's already-computed face envelope (in
  window coordinates) bounds the trim: if the anti-alias extension
  would cross the face's first pixel, the trim clamps to that pixel;
  if the face overlaps the line band itself, the trim is dropped to
  0 — a face is never trimmed into or across.
- Minimum band 3 px. Degenerate results (inset tile < 96 px on a
  side) are refused whole at apply time.

Gating: `BORDER_TRIM_ENABLED`, **default ON** — this is a
deterministic geometry correction, not a model feature; `0 / false /
off / no` disables it and restores today's geometry exactly. Any
failure anywhere in stamping leaves the plan untouched (per-shot and
outer try/except, warnings logged). Audit: the applied trims are
stamped as `border_trim` on the QC shot entry and on each resolved
split layer (applied sides only), plus an info log line per stamped
shot — the vision-veto stamp style.

## Bert pixel calibration (design input, measured pre-lane)

On `bert_before_f7.png` (output coords): top tile — left white bar
x≈9–16 (column std 0, mean 255) preceded by ~9 px of darker border
art; right white bar flush at x≈1073–1079; transition columns std
~7–12 (anti-aliased edge, inside the ±14 tolerance); bottom tile
edges all std ≥ 17 (curtain content — correctly NOT furniture: a
smooth dark gradient is uniform-ish but its median is not extreme,
so the detector leaves it alone; pinned by a unit test).

## Measured proof

New suites: `test_border_trim.py` (16), `test_reframe_border_trim.py`
(4), `test_border_trim_render.py` (2) — **22/22 passed**. Neighbours:
117 reframe/caption/face-guard/editor tests — all green (82 clean
dots before a sandbox SIGTERM cut the first combined run; the tail
four files re-ran 44/44; every file covered green).

**Synthetic furniture source → plan stamps (exact):**

| tile | stamp | tile before | tile after |
|---|---|---|---|
| left | `{left: 52, top: 43, right: 0, bottom: 0}` | (0, 170, 920, 788) | (52, 213, 868, 744) |
| right | `{right: 20, left/top/bottom: 0}` | (940, 170, 920, 788) | (940, 170, 900, 788) |

(52 = the vertical line's far side; 43 = the horizontal line's far
side 40 + 3 transition px; 20 = the right line's far side measured
from the window's right edge — all as designed.)

**Render-level (real `render_edl_clip_ffmpeg`, frame t = 2.0):**
near-white column counts (≥ 85% of the tile's rows near-white)
inside the outer 90 output columns per tile edge —

| zone | before | after |
|---|---|---|
| top tile, left | **14** | **0** |
| top tile, right | 0 | 0 |
| bottom tile, left | 0 | 0 |
| bottom tile, right | **11** | **0** |

Faces stay fully in frame in both variants: each stamped face box
holds its white centre marker (≥ 1,500 px), and in the after render
the marker centroid sits within 40 px of the box centre — the face
boxes moved WITH the inset tiles, nothing clipped or shifted off.

**Japan real source (no furniture):** the enabled plan is exactly
the disabled plan (resolved segment dicts and the QC section equal,
no `border_trim` stamp anywhere), and a frame rendered from each
inside the split interval is equivalent (mean abs diff ≤ 1.0; the
assert allows only encoder noise, the plans being identical means
the renders take the identical code path).

**Furniture-free synthetic source:** enabled attach resolves
byte-identically to disabled attach (pinned by test) — the
no-furniture → plans-unchanged invariant holds at plan level, not
just on Japan.

**Detector unit pins:** inset band behind an art strip trims through
the band (32); flush white/black bands trim exactly; black letterbox
rows trim top/bottom; textured noise edges and a smooth curtain
gradient → 0; band starting beyond the cap → 0; band ending beyond
the cap → 0; line in 1/4 frames → 0, in 3/4 → trimmed; face-stop:
extension clamps at the face's first pixel (trim == face x),
face-overlapping-band → 0, face-before-band → 0; env gate semantics;
ffmpeg grab shapes.

## State

CROSS-FEATURE VERIFIED in the sandbox (detector + wiring + render +
neighbours + changelog.test.ts 4/4). **USER-MACHINE VALIDATION
PENDING:** re-run the same Bert video ("Bert Has Sexy Lady Legs |
2 Bears, 1 Cave") on the next package and confirm on extracted frames
that the split tiles no longer carry the white frame slivers at
their edges — the same frames that showed them in
caption-face-frames/ (f7 especially). Hard limits honoured: reframe
geometry + detector + tests only; no caption/title, B-roll, or
selection changes; ₹0. The detector was NOT forced off: on every
realistic textured edge tested (noise, curtain gradient, Japan
footage) it returns 0, so it ships enabled with the env kill-switch
documented in the CHANGELOG.
