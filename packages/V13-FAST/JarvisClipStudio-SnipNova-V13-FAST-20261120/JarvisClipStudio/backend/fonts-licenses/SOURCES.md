# Bundled font sources

Fonts in `backend/fonts/` added for the caption system (2026-11-09):

- `Montserrat-Black.ttf`, `Montserrat-ExtraBold.ttf` — Google Fonts
  (fonts.gstatic.com, Montserrat v31 statics). SIL Open Font License 1.1 —
  see `OFL-Montserrat.txt`.
- `NotoSerif-Regular.ttf` — Google Fonts (fonts.gstatic.com, Noto Serif
  v33 static). SIL Open Font License 1.1 — see `OFL-NotoSerif.txt`.

Licence texts live here, NOT inside `backend/fonts/`, because the caption
renderer hands that folder to libass and non-font files in it have broken
renders before.
