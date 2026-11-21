# Asset library: music, sound effects, keyless B-roll

Luffy's brief (2026-11-18): download wide, ship clean. Sanji's rule: anything
inside the V12 package passes a licence check at packaging.

## How the library is built

| Step | Tool | Output |
|---|---|---|
| 1. Download wide | `tools/assets/fetch_library.py openverse|kenney|freepd` | `asset-staging/` (gitignored) plus `asset-staging/manifest.jsonl`, one line per file: source, author, licence, sha256 |
| 2. Synthesise originals | `tools/assets/make_sfx.py`, `tools/assets/make_meme_sfx.py` | `assets/sfx/*.wav`, `assets/sfx/library/meme/...` |
| 3. Curate | `tools/assets/curate_library.py` | `assets/sfx/library/<cue>/`, `assets/music/library/<mood>/`, `assets/ASSET_MANIFEST.json` |
| 4. Gate | `tools/assets/check_licences.py` | prints `ship-clean` or lists every blocking file (exit 1) |

**Allowed licences for shipped files:**
- `CC0-1.0`
- `PDM-1.0` (Public Domain Mark)
- `original` (made here)

Every shipped file must have a manifest entry whose sha256 matches.

## Sources

| Source | What | Licence | How it is checked |
|---|---|---|---|
| FreePD (Internet Archive mirror) | music | CC0 | The title must be listed on an archived freepd.com page (`tools/assets/freepd_listed.txt`, 324 tracks) and the file must match the archive's MD5. The mirror holds foreign files (a BBC Earth track, for example), so unlisted files are never taken. |
| Kenney (kenney.nl) | UI, impact, digital, sci-fi, casino and RPG sounds; jingles | CC0 (stated on every pack page) | Pack page checked for "CC0"; zip sha256 recorded |
| Openverse (api.openverse.org) | Sound effects (mostly Freesound) | Filtered `license=cc0,pdm` per item | Licence, author and landing page recorded per file |
| Synthesised (ffmpeg oscillators/noise) | the 6 cue sounds; the meme pack (booms, boing, rimshot, sad trombone, slide whistle, scratch, horn, crickets, glitch, heartbeat) | original | No samples used |

**Not used, on purpose:**
- **Meme-sound boards (myinstants), Pinterest and GIF services (klipy):** their
  items carry no licence. Nothing from them could pass the gate, so we don't
  download them. Users can still upload their own sounds to their SFX library.
- **Pixabay Music, Mixkit and Sonniss:** free to use, but their terms forbid
  redistributing the files as a library, which shipping them would be.

## How the renderer uses it (`backend/src/media/asset_library.py`)

- **Music:** a mood (`comedy`) plays one track from `assets/music/comedy.mp3`
  plus `assets/music/library/comedy/*`. The pick comes from a hash of the clip
  id, so a re-render keeps its track and different clips get different tracks.
  An explicit track pick is never swapped. Beat-grid clips keep the stem file,
  because their grid was analysed on it.
- **SFX:** each cue event (whoosh, pop, riser, ding, impact, sting) gets its own
  variant. A profile's `audio.sfx_pack` (`meme` for Meme & comedy) draws from
  `assets/sfx/library/<pack>/<cue>/` first.
- **New moods:** a mood that exists only as a folder (`epic`, `suspense`,
  `emotional`, `cinematic`, `world`) now resolves.
- **Switch:** `ASSET_VARIANTS=off` restores the single stem file.
- **Plain template:** it carries no music and no SFX, so variants never reach a
  Plain render.

## Keyless B-roll (`backend/src/media/openverse_broll.py`)

When neither Pixabay nor Coverr has a key, or neither finds a hit, Auto B-roll
cues ask Openverse for CC0 / public-domain photos.
- Each photo goes through the same relevance gate and vision veto.
- It is rendered as a slow-zoom clip.
- Anonymous limits: 20 requests a minute and 200 a day. A failure just skips the cue.
- Switch: `BROLL_OPENVERSE=off`. Unit tests run with it off (autouse fixture);
  `test_openverse_broll.py` opts in against a fake network.
