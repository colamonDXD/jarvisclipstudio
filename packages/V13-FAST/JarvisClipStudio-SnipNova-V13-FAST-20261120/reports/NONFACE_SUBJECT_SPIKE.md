# NON-FACE SUBJECT PRESENCE — research spike (2026-11-16)

**Question:** what is the cheapest reliable way to give the reframe planner subject boxes (or a
subject-presence + extent signal) for subjects that are NOT faces — within ₹0, licence-clean
models only, CPU-viable on Luffy's RTX 4050 laptop (renders are CPU-bound), no new paid/API
services?

**Why:** the framing lane protects detected *faces* from crop amputation. Non-face subjects
have no boxes at all. Proven on Luffy's MrBeast exports: the drone light-portraits clip (m4)
had its subject sliced at the frame edges and one nearly-empty frame kept, because a face made
of lights is invisible to the YuNet face detector.

**Verdict: PARTIAL — build two cheap pieces, don't build the rest.** Detail in §6.

---

## 1. Method and evidence base

- Test frames extracted from Luffy's actual exports (`~/workspace/user/media_library/video/`,
  contact sheets in `~/workspace/clip-review2/`): 8 frames from the m4 drone clip
  (`eb/eb8c5819…`), 3 from the m1 duel clip, 2 from the m3 fog clip, 1 from m6, plus 2 frames
  from the 16:9 Japan source (`~/workspace/qm-source/source.mp4`, breakfast scene — not used
  for scoring, no clean subject GT).
- Ground truth = boxes eyeballed by the spike author from the actual frames (normalized
  xyxy), recorded before any detector ran. GT is approximate (±~0.03); IoU differences
  below ~0.05 are not meaningful.
- **Evidence limitation, stated plainly:** the m4/m1/m3/m6 frames are 9:16 *exports* with the
  app's own burned-in title/captions and the source's scoreboard graphics. The planner
  consumes 16:9 *source* frames, which carry none of the app's overlays. Mask measurements
  on exports are therefore a *lower bound* contaminated by overlay text (it visibly captures
  the title block once, §3). Detector (YOLOX/HOG/YuNet) measurements are barely affected —
  overlays are small and none of the scored boxes hinge on them.
- Sandbox CPU: shared/throttled vCPU (1.5 CPU quota, 4 threads visible). All timings are
  sandbox numbers; Luffy's Ryzen 7 7445HS is far stronger per core. Laptop figures are
  estimates and marked UNVERIFIED.
- Working resolution for mask/HOG tests: 480 px wide — the reframe analysis pass's own
  resolution (`reframe_plan.py`: "CPU-only analysis at 480 px wide", samples at 3 fps,
  2 fps for long sources).

## 2. Status quo — what the stack already computes (measured)

`media/scene_context.py::ContextAnalyzer.measure` runs per sample and already computes a
camera-compensated motion mask, Canny detail, and text-line boxes. But the *subject* signals
that reach the planner are:

- `detail_left/right` — a **1-D horizontal** span of Canny detail. Measured on all 10 scored
  frames: **[0.002, 0.998] on every single one** (portraits, people, forest alike). It carries
  effectively no subject-extent information on real content.
- `protect_left/right` — horizontal span of **faces + text lines only**. With no faces
  supplied it is driven by whatever text-like components exist; no non-face, non-text subject
  can ever enter it. There is no vertical extent and no 2-D box anywhere in `SceneEvidence`.
- Motion: the analyzer keeps only aggregates (fraction, 6-column spread, and — on the
  framing branch — a centroid). The framing lane's Step-0 already established the mask's
  distribution is discarded.

**YuNet baseline (shipped model, run at full res):** m4_t0 night-vision face **1 found**;
m4_t18 night-vision face **0**; m1_t6 helmeted guard **0**; m4_t15 drone portrait **0**;
m4_t24 **2**; m4_t36 **2**; m6_t18 **2** (backs of heads — at least partly false positives).
Confirms the gap: helmets, one of two night-vision shots, and the light-portrait are
face-invisible today.

## 3. Mask-derived extent (zero new dependencies — OpenCV only)

Three candidate masks were built from the same ingredients the analyzer already computes.
IoU vs GT (union-of-components boxes unless noted):

