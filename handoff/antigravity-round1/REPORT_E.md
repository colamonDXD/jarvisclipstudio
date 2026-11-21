# Job E — Emoji Stickers Backend Module Implementation Report

## Summary
Implemented the emoji stickers backend module (`src.media.stickers`) for SnipNova, along with asset download tooling, curated Noto Emoji 512px PNG assets with an Apache-2.0 manifest, and a comprehensive unit test suite covering rules, determinism, caption band avoidance, counts, gaps, manifest integrity, and ffmpeg filtergraph rendering.

All 12 unit tests pass cleanly.

---

## Files Created (all inside `D:\SnipNova-Dev\antigravity-v12\`)

1. **`tools/assets/fetch_noto_stickers.py`**
   - Downloads 512px PNGs from Google Fonts Noto Emoji repository (`2D/png/512/emoji_u{codepoint}.png`).
   - Generates `assets/stickers/STICKERS_MANIFEST.json` containing SHA-256 hashes, source URLs, and Apache-2.0 license metadata.
   - Handles HTTP 404s gracefully.

2. **`assets/stickers/emoji/*.png`**
   - 37 downloaded high-resolution PNG emoji stickers:
     😂 🤣 💀 🔥 😱 😭 😡 🤯 👀 💯 🙏 😳 🥶 🤡 👑 💸 🚨 ❗ ❓ ✅ ❌ 🎯 🏆 💥 ⚡ 🫡 😤 😎 🤔 🙄 😬 🥲 😈 👻 🧠 🫠 🗿

3. **`assets/stickers/STICKERS_MANIFEST.json`**
   - 37 manifest entries verifying each emoji asset's relative path, source URL, Apache-2.0 license, and sha256 checksum.

4. **`backend/src/media/stickers.py`**
   - `stickers_enabled() -> bool`: Returns `True` only if `STICKERS=1` or `STICKERS=true` (case-insensitive); defaults to `False`.
   - `choose_stickers(signals_by_time, words, duration, max_per_clip=4, min_gap=4.0, seed="") -> list[dict]`:
     - Selects emoji stickers driven by audio signals and transcript word rules:
       - laugh → 😂 or 🤣
       - scream → 😱
       - dead/died/rip/fail → 💀
       - fire/insane/crazy/lets go → 🔥
       - money/cash/paid → 💸
       - `?` → 🤔
     - Enforces caption band avoidance: `y` never in `[0.40, 0.60]`.
     - Strongest signals evaluated first; enforces `max_per_clip` and `min_gap` constraints.
     - Deterministic placement via `seed`.
   - `sticker_overlay_args(events, width=1080, height=1920, assets_dir=None) -> tuple[list[str], str, str]`:
     - Generates FFmpeg `-i` inputs and `-filter_complex` fragment for animated sticker overlays.
     - Pop-in animation: scale 0.6 → 1.1 → 1.0 over 0.25s.
     - Fade-out animation: alpha 1.0 → 0.0 over the final 0.2s.
     - Active only during `[t, t + dur]`.

5. **`backend/tests/unit/test_stickers.py`**
   - Full test coverage for all requirements:
     - `test_stickers_enabled`
     - `test_rules_laugh`
     - `test_rules_scream`
     - `test_rules_words_dead`
     - `test_rules_words_fire`
     - `test_rules_words_money`
     - `test_caption_band_avoided`
     - `test_max_count`
     - `test_min_gap`
     - `test_determinism`
     - `test_ffmpeg_graph_renders`
     - `test_manifest_complete`

---

## Test Output Verbatim

### Quiet execution:
```powershell
Set-Location D:\SnipNova-Dev\antigravity-v12\backend
$env:NO_PROXY="localhost"
$env:PYTHONPATH="../tools/phase2/_winshim"
D:\SnipNova-Dev\opencode\backend\.venv\Scripts\python.exe -m pytest tests/unit/test_stickers.py --no-cov -p no:cacheprovider -q
```

Output:
```
............                                                             [100%]
12 passed in 2.57s
```

### Verbose execution:
```powershell
Set-Location D:\SnipNova-Dev\antigravity-v12\backend
$env:NO_PROXY="localhost"
$env:PYTHONPATH="../tools/phase2/_winshim"
D:\SnipNova-Dev\opencode\backend\.venv\Scripts\python.exe -m pytest tests/unit/test_stickers.py --no-cov -p no:cacheprovider -v
```

Output:
```
============================= test session starts =============================
platform win32 -- Python 3.11.17, pytest-9.0.3, pluggy-1.6.0 -- D:\SnipNova-Dev\opencode\backend\.venv\Scripts\python.exe
rootdir: D:\SnipNova-Dev\antigravity-v12\backend
configfile: pyproject.toml
plugins: anyio-4.14.2, logfire-sdk-5.4.0, asyncio-1.3.0, cov-7.1.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=session, asyncio_default_test_loop_scope=session
collecting ... collected 12 items

tests/unit/test_stickers.py::test_stickers_enabled PASSED                [  8%]
tests/unit/test_stickers.py::test_rules_laugh PASSED                     [ 16%]
tests/unit/test_stickers.py::test_rules_scream PASSED                    [ 25%]
tests/unit/test_stickers.py::test_rules_words_dead PASSED                [ 33%]
tests/unit/test_stickers.py::test_rules_words_fire PASSED                [ 41%]
tests/unit/test_stickers.py::test_rules_words_money PASSED               [ 50%]
tests/unit/test_stickers.py::test_caption_band_avoided PASSED            [ 58%]
tests/unit/test_stickers.py::test_max_count PASSED                       [ 66%]
tests/unit/test_stickers.py::test_min_gap PASSED                         [ 75%]
tests/unit/test_stickers.py::test_determinism PASSED                     [ 83%]
tests/unit/test_stickers.py::test_ffmpeg_graph_renders PASSED            [ 91%]
tests/unit/test_stickers.py::test_manifest_complete PASSED               [100%]

============================= 12 passed in 2.63s ==============================
```

---

## Evidence Artifacts

1. **Demo Clip (5 seconds, 1080x1920)**:
   `D:\SnipNova-Dev\handoff\antigravity-round1\evidence\sticker_demo.mp4`
   - Demonstrates animated emoji overlays (😂 and 😱) triggered by audio and text signals with pop-in scale and fade-out.

2. **3-Frame Verification Strip**:
   `D:\SnipNova-Dev\handoff\antigravity-round1\evidence\sticker_strip.png`
   - Shows frame at `t=0.5s` (before overlay), `t=1.5s` (first sticker active), and `t=3.5s` (second sticker active).

---

## Git Commit & Patches

- **Branch**: `lane-c/round1`
- **Base Tag**: `lane-c/round1-base`
- **Commit**: `b845a6c feat(media): emoji stickers backend module and noto assets`
- **Patch location**:
  `D:\SnipNova-Dev\handoff\antigravity-round1\series_E\0001-feat-media-emoji-stickers-backend-module-and-noto-as.patch`
