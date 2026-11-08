# Music library

The create bar's **Music** menu lets you pick the background music for a
generation: leave it on **Auto** and the niche profile's mood chooses a
track, pick any library track to use it for every clip in the task, or
upload your own. The library is **folder-driven** — any audio file in
either music folder appears in the menu automatically, with a preview
play button per track.

## The two folders

| Root | In-container path | Host path | Writable |
| --- | --- | --- | --- |
| Bundled | `/assets/music` | `<project>/assets/music` (mounted `./assets:/assets:ro`) | No (read-only mount) |
| User uploads | `/app/uploads/user_music` | Inside the Docker `uploads` volume | Yes, via the app |

The bundled folder is the repo's `assets/music` — the same library the
renderer has always resolved profile moods (`calm`, `energetic`, …)
against. Its filename stems are the mood names profiles ask for.

The user folder defaults to `user_music` under the app's temp tree
(`TEMP_DIR`, `/app/uploads` in Docker — the `uploads` named volume
shared by backend, worker and ai-worker at the same path), so a track
uploaded through the API is visible to the render workers with no
compose change. Set `USER_MUSIC_DIR` to move it (for example to a host
folder you manage yourself).

When both folders hold the same filename, the bundled copy wins;
uploads whose name collides get a `_2`, `_3`, … suffix instead of
shadowing a bundled track.

## Resolution at render time

A clip's EDL names music either by profile mood (a stem like `calm`) or
by an exact track (a per-task `music_override`, or a re-render choice).
The renderer resolves the name against `assets/music` first — exact
filename, or stem with any audio extension — then against the user
folder. If nothing resolves, the render still succeeds without music,
and the worker log names the missing track and both folders.

A per-task override keeps the profile's volume and ducking when the
profile carries music. On a profile with no music (for example
podcast), the chosen track plays at volume 0.25 with −12 dB ducking so
the pick is actually audible under the voice.

## Upload rules

- Formats: `.mp3`, `.wav`, `.ogg`, `.m4a`, `.flac`.
- Size cap: 30 MB (larger uploads are refused with a clear error and no
  partial file is kept).
- Filenames are sanitized to a recognizable safe basename; the track is
  selectable immediately after upload.

## Credits (CREDITS.json)

Licensed tracks (for example CC-BY music) can carry their attribution
into every clip's `post.txt`. Drop a `CREDITS.json` file into either
music folder:

```json
{
  "calm-piano.mp3": "Calm Piano by Kevin MacLeod (CC BY 4.0)",
  "phonk-loop.mp3": "Phonk Loop by A. Artist — via the starter pack"
}
```

When a clip's EDL used that **exact track** (a per-task pick), the
bundle's `post.txt` gains a final line: `Music: <credit line>`. Tracks
resolved from a profile mood are not recorded in the EDL, so no credit
is claimed for them. With no manifest — or no matching entry — `post.txt`
is written exactly as before.

## A note on commercial music

The bundled library ships only tracks with licences that allow this
use. Trending or commercial songs are **user-supplied only**: add them
through the Custom music upload (or by placing files in the user
folder) under your own responsibility for the rights to use them.