| Frame (subject) | Bright mask | Detail mask (2-D) | Motion mask (0.5 s pair) |
|---|---|---|---|
| m4_t21 drone portrait | 0.225 union / **0.755 largest-comp** | 0.212 | 0.249 (cov 0.28) |
| m4_t15 drone portrait | **0.521** union / 0.460 largest-comp | 0.319 | 0.237 (cov 0.28) |
| m4_t12 portrait ~absent | box meaningless (see below) | 0.052 | — |
| m4_t24 people, night | 0.546 | 0.339 | — |
| m4_t36 people, night | 0.543 | 0.406 | — |
| m4_t18 / t0 night-vision | 0.0 / 0.0 | 0.092 / 0.127 | — |
| m1_t6 helmet duel | 0.140 | 0.139 | 0.154 (cov 0.32) |
| m6_t18 wide group | 0.387 | 0.340 | 0.437 (cov 0.07) |
| m3_t12 forest, NO subject | full-frame box (false extent) | full-frame box | — |

Findings:

- **Bright mask, largest component = the one mask that sees the m4 subject.** On m4_t21 the
  largest bright component *is* the portrait (+ its dot-matrix caption): box
  [0.225, 0.054, 0.777, 0.435], **IoU 0.755**. On m4_t15 IoU 0.46–0.52. Nothing else tested
  detects this subject at all.
- **Bright *coverage* is a clean presence signal even when the box is not.** Total bright
  coverage: portrait present 0.20 (t21) / 0.12 (t15) vs portrait ~absent 0.037 (t12);
  largest-component coverage 0.162 / 0.121 vs 0.029. A threshold near 0.05 separates
  "subject present" from "subject gone" on all three m4 frames. On t12 the largest
  component is the app's burned-in title (an export-only artifact); on source frames that
  component doesn't exist.
- **Bright extent must be dark-scene gated.** On the sunlit forest (m3_t12) bright coverage
  is 0.113 with a full-frame box — meaningless. The portrait frames are near-black scenes.
  Gate: only trust bright extent when the frame is dark (low mean luminance). That gate is
  a design requirement derived from these measurements, not yet itself validated.
- **Detail mask as a 2-D extent: dead end.** Boxes ≈ full frame on 9/10 frames (texture is
  everywhere). Same disease as the existing 1-D span. Don't build.
- **Motion mask as an extent: dead end.** The drone portrait *animates* (shimmer/redraw),
  so motion coverage on its shot is 0.28 with a near-full-frame box; the duel is 0.32.
  Motion measures *change*, not *subject*. (The framing lane's motion *centroid* remains
  the right use of this signal; a motion *box* is not.) Don't build.
