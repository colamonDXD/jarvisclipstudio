# Free-API fallback routes — LiteLLM gateway wiring (2026-11-11)

**State: IMPLEMENTED / INDIVIDUALLY TESTED on branch — NOT merged, NOT packaged, NOT uploaded. USER-MACHINE VALIDATION PENDING.**

- Worktree: `~/workspace/jcs-freeapi`, branch `feature-freeapi-routes` off next-milestone @ `b555eca`
- Commits: `c1f8b1d` feat(gateway) · `188a90f` test(gateway) · `c873333` docs(changelog)
- Scope honoured: config + env example + tests + changelog only. No sign-ups, no real keys, no network calls to DeepSeek/OpenRouter, no provider-manager code changes.

## What was wired

Two new deployments in `litellm_config.yaml`, appended to the END of every router fallback chain — after Groq and the local route, so they are only reached when everything else has failed:

| Alias | LiteLLM model | Endpoint | Key env |
|---|---|---|---|
| `deepseek-chat` | `openai/deepseek-chat` | `https://api.deepseek.com` (api_base, mirroring the `nim-deepseek` pattern) | `os.environ/DEEPSEEK_API_KEY` |
| `openrouter-free` | `openrouter/deepseek/deepseek-chat-v3.1:free` | LiteLLM's built-in OpenRouter base (same as existing `or-ultra-free`) | `os.environ/OPENROUTER_API_KEY` |

Resulting chains (existing entries keep their exact order; only the suffix is new):

- `clip-fast`: or-ultra-free → nim-deepseek → clip-assured → local-qwen → **deepseek-chat → openrouter-free**
- `clip-assured`: groq-20b → local-qwen → **deepseek-chat → openrouter-free**
- `local-qwen`: clip-assured → **deepseek-chat → openrouter-free**

Order rationale (from the research report): DeepSeek's official API is the only true-API free capacity in the set (new-account grant, widely reported as 5M tokens / 30 days / no card — officially unconfirmed; the billing page after signup is the ground truth), so it sits ahead of OpenRouter `:free`, which is a 50-requests/day last-resort pool.

## Exact diff summary

- **`litellm_config.yaml`** (+15/−3): two new `model_list` entries (above); the three fallback lines extended with `"deepseek-chat", "openrouter-free"`. `num_retries: 1` and `cooldown_time: 60` untouched.
- **`compose.override.yml`** (+1): `- DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}` added to the **litellm** service environment, mirroring the existing GROQ/NVIDIA/OPENROUTER lines. This is the one file beyond the brief's list, and it is required: without the passthrough, a key typed into `.env` never reaches the gateway container and the DeepSeek route could never authenticate. (OPENROUTER_API_KEY was already passed through.)
- **`.env.example`** (+6/−1): `DEEPSEEK_API_KEY=` placeholder added with a one-line comment. `OPENROUTER_API_KEY=` **already existed** (declared for the app-side OpenRouter provider), so it was NOT duplicated — its comment now also notes the gateway role. No real keys anywhere.
- **`backend/tests/unit/test_litellm_gateway_config.py`** (new, 11 tests): first tests to parse the gateway config — see below.
- **`CHANGELOG.md`** (+1): a **New** entry under 2026-11-11 describing the optional fallbacks in plain language.

## Boot safety with keys absent — stated plainly

Both new routes reference their keys with the **identical `os.environ/<KEY>` pattern** the shipped config already uses for its optional-key routes (`or-ultra-free` → OPENROUTER_API_KEY; `clip-fast`/`nim-deepseek` → NVIDIA_API_KEY). Luffy's installed gateway boots and passes verify.ps1's LiteLLM model-list check (147/0, 2026-11-11) with that exact pattern in place, so the new entries change nothing about startup. Behaviour with a key unset/empty: the deployment loads; if a request ever falls all the way through to it, it fails authentication, the router marks it cooled down (60 s), and the request ends exactly as it would have before this change (all earlier routes already failed). Primary routes are unaffected — fallback entries only fire on failure of the entries before them, and the new ones are last.

Honest bound: this could not be live-boot-tested in the sandbox (no Docker, no litellm package here). The claim rests on the identical-pattern evidence above plus the structural tests. The first real boot proof is Luffy's next stack restart after the routes reach his machine (next package), keys or no keys.

## Tests

- `tests/unit/test_litellm_gateway_config.py` (11 tests) + neighbours `test_auto_director_classify_chain.py` + `test_auto_director_classify_battery.py` (they pin the app-side candidate chain that calls these gateway aliases): **32 passed / 0 failed** (serial run, OMP_NUM_THREADS=2, TMPDIR under workspace; log: `~/workspace/research_notes/free-api-radar/wiring-tests.log`). The new suite pins: the full model list exactly (existing six entries unchanged + the two new ones), every fallback chain exactly, free routes last in every chain, every fallback target exists, router retry/cooldown settings unchanged, the litellm compose passthrough list, and both gateway keys declared exactly once (empty) in `.env.example`.
- `frontend/src/lib/changelog.test.ts` after the CHANGELOG edit: **4 passed / 0 failed** (log: `~/workspace/research_notes/free-api-radar/changelog-test.log`; node_modules symlink borrowed from jcs-rc and removed after).

## Activation instructions for Luffy (when he wants them — each is optional and independent)

1. **Sign up** at platform.deepseek.com (DeepSeek) and/or openrouter.ai (OpenRouter) and create an API key. On DeepSeek, check the billing page — that's where the reported free grant (5M tokens / 30 days) will show if it's real. No card needed for either free route. Never paste a key into chat.
2. **Type the key into your own `.env`** (in `D:\EditedTool\JarvisClipStudio`): fill in `DEEPSEEK_API_KEY=` and/or `OPENROUTER_API_KEY=` — both lines will be there as empty placeholders once this ships in a package.
3. **Restart the stack**: `docker compose up -d` from `D:\EditedTool\JarvisClipStudio`. The gateway picks the keys up; the routes stay invisible unless every regular route fails first.

One extra line **only if activating on the current v3 install** (before any new package): his live `compose.override.yml` also needs `- DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}` under the `litellm:` service's environment list, or the DeepSeek key can't reach the gateway (OPENROUTER_API_KEY is already there). Future packages carry this in the template automatically.

## Notes / non-goals

- The app-side selectable alias list (`PERMITTED_ALIASES` in content_requirements) was deliberately NOT extended: these are gateway-router fallbacks, not user-selectable models, and provider-manager code was out of scope.
- FCC/NVIDIA NIM needed no wiring: NIM routes (`clip-fast`, `nim-deepseek`) already exist in the gateway.
- Merging into next-milestone and packaging are main's call; nothing here touches the installed v3 build.
