# Framing Mode — API Contract for the Frontend

Branch: `feat/framing-mode` (backend only; nothing merged yet).
Requested by Luffy, 2026-11-16: when creating a task, a 3-way choice for
how the 16:9 source sits in the 9:16 output — the whole frame fitted
inside with blurred bands (his pick from the podcast screenshot), the
full-bleed crop, or today's automatic behaviour.

## The field

| | |
|---|---|
| Create field | `framing_mode` — a sibling of `template_mode` in the `POST /tasks` JSON body |
| Values | `"auto"`, `"fit"`, `"crop"` (lowercase; the API normalises case and surrounding spaces) |
| Optional? | Yes. Omit it or send `null` to leave the task unset |
| Read-back field | `framing_mode` on the task object — present in BOTH the single-task response (`GET /tasks/{id}`) and the task list (`GET /tasks`), as the stored string or `null` |
| Invalid value | **422** — a present value that is not one of the three is rejected at create, exactly like a bad `template_mode`. There is no silent fallback |

## What each value does (plain words)

- **`auto`** — *Let the app decide.* The layout planner looks at each
  shot and picks fit or crop the way it does today.
- **`fit`** — *Show the whole picture.* The complete 16:9 frame sits
  inside the 9:16 output for every shot, with blurred bands filling the
  space above and below. Nothing is ever cut off at the sides — this is
  the podcast look from Luffy's screenshot. Faces are still tracked, so
  captions and the hook title still keep off faces.
- **`crop`** — *Fill the whole screen.* Every shot is the tight
  full-bleed face-tracked crop (the finance-clip look). This is a
  framing choice only: it does **not** turn on Plain's other rules —
  an Auto-template task with `crop` framing keeps its music, B-roll,
  effects and transitions.

## How it interacts with the template choice

`framing_mode` is independent of `template_mode`. A framing the user
explicitly picks always wins over the template's default framing:

| Template | Framing sent | Result |
|---|---|---|
| Auto | *(unset)* | Planner decides per shot (today's behaviour) |
| Auto | `auto` | Planner decides per shot (same as unset) |
| Auto | `fit` | Always fit |
| Auto | `crop` | Always crop, everything else stays Auto |
| Plain | *(unset)* | Always crop (today's Plain behaviour) |
| Plain | `auto` | Planner decides the framing; the rest of Plain (no B-roll/music/effects) still applies |
| Plain | `fit` | Always fit; the rest of Plain still applies |
| Plain | `crop` | Always crop (same framing as unset Plain) |

Tasks created before this control existed read back `framing_mode:
null` and behave exactly as they always have.

## UI guidance

- Render it as a 3-option segmented control next to the template
  choice: **Auto / Fit (whole picture) / Crop (full screen)**.
- **Always send the selected value**, including `"auto"`. Sending
  nothing is not the same as `"auto"` on a Plain task (unset = Plain's
  crop default; `"auto"` = the planner decides). Sending the explicit
  value makes the control mean the same thing on every template.
- Default selection: **Auto**.
- The choice is stored on the task, so re-renders, regeneration and
  hook previews keep it automatically — no need to resend it anywhere.
  Read it back from the task object if the editor needs to show it.

## Backend notes (for whoever reviews the merge)

- Storage: `tasks.framing_mode` (migration `20261116_0001_task_framing_mode.sql`).
- Validation: `validate_framing_mode` in `src/auto_director/framing_mode.py`;
  precedence resolution: `resolve_framing_mode` in the same module.
- The EDL carries the decision for debugging: `edl["reframe"]["framing"]`
  is `"fit"` / `"crop"` on forced plans (absent on planner plans), and
  forced-fit shots carry reason `user_fit`.
