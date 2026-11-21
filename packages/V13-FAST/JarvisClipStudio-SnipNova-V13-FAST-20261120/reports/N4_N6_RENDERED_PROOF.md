# N4 / N6 RENDERED PROOF — words-per-line control + Montserrat fonts

**Scope:** the LOW editor fixes merged @ `a9c6bc0` (in tip `5ea71bf`) — N4 (per-clip
`words_per_line` settable in the quick editor + AI-edit vocabulary) and N6
(Montserrat-Black / Montserrat-ExtraBold added to the editor font select) — were
unit-proven only. This pack proves both through **actual rendered video**.
**Code under test:** next-milestone tip `5ea71bf`, in a dedicated snapshot worktree
(`~/workspace/jcs-n4n6-proof`, detached at `5ea71bf`). **No production code was
changed, nothing committed** — the snapshot's only addition is the untracked
`.proof/` artefact dir; `~/workspace/jcs-next` and the tag `v6-candidate-90f1cb4`
(→ `90f1cb41b083`) verified untouched after the run.
**Fixture:** Japan source `~/workspace/qm-source/source.mp4`, window **r3 =
1324.0–1338.0 s** (52 real transcript words from the source's transcript cache:
*"I'm talking like $400 per sandwich…"*), niche `travel_vlog` — the same fixture
and window as the caption-position evidence pack.

## Method

The production re-render path, exactly as the caption-position pack drove it:

```
normalize_caption_overrides          (the production entry validation)
→ ClipEditingMixin._build_rerender_edl   (the re-render seam)
→ attach_reframe_plan
→ create_optimized_clip              (the render create_single_clip calls)
```

- **N4 pair:** seam overrides `{"words_per_line": 2}` vs `{"words_per_line": 6}`;
  everything else identical (`font_family=None`, template `default`). The two
  frozen EDLs were deep-compared programmatically: **they differ only in
  `captions.words_per_line`** (both blocks otherwise
  `{preset_id: classic_box, position: bottom}`).
- **N6 pair:** one seam EDL shape (no overrides; profile default
  `words_per_line` 4), rendered twice with the explicit builder font argument —
  `font_family="Montserrat-Black"` vs `font_family="Montserrat-ExtraBold"` — the
  argument `create_single_clip` passes from the task/override font, after the
  same `find_font_path` registry validation `rerender_clip` performs (reproduced
  in the driver, results below).
- **ASS capture:** the render writes `captions.ass` into a `TemporaryDirectory`
  that is deleted on exit. The driver wraps `src.video_utils`'
  `build_assemblyai_ass_subtitles` with a tee that copies the built ASS out
  before deletion. Driver-side instrumentation only — the production call, its
  arguments and its result are unchanged; the render itself is the stock path.
- All renders serial, sanctioned venv, `OMP_NUM_THREADS=2`. Render times
  43–45 s each. NVENC probing fails in the sandbox (no GPU) and the encoder
  falls back to CPU — the same path every sandbox lane uses. The EDLs expect a
  `calm` music bed but the sandbox worktree has no music library files, so the
  renders carry no bed (same note as the caption-position pack; caption
  grouping and fonts are independent of the bed).

## N4 — `words_per_line` reaches the rendered captions — **PASS** (RENDERED-VIDEO)

Entry validation: `normalize_caption_overrides({"words_per_line": 2})` →
`{'words_per_line': 2}`; `…, 6}` → `{'words_per_line': 6}` — the N4 field is
accepted by the production normalizer (pre-fix it raised
`ValueError: unknown caption fields: words_per_line`).

ASS measurement (the app's karaoke ASS emits one step-event per word — 52
events in both renders — where each event's text is the current caption chunk;
grouping is therefore read directly off the per-event word counts):

| Render | Events | Words/event distribution | Max words/event | Max words/line |
|---|---|---|---|---|
| `n4_wpl2.mp4` | 52 | {1: 4, 2: 48} | **2** | 2 |
| `n4_wpl6.mp4` | 52 | {2: 2, 3: 6, 4: 4, 5: 10, 6: 30} | **6** | 6 |

Chunk texts confirm it: wpl=2 events read `"I'm talking"`, `"like $400"`,
`"per sandwich,"`; wpl=6 events read `"I'm talking like $400 per sandwich,"`.
No wpl=2 event exceeds 2 words; wpl=6 uses the full range up to 6. The grouping
the user sets is the grouping the renderer executes. Both exports are valid
1080×1920, h264 + aac (ffprobe), `layout_report.caption_y` 0.7557 in both
(position untouched, as expected).

| File | Size | SHA-256 |
|---|---|---|
| `renders/n4_wpl2.mp4` | 11,010,697 B | `29dc1cf639381b183635f3b7bae06e2e4ffc596c01755a0f29b63c963e04f0d9` |
| `renders/n4_wpl6.mp4` | 10,998,563 B | `df4105cda29f1f66e6a9f60f417851aeca99c44c70c70abd876569ff794d70cf` |

