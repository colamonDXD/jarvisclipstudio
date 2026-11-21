# ASSET SOURCES REGISTRY

*Grounded in the code at `next-milestone` @ `6793620` (2026-11-12):
`backend/src/media/stock_library.py`, `pixabay_broll.py`,
`coverr_broll.py`, `backend/src/broll.py`, `docs/music-library.md`.
Licence wordings below are the ones recorded in-tree; if a provider's
terms page and this page disagree, the provider's current terms win
and this page needs an update.*

## 1. Stock video — auto-director B-roll (EDL renders)

Resolution order per planned cue, at render time
(`media/pixabay_broll.py::resolve_edl_broll`): **Pixabay first, Coverr
only when Pixabay is unconfigured, fails, returns nothing, or no hit
passes the relevance gate.** Either provider failing never blocks the
other; with neither key set, resolution is a no-op and clips render
with no stock inserts. Search queries are sense-resolved (the spoken
sense of an ambiguous word, never the bare noun), hits must pass a
tag-relevance gate, and — with the opt-in SigLIP veto — a vision
check. A cue with no acceptable hit is skipped, never filled with
unrelated footage.

| Source | Key | Terms as recorded in-tree (`stock_library.PROVIDER_LICENCES`) | Limits / notes |
| --- | --- | --- | --- |
| **Pixabay** | `PIXABAY_API_KEY` (free key, user-supplied) | "Pixabay Content License — free for commercial and non-commercial use, no attribution required" | Primary source. |
| **Coverr** | `COVERR_API_KEY` (free Demo key, no card) | "Coverr License — free for commercial and non-commercial use in anything you make and sell (editing/remix permitted); files may not be resold standalone or used to train AI; **Demo-tier API apps must credit Coverr and the creator**" | 50 requests/hour (verified 2026-11-12 against coverr.co, recorded in `coverr_broll.py`). Catalogue is mostly 16:9. The credit duty is discharged via the library manifest (below) plus a "Stock footage: Coverr" line on published clips that use it. |

**Provenance:** every download from either source lands **once** in
the shared stock library, `<temp_dir>/stock_library/` as
`<provider>_<asset_id>.mp4`, with a sidecar `manifest.json` recording
provider + asset id, file name, title/tags/dimensions, page and
download URLs, **the licence the asset was fetched under**, download
time, and which EDL entries used it (`media/stock_library.py`). The
manifest is the provenance record — keep it with the library.

## 2. Stock video — editor / legacy path (Pexels)

`backend/src/broll.py` (upstream SupoClip module) is **live code**:
it serves the clip editor's B-roll picker and AI-edit flows
(`task_service.BRollSession`, `editor_service`, `ai_edit_service`)
against the Pexels API with `PEXELS_API_KEY`.

- **Status: usable only with a pre-existing key.** Pexels paused new
  API key issuance (recorded in `pixabay_broll.py`'s header and the
  2026-11-12 provider sweep); no reopening date is known in-tree.
- In niche profiles, the legacy value `source: "pexels"` now means
  "stock B-roll wanted": auto-director renders resolve through
  Pixabay/Coverr (§1) and stamp the real provider per entry.
- Pexels downloads cache alongside the editor flow (file naming
  `pexels_<id>_<w>x<h>.mp4` in `broll.py`); they are **not** part of
  the §1 manifest library.

## 3. Evaluated and not used

| Source | Verdict | Reason (one line) |
| --- | --- | --- |
| Openverse | **Not implemented** | No code in tree; Coverr passed verification as the second source, and videos were preferred. |
| Pexels (for new users) | **Blocked upstream** | New API key issuance paused — see §2. |
| DeepFilterNet (audio denoise, not stock) | **Rejected on measurement** | Worsened Whisper WER in testing (`DENOISE_REPORT.md`, research notes). |

## 4. Music and SFX — folder-driven libraries, nothing bundled

Both libraries are **folder-driven**: any audio file in the root
folders is listed and placeable, with no registration step
(`docs/music-library.md`, `media/sfx_library.py`).

| Library | Bundled root (read-only in Docker) | User root (writable) |
| --- | --- | --- |
| Music | `<project>/assets/music` → `/assets/music` (mount `./assets:/assets:ro`) | `user_music` under the app temp tree (`uploads` volume); `USER_MUSIC_DIR` overrides |
| SFX | `<project>/assets/sfx` → `/assets/sfx` | `user_sfx` likewise; `USER_SFX_DIR` overrides |

**Provenance status — flagged gap.** The git tree and every release
package ship **no audio files at all** (verified 2026-11-12: `assets/`
in git and in the V5 ZIP contains only `banner.jpg`). The bundled
folders' contents exist only on the user's installed machine, carried
over from his original upstream clone, and **their provenance and
licence are not recorded anywhere in-tree — UNVERIFIED.** Until a
per-track licence record exists, treat the on-machine library as
personal-use material: do not redistribute it, and do not describe
any track as licence-cleared. User uploads are the user's own
responsibility on the same basis.

## 5. Fonts

- **Caption/overlay fonts — bundled:** 25 files in `backend/fonts/`
  (Anton, Archivo Black, Bangers, Barlow Condensed, Bebas Neue, DM
  Sans, Inter, League Spartan, Montserrat ×3, Noto Serif, Nunito
  Sans, Open Sans, Oswald, Poppins, Raleway, Roboto, Rubik, Sora,
  THEBOLDFONT, TikTok Sans ×2, Urbanist, Work Sans). **No licence
  files ship in that folder** — per-font licence is **UNVERIFIED
  in-tree** (most names match well-known open font families, but the
  tree records no licence texts; `THEBOLDFONT` and `TikTokSans`
  provenance is likewise unrecorded). Do not redistribute the font
  files as a standalone pack.
- **UI fonts:** Inter, JetBrains Mono, Space Grotesk via
  `next/font/google` (`frontend/src/app/layout.tsx`) — fetched by
  Next.js from Google Fonts, not bundled.

## 6. Other bundled assets

- `assets/banner.jpg` — the only file under `assets/` in git and in
  the release packages.
- YuNet face model — a model, not media; see
  `MODEL_REGISTRY.md` §5 (MIT, bundled with its licence file).
- Colour LUTs are folder-driven code-side (`media/lut_library.py`);
  no `.cube` files ship in the tree.
