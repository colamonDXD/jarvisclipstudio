# API follow-ups

- Add a clip re-render endpoint that accepts `niche_override` (one of the eight values used in `frontend/src/lib/niches.ts`) and returns the refreshed clip. The results-card “Re-render as…” selector is intentionally disabled until this exists; no equivalent route is currently exposed.
- Return the resolved, actually-used niche on each clip (for example `niche_profile`) from the task clips endpoint. Until then, the results card can show only the task or clip `niche_override`; “Auto-detect” does not reveal the classifier’s result.