## N6 — Montserrat fonts reach the rendered captions — **PASS** (RENDERED-VIDEO)

Registry resolution (the `rerender_clip` validation, reproduced):

| Requested font | `find_font_path` | `ass_font_name` (production resolver) |
|---|---|---|
| `Montserrat-Black` | `backend/fonts/Montserrat-Black.ttf` | `Montserrat Black` |
| `Montserrat-ExtraBold` | `backend/fonts/Montserrat-ExtraBold.ttf` | `Montserrat ExtraBold` |

Each request resolves to **its own distinct TTF file**. The ASS the render
actually burned carries that font's name in the Default style (verbatim):

- Black render: `Style: Default,Montserrat Black,62,&H00FFFFFF&,…`
- ExtraBold render: `Style: Default,Montserrat ExtraBold,62,&H00FFFFFF&,…`

(Per-word `\fnMontserrat Black` / `\fnMontserrat ExtraBold` tags appear in the
Dialogue events as well.) The two renders' event structure is identical (52
events, same texts, same distribution {1: 2, 2: 4, 3: 6, 4: 40}) — the font is
the only difference.

**Naming note, stated precisely:** the ASS stamps the font's *embedded family
name* (`Montserrat Black`, with a space) rather than the model/select string
(`Montserrat-Black`). That is the production `ass_font_name()` resolver's
designed behaviour for every font in this renderer — libass matches fonts by
family name, and the stock `default` template itself requests `Montserrat-Black`
and stamps `Montserrat Black`. It is **not** a silent fallback: the requested
font's own file is resolved and loaded, the stamped name is that font's own
family name, and the two fonts stamp *different* names. No substitution with a
different font occurs.

**Rendered-glyph corroboration:** the two exports are not byte-identical
(different SHA-256, different sizes), and a same-timestamp frame diff
(t = 1.6 s) concentrates in the caption band — mean |Δ| per pixel **7.551** in
caption rows 1350–1700 vs ≈**1.238** in the rest of the frame (the residual
whole-frame difference is CRF rate-control ripple from the changed glyphs, the
expected behaviour when any burned-in pixel changes). If both requests had
silently fallen back to one font, the files would be byte-identical, as
identical-EDL renders in this pipeline are (see the consistency note below).
Both exports are valid 1080×1920, h264 + aac.

| File | Size | SHA-256 |
|---|---|---|
| `renders/n6_montserrat_black.mp4` | 10,913,782 B | `04d9354c75d2f21f117d01a5aa34abae9fba510aca566bf0ed43348183922ee4` |
| `renders/n6_montserrat_extrabold.mp4` | 10,919,152 B | `574abcb55829fe7db6b5edb79cc7bfa19051ac6beffdb35bdc9cf62ccdce39ec` |

**Consistency note:** the Black render's SHA-256 (`04d9354c…`) is identical to
the caption-position pack's Studio-seam Bottom render of the same window — the
deterministic render path reproduces that earlier pack's file byte-for-byte
(same EDL shape; the default template's font is Montserrat-Black), which
cross-validates that this lane drove the same production path.

## Verdicts

| # | Item | Verdict | Proof level |
|---|---|---|---|
| N4 | User-set `words_per_line` (2 vs 6) groups the rendered captions as set | **PASS** — ASS per-event word counts follow the setting exactly (max 2 vs max 6, full distributions above) | RENDERED-VIDEO |
| N6 | Montserrat-Black / Montserrat-ExtraBold render in their own fonts | **PASS** — distinct TTFs resolved, distinct family names stamped in the burned ASS, glyph-level frame difference concentrated in the caption band, exports not byte-identical | RENDERED-VIDEO |

No defect found in either proof. Nothing was fixed here (evidence-only lane);
no production file was modified and no test was touched.

## Artefact index (all under `~/workspace/jcs-n4n6-proof/.proof/`)

- Driver: `n4n6_proof.py`; run log: `logs/driver.log`; raw measurements:
  `results.json`.
- Renders: `renders/n4_wpl2.mp4`, `renders/n4_wpl6.mp4`,
  `renders/n6_montserrat_black.mp4`, `renders/n6_montserrat_extrabold.mp4`.
- Captured ASS files: `ass/n4_wpl2.ass`, `ass/n4_wpl6.ass`,
  `ass/n6_montserrat_black.ass`, `ass/n6_montserrat_extrabold.ass`.
- Frozen EDLs (pre-/post-attach): `edls/*.pre.json`, `edls/*.attached.json`.
- Frames @ t=1.6 s: `frames/{n4_wpl2,n4_wpl6,n6_montserrat_black,n6_montserrat_extrabold}_t1.6.png`.
