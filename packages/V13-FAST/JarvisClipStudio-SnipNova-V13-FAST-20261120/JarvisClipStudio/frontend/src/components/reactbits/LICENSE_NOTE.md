# React Bits — vendored components

Components in this folder are adapted from **React Bits** by David H. (DavidHDev):

- Site: https://reactbits.dev
- Source: https://github.com/DavidHDev/react-bits

## License

React Bits is released under the **MIT License + Commons Clause**. In plain
terms: the components are free to use in personal and commercial projects —
including inside this app — but the components themselves may not be sold,
redistributed, or repackaged as a component library / UI kit. We vendor them
only as part of this application's own UI, which is a permitted use. This note
must stay with the folder.

## What is here, and how it was adapted

All adaptations are deliberate: the app is a working tool, so every effect is
kept lightweight (no WebGL context) and retuned to the app's darker theme —
deep, low-luminance glows and soft sheens, never bright white washes or
flashlight glare.

| File | Upstream component | Adaptation |
| --- | --- | --- |
| `AppBackground.tsx` | Galaxy (Backgrounds) | Upstream is a WebGL (ogl) shader starfield. Re-implemented as a lightweight canvas-2d starfield with the same look: twinkling stars, a slow warp drift and subtle cursor repulsion, dimmed hard for the dark theme. The loop pauses when the tab is hidden; under prefers-reduced-motion one static frame is drawn. Replaced the earlier CSS Aurora / Pattern Waves adaptation (2026-11-18). |
| `GradientText.tsx` | GradientText (TextAnimations) | CSS-driven variant: animated `background-clip: text` gradient over the app's `--brand` → `--brand-2` tokens. |
| `ShinyText.tsx` | ShinyText (TextAnimations) | CSS-driven variant: a soft sheen sweep tinted with `--brand-2` (muted blue-grey, not white), sweeping once per cycle then resting, mirroring the app's existing title-sheen pattern. |
| `SpotlightCard.tsx` | SpotlightCard (Components) | Lean variant: pointer tracking via CSS variables and a CSS radial-gradient overlay. Upstream runs a `requestAnimationFrame` loop with grain/flare; this keeps only the pointer-tracking glow, at low intensity (dim warm-neutral). |

The shared CSS for these components lives in `src/app/globals.css` under the
"React Bits polish" section.