- Cost at 480 w: bright ≈ 15 ms/sample typical (87 ms worst, t21's large components),
  detail ≈ 5–17 ms, motion ≈ 5–12 ms. The analyzer already pays Canny + components +
  optical flow per sample, so a bright-extent addition is a minor increment.

## 4. Learned detectors — licence screen first, then measurements

### Licence screen (checked 2026-11-16)

| Candidate | Licence (source checked) | Size | Screen |
|---|---|---|---|
| **YOLOX** via OpenCV Zoo (`object_detection_yolox`) | **Apache-2.0** — zoo model folder ships the full Apache-2.0 LICENSE (read first-hand); YOLOX upstream (Megvii) is Apache-2.0 for code *and* weights (LibreYOLO's licence survey, checked against the repo LICENSE) | int8 ONNX **9,079,452 B** (downloaded + run) | **CLEARED** |
| EfficientDet-Lite0 (Google MediaPipe) | Apache-2.0 (MediaPipe repo + model distribution; multiple independent sources concur, incl. a package publishing byte-identical files + SHA-256) | int8 ≈ 4.4 MB, float16 ≈ 7.3 MB | CLEARED on licence; **not run** — no TFLite runtime in the product (new dependency); see §6 |
| NanoDet (OpenCV Zoo) | Zoo is Apache-2.0; NanoDet upstream Apache-2.0 (per-model licence not individually opened in this spike) | ~1–5 MB class | Not run; weaker/faster alternative, same plug-in point |
| OpenCV HOG people detector | Built into OpenCV (Apache-2.0), no weights file | 0 | CLEARED (but see quality, §5) |
| Ultralytics YOLO (v5/v8/11…) | **AGPL-3.0 over code AND weights per Ultralytics' own licensing page**; their stated position: any commercial product needs their paid Enterprise licence unless the whole project is AGPL | runtime = ultralytics + torch (heavy) | **DISQUALIFIED** — contested/conditional weights licensing from a vendor selling the exemption; also the heaviest runtime. Not run. |
| MobileNet-SSD (Caffe, the classic OpenCV tutorial weights) | Widely mirrored weights ship **without a clear licence** (not verified first-hand in this spike — excluded under the spike's own rule: unclear = disqualified) | ~23 MB | **DISQUALIFIED** |
| YOLO-NAS (Deci) | Code Apache-2.0, **official weights non-commercial** (per LibreYOLO survey) | — | **DISQUALIFIED** |
| OpenCV saliency (spectral residual) | Apache-2.0, but lives in opencv-contrib; the shipped `opencv-python` wheel in the venv **has no `cv2.saliency`** (checked) | 0 | Not run — would need a dependency change for an unproven signal |

### YOLOX int8 — measured on his frames

Runtime note: the product does **not** depend on onnxruntime (SigLIP runs via torch,
YuNet via OpenCV). YOLOX was therefore run **both** ways: onnxruntime (test venv) and
**cv2.dnn — the product's existing dependency**, using the zoo's own wrapper. Detections
were *identical* on the cross-checked frames; cv2.dnn was faster in this sandbox.

| Frame | YOLOX result | vs GT |
|---|---|---|
| m4_t24 night group | **3 persons** (0.92 / 0.92 / 0.66) — all three men | best **IoU 0.917** |
| m4_t36 night pair | **2 persons** (0.92 / 0.86) + spurious "car" 0.31 | best **IoU 0.83** |
| m1_t6 **helmet** duel | **2 persons** (0.92 / 0.89) — guard + Kevin at edge | best **IoU 0.544** (box spans guard+front man) |
| m6_t18 wide, backs | **4 persons** (0.92/0.92/0.89/0.48) — 3 backs + Jimmy small on the structure | union **IoU 0.726** |
| m4_t0 night-vision | 1 person (0.57), body box (not the face) | subject extent correct |
| m4_t18 night-vision | **MISS** (top raw score 0.056) | — |
| m4_t21 drone portrait | **MISS** (top raw: "tie" 0.175, "person" 0.105) | not a COCO object |
| m4_t15 drone portrait | portrait **MISS**; found the foreground silhouetted man (0.73) | portrait invisible to it |
| m4_t12 portrait absent | nothing (top 0.10) — honest negative | — |
| m3_t12 forest, no subject | nothing (top: "broccoli" 0.071) — correct negative | — |

- **Where faces fail and bodies exist, YOLOX is excellent**: helmets, backs, night,
  wide groups, small distant people (Jimmy at ~11% of frame height, 0.48). True-person
  scores were 0.48–0.92.
- **It cannot see the m4 subject at all.** A face made of drone lights is not a COCO class.
  No generic detector fixes m4; only the bright mask does (§3).
- False positives observed: "umbrella" ×2 and "car" ×1 on the metal structure — all
  non-person classes ≤0.47. Restricting to the **person class at ≥0.5** removes every
  false positive seen while keeping every true person except none (min true score 0.48 —
  set the floor at 0.45–0.5 and treat 0.48 as the observed edge).
- **Cost (sandbox, throttled):** onnxruntime 1.23–1.72 s/frame; **cv2.dnn 0.69–0.77 s/frame**
  (640×640 letterbox, int8). At the planner's 3 fps sampling this must NOT run on every
  sample: gate it to samples where YuNet found no significant face, and/or run the subject
  pass at 1 fps. Worst case ≈ 60 gap-samples/min × 0.7 s ≈ 42 s of added analysis per
  source-minute *on sandbox-class CPU*; on Luffy's Ryzen 7 expect several× faster
  (UNVERIFIED — must be timed on his laptop before defaulting on).

### HOG people detector — measured, and dominated

Zero download, already in OpenCV — but: m4_t36 union IoU 0.542 (boxes offset from the
people), m6_t18 0.367 (incl. a false positive on the sky/structure), m1_t6 union 0.24,
m4_t24 one weak false positive (0.22) while missing all three men, night-vision 0/2,
portraits 0. Cost 103–144 ms/frame at 480 w — *slower than the masks, far worse than
YOLOX*. **Don't build.**

## 5. Failure honesty

- **Night-vision green footage defeats almost everything**: YOLOX 1/2 (body box on t0,
  total miss on t18), YuNet 1/2, bright mask useless (uniform green), HOG 0/2. No tested
  method is reliable on NV; t18's tilted large-face framing is the hardest frame in the set.
- **Helmets/visors**: YuNet blind (0), YOLOX 2/2 people found. This is the single clearest
  detector win — MrBeast-style guards/players are a recurring content class for Luffy.
- **Motion blur**: evidence is thin — m1_t6 is mildly blurred and YOLOX handled it;
  stronger blur (m3 fog/action) was not scored against people GT. Claim beyond that is
  UNVERIFIED.
- **Exotic subjects** (light portraits, graphics, products): no semantic detector helps;
  bright extent helps only in dark scenes; in lit scenes there is still *no* presence
  signal for a non-face, non-text subject. That residual gap is real and remains.
- **Sunlit texture** (forest): every mask degenerates; YOLOX is correctly silent. A planner
  must treat "no signal" as "fall back to today's behaviour", never as "crop freely".

## 6. Verdict — PARTIAL: build two pieces

### BUILD-1 — mask-derived subject extent (tiny lane; the only thing that sees m4)

Add to `ContextAnalyzer.measure` (alongside the existing Canny/components work):
largest-component bright-mask box + bright coverage, computed on the gray frame already in
hand, **gated to dark scenes** (low mean luminance). Extend `SceneEvidence` with
`subject_box` (xyxy), `subject_cov`, `subject_source="bright"`. Feed the box into the same
places face boxes already go: the protect set in `reframe_plan` and the framing lane's
amputation audit (a crop that slices the bright subject gets repaired/stamped like a face),
and let the no-face fallback anchor prefer the subject-box centre over the motion centroid.
Measured basis: IoU 0.755 / 0.46 on the failure frames; presence threshold ≈ 0.05 coverage
separates present/absent on all m4 samples. Cost ≈ 15 ms/sample. No new dependency, no
model, no licence surface. **Estimated lane: small (1–2 days incl. tests + rendered proof).**
Caveat: cache schema — `SceneEvidence` is serialized in the reframe cache; adding fields
needs the same schema-bump care the framing lane used for `motion_center`.

### BUILD-2 — YOLOX person pass, gated to no-face samples (small-medium lane)

Ship the zoo's YOLOX int8 ONNX (9.08 MB, Apache-2.0 LICENSE filed next to the model,
mirroring the YuNet precedent in `media/models/`) and run it via **cv2.dnn** (existing
dependency; zoo wrapper is Apache-2.0) **only on samples where YuNet found no significant
face**, person class only, score ≥ 0.5. Person boxes join the protect set / audit /
fallback anchor exactly like face boxes (they are body boxes — for crop protection that's
the right granularity). Measured basis: IoU 0.54–0.92 on helmets/groups/backs/night where
YuNet scores 0. **Estimated lane: small-medium (2–4 days incl. model plumbing, gating,
tests, rendered proof) + a timing check on Luffy's laptop before it defaults on** — if his
CPU cost disappoints, EfficientDet-Lite0 (Apache-2.0, 320 px int8, ~4.4 MB) is the
licence-clean fallback, at the price of a new TFLite dependency (UNVERIFIED-run in this
spike).

### DON'T BUILD

- HOG person detector (dominated on quality *and* cost).
- Motion-mask or detail-mask extents (measured dead ends, §3).
- Ultralytics anything (licence), MobileNet-SSD Caffe weights (licence unclear),
  YOLO-NAS weights (non-commercial).
- Saliency via opencv-contrib (dependency change for an unproven signal; not run).

### What remains UNVERIFIED

- All detector/mask behaviour on **16:9 source** frames (tests ran on 9:16 exports; the
  planner's geometry differs — boxes are normalized so the methods transfer, but the
  numbers should be re-measured on source during BUILD-1/2 proof).
- YOLOX/EfficientDet cost on Luffy's actual laptop; EfficientDet-Lite0 entirely (not run).
- The dark-scene gate threshold for BUILD-1 (derived from 3 m4 frames + 1 forest frame).
- Strong motion blur, rain/fog people shots, and non-MrBeast content classes.

## Artefacts

- Scripts + raw results: `~/workspace/spike-nonface/` (`spike_masks.py`, `spike_yolox.py`,
  `mask_results.json`, `yolox_results.json`, frames in `frames/`).
- Model used for measurement only (not shipped anywhere): `~/workspace/spike-nonface/yolox_int8.onnx`
  (9,079,452 B, Apache-2.0, OpenCV Zoo / Megvii YOLOX) + `YOLOX_LICENSE.txt`.
