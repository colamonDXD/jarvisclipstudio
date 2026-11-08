# Transcription speed: model research and the turbo option

Research and implementation notes for the `transcription-speed` lane
(Part A: research, Part B: what shipped). Target hardware: the user's
laptop — Ryzen 7, **RTX 4050 Laptop GPU with 6 GB VRAM**, 16 GB RAM,
Windows + Docker. The engine stays faster-whisper/CTranslate2
(`TRANSCRIPTION_PROVIDER=faster-whisper`); this lane only widens the
model/batch choices around it.

Word timestamps are load-bearing in this product — karaoke captions and
highlight selection are built from them — so every candidate below was
judged on word-timestamp support first, speed second.

## TL;DR verdict

| Rank | Option | Verdict |
| --- | --- | --- |
| 1 | **`large-v3-turbo`** | **Recommended upgrade.** ~8× faster decoding than large-v3 (OpenAI's relative-speed figure), English accuracy within ~0.2–0.4 WER points of large-v3, multilingual, full word timestamps in faster-whisper. Fits 6 GB at float16 with the reduced batch this lane ships (8). Weakness: measurably worse Hindi than large-v3 (see below) and rare repetition loops reported on noisy audio. |
| 2 | `large-v3` | The accuracy ceiling, including the best Hindi. Also fits 6 GB via faster-whisper float16 (~4.5 GB class), but is the slowest of the large family — pick it when a video matters more than the wait. Not benchmarked on this VM (CPU transcription of large-v3 is impractical here); figures are from the sources below. |
| 3 | `distil-large-v3` / `distil-large-v3.5` | Fastest English option (~1.5× faster than turbo in the distil-whisper project's own long-form tests). **English only** — disqualified as a default for a user who makes Hindi/Hinglish content, but a legitimate opt-in for English-only channels. In faster-whisper, v3 is a built-in name (`distil-large-v3`); v3.5 loads by repo id `distil-whisper/distil-large-v3.5-ct2`. |
| 4 | `medium` (current default) | Stays the default. Turbo has never been run on the user's GPU by us, and medium's Hindi behaviour is the known quantity today. Switching is one env var; the evidence below is why turbo is the recommended *opt-in*, not a silent default swap. |
| — | WhisperX | **Rejected.** It wraps faster-whisper and adds a wav2vec2 alignment model (+~2 GB VRAM) and a torch/pyannote stack. On 6 GB the full pipeline with a large-class model does not fit comfortably, it is a heavy new dependency (against this lane's constraints), and the DTW word timestamps faster-whisper already produces are what the caption pipeline was built and tested against. |
| — | Parakeet (NeMo) | **Rejected.** `parakeet-tdt-0.6b-v3` covers 25 European languages only — **no Hindi** — and requires the NeMo toolkit (heavy dependency tree). Its speed is real but irrelevant for this user's content mix. |
| — | CrisperWhisper / insanely-fast-whisper | **Rejected.** CrisperWhisper is a research checkpoint for verbatim transcription (Transformers-based, no maintained CTranslate2 path); insanely-fast-whisper is a CLI wrapper around the same Transformers pipeline — neither beats staying on faster-whisper. |

## Evidence

### large-v3-turbo
- Architecture: large-v3 with the decoder pruned from 32 layers to 4 and
  fine-tuned; 809M parameters (vs 1,550M). Same encoder — which is why
  faster-whisper's cross-attention/DTW word timestamps work unchanged.
  Source: https://huggingface.co/openai/whisper-large-v3-turbo
- Speed: OpenAI's Whisper README lists turbo at **~8× the relative speed
  of large** (A100, English). Independent English WER: HF Open ASR
  Leaderboard means 7.83% (turbo) vs 7.44% (large-v3); LibriSpeech clean
  2.10% vs 2.01%. Sources: https://github.com/openai/whisper,
  https://whispernotes.app/blog/introducing-whisper-large-v3-turbo
- VRAM: official reference-implementation figure is ~6 GB, but that is
  PyTorch. Under CTranslate2 float16 the fp16 weights are ~1.6 GB and
  faster-whisper's published large-v2 fp16 footprint is ~4.5 GB, so
  turbo at batch 8 sits comfortably in 6 GB; batch 16 is the risky
  setting, which is why this lane defaults large-class CUDA models to
  batch 8 (see Part B). Sources:
  https://github.com/SYSTRAN/faster-whisper,
  https://nexgpu.net/en/models/whisper/
- Hindi caveat (the honest weak point): in a 1,749-run FLEURS study,
  large-v3 scored 15.7% WER on Hindi vs **turbo 22.3%** (English 4.1% vs
  4.7%); the same study caught four repetition-loop hallucinations, all
  from turbo/small, none from large-v3. FLEURS is clean read speech, so
  treat the *gap* (≈ +6 points on Hindi) as the signal, not the absolute
  numbers. For Hindi-first videos, `large-v3` or the current `medium`
  default remain the safer picks; turbo's pruned decoder costs the most
  exactly on lower-resource languages.
  Source: https://vocova.app/blog/ai-transcription-accuracy-benchmark-2026
- Download: ~1.6 GB (CTranslate2 fp16 weights), into the Hugging Face
  cache inside the worker's Docker volume — the C: drive is not touched.
  Licence: MIT (Whisper weights), same as the models already in use.

### distil-whisper (v3 / v3.5)
- Distilled from large-v3 with the encoder frozen and a 2-layer decoder;
  756M parameters. The project describes the family as its **English**
  line: "as the newest addition to the Distil-Whisper English family"
  (v3.5 card). English-only ⇒ no Hindi/Hinglish. Sources:
  https://huggingface.co/distil-whisper/distil-large-v3.5,
  https://github.com/huggingface/distil-whisper
- Speed/accuracy vs turbo (project's own tables): v3.5 runs ~1.46×
  turbo's RTFx on long-form, short-form OOD WER 7.08 vs turbo 7.30,
  long-form OOD WER 11.39 vs turbo 10.25 — faster on clips, slightly
  behind on long videos. Same card documents the faster-whisper
  integration and the `-ct2` repo id used above. MIT licence.

### WhisperX / Parakeet rejection notes
- WhisperX VRAM accounting (model + alignment + diarization, RTX 3090
  measurements): medium pipeline ≈ 10 GB, large ≈ 14 GB; alignment alone
  adds ~2 GB on top of the ASR model. On a 6 GB card that forces a small
  ASR model — a downgrade, not an upgrade.
  Source: https://deepwiki.com/murtaza-nasir/whisperx-asr-service/6.2-model-selection
- Parakeet v3's official language list is 25 European languages (NVIDIA
  NGC card) — Hindi is not among them; runtime is NeMo.
  Source: https://catalog.ngc.nvidia.com/orgs/nim/nvidia/containers/parakeet-0.6b-tdt/

## CPU benchmark (this VM — relative numbers ONLY)

This VM has **no GPU**. These numbers say nothing about how fast the
user's RTX 4050 will be; they only rank the models relative to each
other under identical settings. Settings mirror production:
faster-whisper 1.2.1, BatchedInferencePipeline, batch 16, int8 (the CPU
compute type the app itself selects), `vad_filter`, `word_timestamps`.
Sample: JFK's 1961 address excerpt (11 s) looped 12× = **132 s** of
continuous English speech; WER is computed against the known text.

| Model | Load (incl. first download) | Transcribe | RTFx | Words with timestamps | WER vs known text |
| --- | --- | --- | --- | --- | --- |
| medium | 414 s (download-dominated) | 139.2 s | 0.95× | 263 | 6.1% |
| large-v3-turbo | 12.5 s (cached) | 77.7 s | 1.70× | 257 | 8.3% |
| distil-large-v3.5 (`-ct2`) | 721 s (download-dominated) | 73.7 s | 1.79× | 263 | 14.4% |

Read the WER column carefully: on this trivially repetitive sample the
*medium* model "wins" because it reproduces all 12 loops most
faithfully; turbo and distil each dropped or merged loop repetitions,
which the WER counts heavily. This is a pipeline sanity check (text
quality + word timestamps present: 257–263 timestamped words from
every model), **not** an accuracy ranking — the published benchmarks
above are the accuracy evidence. The speed ranking matches the
literature's direction: turbo ≈ 1.8× faster than medium on the same
hardware and settings, distil a hair faster still — at settings parity
the CPU gap between turbo and distil is small (their published ~1.5×
gap is measured on GPU long-form).

An earlier full-length run of this benchmark was lost when the bench
process was OOM-killed at exit (the VM is small); the figures above come
from re-runs of the identical script and settings, one model at a time.

## Part B — what shipped

1. **Model choice is now documented and plumbing-verified.**
   `WHISPER_MODEL_SIZE` already flows untouched into `WhisperModel()`,
   so `large-v3-turbo`, `distil-large-v3` and
   `distil-whisper/distil-large-v3.5-ct2` are selectable today with no
   code change. `.env.example` now lists them. The **default stays
   `medium`** (docker-compose + .env.example unchanged in that respect).
2. **`WHISPER_BATCH_SIZE` env (default 0 = auto).** Resolution order:
   env override (clamped 1–64) → large-class models on CUDA get 8 →
   everything else gets 16 (the value the pipeline has always used, so
   unset = byte-identical behaviour). Plumbed through
   `docker-compose.yml` for both worker services.
3. **CUDA OOM discipline in the batched path.** If a batched
   transcription fails with a CUDA out-of-memory error, the pipeline
   retries once with the batch halved; if it OOMs again it reloads the
   model on CPU int8 (the existing fallback compute path) and retries
   there; only then does the historic sequential fallback run. Non-OOM
   failures behave exactly as before (one batched attempt, then
   sequential). A sequential retry after a CPU fallback uses the CPU
   model, so it cannot re-OOM on the same settings.

## How to enable turbo on the laptop

In `.env` (project root), then recreate the workers
(`docker compose up -d worker ai-worker`):

```ini
TRANSCRIPTION_PROVIDER=faster-whisper
WHISPER_MODEL_SIZE=large-v3-turbo
# WHISPER_BATCH_SIZE=0   # auto: 8 for turbo on CUDA; raise to 16 only
                         # if you watch VRAM and it fits your card
```

The first task after the switch downloads ~1.6 GB of weights once into
the Docker volume cache; later tasks reuse them. For a Hindi-first
video, prefer `WHISPER_MODEL_SIZE=large-v3` (slower, best Hindi) or the
default `medium`.

**Rollback:** set `WHISPER_MODEL_SIZE=medium` (and
`WHISPER_BATCH_SIZE=0`) and recreate the workers. Nothing else changes;
transcript cache format and downstream consumers are untouched.

## Honest limits

- **No GPU verification.** Turbo on the actual RTX 4050 6 GB — real
  speed, real VRAM headroom at batch 8/16 — cannot be measured from
  this VM. The batch-8 default and the OOM retry chain are the safety
  design for exactly that uncertainty; the first laptop run is the real
  test, and the worker log line
  `Loading faster-whisper model: large-v3-turbo (device: cuda, compute: float16)`
  plus the transcription timing will confirm it.
- **Hindi quality is from published benchmarks, not our own test.**
  We have no Hindi speech sample + reference transcript in the repo to
  measure with; the FLEURS gap (+~6 WER points for turbo) is the best
  available evidence and is why the default did not move.
- Word timestamps were verified present for all three benchmarked
  models in the CPU runs above (257–263 timestamped words on the same
  sample), all produced by faster-whisper's DTW path.
- `large-v3` (non-turbo) was not CPU-benchmarked — at int8 on this VM
  it is too slow to be a useful relative data point, and its role here
  is the published-accuracy fallback for Hindi, not a speed candidate.
