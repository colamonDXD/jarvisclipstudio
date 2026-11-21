# Bundled music: provenance (FreePD, CC0)

Approved by Luffy on 2026-11-18 ("Yes, use FreePD").

**Licence.** FreePD.com published every track under the CC0 1.0 Universal Public
Domain Dedication. From the archived home page
(https://web.archive.org/web/20240104044209/https://freepd.com/):

- "This music is all licensed CC0 1.0 Universal Public Domain Dedication."
- FAQ: "Can I include this music in an app that generates movies for users? YES!"

Attribution is not required; we still credit the composers here.

**Source of the files.** freepd.com closed in 2025. The files come from the
Internet Archive mirror, item `freepd` (https://archive.org/details/freepd):
- That item was uploaded anonymously and contains at least one file that is not
  FreePD's, so the item itself is **not** trusted.
- A track is used only if the archived freepd.com page listed it, with the same
  title and running time.
- Each file must match the archive's published MD5. `tools/assets/fetch_freepd_music.py`
  re-downloads and re-verifies them, and refuses to write anything that does not match.

**Not committed to git.** The 7 MP3s (45 MB) are kept out of the patch series. Run
the fetch script to recreate `assets/music/`, or copy them from the handoff folder.

| File (mood stem) | Used by profiles | Title | Composer | Listed on (archived) | Time | Archive MD5 | SHA-256 |
|---|---|---|---|---|---|---|---|
| `comedy.mp3` | meme_comedy | Wakka Wakka | Bryan Teoh | [comedy.php](https://web.archive.org/web/20250104052211/https://freepd.com/comedy.php) | 2:01 | `dec522c5b475f74af7732c089e5b6ec1` | `de261ef809e92732806ebd51534da59b1dda8aee9c9f8bd062c4234aa30eb10c` |
| `energetic.mp3` | gaming, sports | Goodnightmare | Kevin MacLeod | [electronic.php](https://web.archive.org/web/20241217205507/https://freepd.com/electronic.php) | 4:00 | `197b28aae16e36c5ac41a3e0f3e73124` | `b3793f15317a70bbbd857fa4a77872a42bcdc27445f25bce397bb7a442f91b9c` |
| `upbeat.mp3` | vlog_lifestyle | Arpent | Kevin MacLeod | electronic.php (as above) | 2:42 | `b1a53b51752ca0b3dc92c6f83bbd99c1` | `ef06d8b524c196a31954dfb1f3261e7d37459e43f4afd9df31ee2965c6094a56` |
| `corporate.mp3` | finance_business | Meditating Beat | Kevin MacLeod | electronic.php | 2:37 | `d27d2c4906bc409ed6259f349c712edf` | `e498c2015a413e6cba5ab8dbef2a60cdebf7c4d4a97f9edd84851752ff49d3b6` |
| `calm.mp3` | education_tutorial, hindi_podcast, travel_vlog, japan_travel_cinematic, Auto bed | Study and Relax | Kevin MacLeod | [home page, featured](https://web.archive.org/web/20240104044209/https://freepd.com/) | 3:43 | `2c6f64b4065f2e71f09a7337e2880872` | `9cba2a3c2d1c9d364220db1691f618a0f25b8d9cf6f2a857fe389ef614fd18a4` |
| `funk.mp3` | anime_funk_edit | Fenster's Explanation | Kevin MacLeod | comedy.php | 2:49 | `0ba77f5905c35a4618e311b5c2ff9d81` | `c9f773e0a153f63764eac84fb3e3fcff6b67abb494b89925c025abfe5a735edd` |
| `phonk.mp3` | football_edit | Beat One | Kevin MacLeod | electronic.php | 3:00 | `1a068ec590ebc360a5357e539f8a9764` | `930dd4c6c88a2abb153f52e56a7c74bd041008f5afa4835602469dba3676aed8` |

**Honest notes**
- FreePD has no phonk. `phonk.mp3` is the closest drum-and-bass track ("Upbeat, drum
  and bass styled track. Modern and dark.").
- `funk.mp3` is funky comedy brass, not modern funk.
- This is one track per mood, so every clip in a mood gets the same bed. A bigger
  library (several tracks per mood, picked by tempo) is Phase 3 work.
- Three files embed cover art (an MJPEG stream). The renderer reads audio only;
  checked in a real render.

# Bundled sound effects: generated, original (CC0)

`assets/sfx/{whoosh,pop,riser,ding,impact,sting}.wav` are the six cue names the EDL
places (`auto_director.edl.SFX_EVENT_NAMES`). They are **synthesised** by
`tools/assets/make_sfx.py` from oscillators and filtered noise with ffmpeg: no
samples and no third-party audio, so there is nothing to license. They ship CC0 with
the project. `impact` is a deep, gritty sub-bass hit, the closest original take on a
meme "boom".

Meme sounds downloaded from sites such as Myinstants have no stated licence or
provenance, so they are **never bundled**. A user who wants one uploads it to their
own SFX library, which the renderer already searches after `assets/sfx`.
