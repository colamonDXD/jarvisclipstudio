"""Pin the bundled LiteLLM gateway config (repo-root litellm_config.yaml).

The gateway is the app's LLM front door: the backend/worker call the
clip-fast / clip-assured / local-qwen aliases, and the router walks each
alias's fallback chain in order when a route fails. These tests pin the
shipped model list and chains so a config edit cannot silently drop a
route, reorder a chain, point a fallback at a model that does not
exist, or lose the env plumbing that delivers the keys.

The two newest routes are last-resort free fallbacks (2026-11-11):

- ``deepseek-chat`` — DeepSeek's official OpenAI-compatible API
  (``openai/deepseek-v4-pro`` via https://api.deepseek.com), keyed by
  DEEPSEEK_API_KEY. A new DeepSeek account reportedly carries a
  one-time free token grant, so this route can serve real traffic
  during the grant window and stays as a cheap route after it.
- ``openrouter-free`` — an OpenRouter ``:free`` model variant, keyed by
  the same OPENROUTER_API_KEY as the pre-existing ``or-ultra-free``
  route. The free tier is small (a last-resort pool, not a workhorse).

Multi-provider ladder (2026-11-12): the chains were restructured so
every route the app can call walks the same ladder instead of
dead-ending after one retry, and ``or-ultra-free`` no longer appears
inside chains: it is the identical deployment to ``openrouter-free``
(same model, same key), so a hop to it bought nothing. All routes
reference their keys with the same ``os.environ/<KEY>`` pattern:
with a key unset the deployment simply fails authentication if it is
ever reached and the router cools it down — the gateway still boots
and the primary routes are unaffected.

Brain ladder v2 (2026-11-12, later the same day): the ORDER inside
the chains changed from cost to strength. The first ladder walked
the free routes first and reached the DeepSeek direct route only
second-to-last; v2 ranks every chain strongest-first instead:
``deepseek-chat`` (DeepSeek direct, the full-size chat model) and
``clip-assured`` (Groq gpt-oss-120b) lead, then the NVIDIA NIM
routes (``clip-fast``, ``nim-deepseek``), then ``openrouter-free``,
then ``groq-20b``, and ``local-qwen`` (the local Ollama route) is
LAST in every chain — the emergency floor that cannot run out of
allowance. ``local-qwen``'s own chain is the single documented
exception: it is only consulted when the local route itself failed,
so it cannot contain ``local-qwen`` and walks the cloud routes
strongest-first.

Cloudflare hop (2026-11-13, the owner's Final Directive): a ninth
production route, ``cloudflare-free`` — Cloudflare Workers AI serving
``@cf/openai/gpt-oss-120b`` (the strongest model in Workers AI's free
allocation: 10,000 Neurons/day on the Workers Free plan) through
LiteLLM's native cloudflare provider, which builds the
OpenAI-compatible endpoint
``https://api.cloudflare.com/client/v4/accounts/<id>/ai/v1`` from
``CLOUDFLARE_ACCOUNT_ID`` at call time, so the config pins no
``api_base`` and carries no account-specific value. It is inserted
into every chain at the owner's specified position — after
``openrouter-free``, before ``groq-20b`` — one rung above the local
floor's neighbour, so a walk that has exhausted DeepSeek, both Groq
routes' provider, both NIM routes and OpenRouter's free pool still
meets a 120B brain on independent infrastructure before it degrades
to the 20B. The frontier Workers AI models (GLM-5.x, Kimi K2.6+,
DeepSeek V4) are NOT in the free allocation — they require a paid
plan — so the route's slug is pinned to the free-allocation model:
this route cannot select a paid model. The endpoint is
UNVERIFIED-LIVE (no Cloudflare key exists in the build sandbox; the
first live call happens on the user's machine).
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
GATEWAY_CONFIG = REPO_ROOT / "litellm_config.yaml"
COMPOSE_OVERRIDE = REPO_ROOT / "compose.override.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# The model list exactly as shipped before the free-fallback routes
# were appended: names, order, and litellm_params are all pinned so
# the new entries cannot disturb the existing routes.
EXISTING_MODELS = [
    {
        "model_name": "clip-fast",
        "litellm_params": {
            "model": "openai/nvidia/nemotron-3-ultra-550b-a55b",
            "api_base": "https://integrate.api.nvidia.com/v1",
            "api_key": "os.environ/NVIDIA_API_KEY",
        },
    },
    {
        "model_name": "or-ultra-free",
        "litellm_params": {
            "model": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
            "api_key": "os.environ/OPENROUTER_API_KEY",
        },
    },
    {
        "model_name": "nim-deepseek",
        "litellm_params": {
            # 2026-11-17: DeepSeek V4.1 Flash returns HTTP 410 (retired) on
            # the owner's live NIM probe; the rung moved to a verified model.
            "model": "openai/nvidia/nemotron-3.5-super-vl-120b-a12b",
            "api_base": "https://integrate.api.nvidia.com/v1",
            "api_key": "os.environ/NVIDIA_API_KEY",
        },
    },
    {
        "model_name": "clip-assured",
        "litellm_params": {
            "model": "groq/openai/gpt-oss-120b",
            "api_key": "os.environ/GROQ_API_KEY",
            # Brain upgrade (2026-11-11): the premium selection pass
            # sends reasoning_effort=high; the Groq routes allow-list it
            # so the gateway forwards it instead of rejecting the call.
            "allowed_openai_params": ["reasoning_effort"],
        },
    },
    {
        "model_name": "groq-20b",
        "litellm_params": {
            "model": "groq/openai/gpt-oss-20b",
            "api_key": "os.environ/GROQ_API_KEY",
            "allowed_openai_params": ["reasoning_effort"],
        },
    },
    {
        "model_name": "local-qwen",
        "litellm_params": {
            "model": "openai/qwen3:8b",
            "api_base": "http://host.docker.internal:11434/v1",
            "api_key": "ollama",
        },
    },
]

NEW_MODELS = [
    {
        "model_name": "deepseek-chat",
        "litellm_params": {
            "model": "openai/deepseek-v4-pro",
            "api_base": "https://api.deepseek.com",
            "api_key": "os.environ/DEEPSEEK_API_KEY",
        },
    },
    {
        "model_name": "openrouter-free",
        "litellm_params": {
            "model": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
            "api_key": "os.environ/OPENROUTER_API_KEY",
        },
    },
]

# Cloudflare Workers AI route (2026-11-13): see the module docstring.
# No api_base on purpose — the provider derives it from
# CLOUDFLARE_ACCOUNT_ID at call time; a pinned api_base would have to
# embed one user's account ID in the shipped config.
CLOUDFLARE_MODELS = [
    {
        "model_name": "cloudflare-free",
        "litellm_params": {
            "model": "cloudflare/@cf/openai/gpt-oss-120b",
            "api_key": "os.environ/CLOUDFLARE_API_KEY",
        },
    },
]

# NIM bake-off candidates (2026-11-12): pre-registered deployments
# competing for the clip-fast / nim-deepseek rungs (see
# tools/nim-bakeoff). NOT armed - nothing falls back to them and no
# app code calls them - so the shipped ladder is unchanged until a
# live bake-off justifies a one-line slug flip on the rung entries.
# The qwen / llama-4 / deepseek-pro slugs were not verifiable on a
# free-callable NIM listing offline; the bake-off protocol probes
# GET /v1/models first and drops any slug that 404s.
CANDIDATE_MODELS = [
    {
        "model_name": "nim-glm-flash",
        "litellm_params": {
            "model": "openai/z-ai/glm-5.3-flash",
            "api_base": "https://integrate.api.nvidia.com/v1",
            "api_key": "os.environ/NVIDIA_API_KEY",
        },
    },
    {
        "model_name": "nim-kimi-k2",
        "litellm_params": {
            "model": "openai/moonshotai/kimi-k2.6",
            "api_base": "https://integrate.api.nvidia.com/v1",
            "api_key": "os.environ/NVIDIA_API_KEY",
        },
    },
    {
        "model_name": "nim-mistral-large4",
        "litellm_params": {
            "model": "openai/mistralai/mistral-large-4-1t-a52b",
            "api_base": "https://integrate.api.nvidia.com/v1",
            "api_key": "os.environ/NVIDIA_API_KEY",
        },
    },
    {
        "model_name": "nim-qwen3-235b",
        "litellm_params": {
            "model": "openai/qwen/qwen3-235b-a22b",
            "api_base": "https://integrate.api.nvidia.com/v1",
            "api_key": "os.environ/NVIDIA_API_KEY",
        },
    },
    {
        "model_name": "nim-llama4-maverick",
        "litellm_params": {
            "model": "openai/meta/llama-4-maverick-17b-128e-instruct",
            "api_base": "https://integrate.api.nvidia.com/v1",
            "api_key": "os.environ/NVIDIA_API_KEY",
        },
    },
    {
        "model_name": "nim-deepseek-pro",
        "litellm_params": {
            "model": "openai/deepseek-ai/deepseek-v4-pro",
            "api_base": "https://integrate.api.nvidia.com/v1",
            "api_key": "os.environ/NVIDIA_API_KEY",
        },
    },
]

CANDIDATE_ALIASES = [entry["model_name"] for entry in CANDIDATE_MODELS]

# Brain ladder v2 (2026-11-12): the routes ranked strongest ->
# weakest. DeepSeek direct leads (the full-size chat model on its own
# endpoint, not behind a shared free-tier pool), Groq gpt-oss-120b is
# the near-equal second, then the NVIDIA NIM routes (the Nemotron
# Ultra flagship before the DeepSeek Flash route), then the same
# Nemotron Ultra via OpenRouter's shared free pool, then the small
# Groq model, and the local Ollama route last — the emergency floor.
# Every chain is exactly this ranking with the chain's own route
# removed. local-qwen's chain is the documented exception only in
# that it cannot contain local-qwen (see module docstring).
# or-ultra-free stays in the model list but appears in no chain: it
# is the identical deployment to openrouter-free. cloudflare-free
# (2026-11-13) sits at the owner's specified position — after
# openrouter-free, before groq-20b — which is also its strength
# position (a 120B model above the 20B).
STRENGTH_ORDER = [
    "deepseek-chat",
    "clip-assured",
    "clip-fast",
    "nim-deepseek",
    "openrouter-free",
    "cloudflare-free",
    "groq-20b",
    "local-qwen",
]

EXPECTED_FALLBACKS = {
    "clip-fast": [
        "deepseek-chat",
        "clip-assured",
        "nim-deepseek",
        "openrouter-free",
        "cloudflare-free",
        "groq-20b",
        "local-qwen",
    ],
    "clip-assured": [
        "deepseek-chat",
        "clip-fast",
        "nim-deepseek",
        "openrouter-free",
        "cloudflare-free",
        "groq-20b",
        "local-qwen",
    ],
    "groq-20b": [
        "deepseek-chat",
        "clip-assured",
        "clip-fast",
        "nim-deepseek",
        "openrouter-free",
        "cloudflare-free",
        "local-qwen",
    ],
    "local-qwen": [
        "deepseek-chat",
        "clip-assured",
        "clip-fast",
        "nim-deepseek",
        "openrouter-free",
        "cloudflare-free",
        "groq-20b",
    ],
    "or-ultra-free": [
        "deepseek-chat",
        "clip-assured",
        "clip-fast",
        "nim-deepseek",
        "cloudflare-free",
        "groq-20b",
        "local-qwen",
    ],
    "nim-deepseek": [
        "deepseek-chat",
        "clip-assured",
        "clip-fast",
        "openrouter-free",
        "cloudflare-free",
        "groq-20b",
        "local-qwen",
    ],
    "deepseek-chat": [
        "clip-assured",
        "clip-fast",
        "nim-deepseek",
        "openrouter-free",
        "cloudflare-free",
        "groq-20b",
        "local-qwen",
    ],
    "openrouter-free": [
        "deepseek-chat",
        "clip-assured",
        "clip-fast",
        "nim-deepseek",
        "cloudflare-free",
        "groq-20b",
        "local-qwen",
    ],
    "cloudflare-free": [
        "deepseek-chat",
        "clip-assured",
        "clip-fast",
        "nim-deepseek",
        "openrouter-free",
        "groq-20b",
        "local-qwen",
    ],
}

# Each bake-off candidate, if ever called directly, walks the FULL
# production ladder (it is not itself in the strength ranking, so
# nothing is removed). No production chain lists a candidate as a
# target - that is what keeps the candidates un-armed.
EXPECTED_FALLBACKS.update(
    {alias: list(STRENGTH_ORDER) for alias in CANDIDATE_ALIASES}
)

# Aliases the app code itself requests through the gateway
# (ANALYSIS_MODELS / LLM / ANALYSIS_FALLBACK_MODEL / LOCAL_LLM and the
# content-requirements PERMITTED_ALIASES). The remaining model-list
# entries exist only as fallback targets.
CODE_CALLED_ALIASES = {"clip-fast", "clip-assured", "groq-20b", "local-qwen"}


def _load_gateway_config() -> dict:
    return yaml.safe_load(GATEWAY_CONFIG.read_text(encoding="utf-8"))


def _fallback_map(config: dict) -> dict[str, list[str]]:
    chains: dict[str, list[str]] = {}
    for entry in config["router_settings"]["fallbacks"]:
        chains.update(entry)
    return chains


class TestGatewayModelList:
    def test_full_model_list_is_exact(self):
        config = _load_gateway_config()
        assert config["model_list"] == (
            EXISTING_MODELS + NEW_MODELS + CLOUDFLARE_MODELS + CANDIDATE_MODELS
        )

    def test_existing_models_unchanged(self):
        config = _load_gateway_config()
        assert config["model_list"][: len(EXISTING_MODELS)] == EXISTING_MODELS

    def test_model_names_unique(self):
        config = _load_gateway_config()
        names = [entry["model_name"] for entry in config["model_list"]]
        assert len(names) == len(set(names))

    def test_deepseek_chat_route(self):
        config = _load_gateway_config()
        by_name = {e["model_name"]: e for e in config["model_list"]}
        assert by_name["deepseek-chat"] == NEW_MODELS[0]

    def test_openrouter_free_route(self):
        config = _load_gateway_config()
        by_name = {e["model_name"]: e for e in config["model_list"]}
        assert by_name["openrouter-free"] == NEW_MODELS[1]


class TestCloudflareRoute:
    def test_cloudflare_free_route_is_exact(self):
        config = _load_gateway_config()
        by_name = {e["model_name"]: e for e in config["model_list"]}
        assert by_name["cloudflare-free"] == CLOUDFLARE_MODELS[0]

    def test_cloudflare_slug_is_the_free_allocation_model(self):
        # The never-paid pin for this route: the slug must stay the
        # free-allocation gpt-oss-120b. Workers AI's frontier rows
        # (GLM-5.x, Kimi K2.6+, DeepSeek V4) are paid-plan only, so
        # none of their slug fragments may ever appear here — a
        # "stronger" slug swap would silently arm paid spend the
        # moment the account had a paid plan attached.
        slug = CLOUDFLARE_MODELS[0]["litellm_params"]["model"]
        assert slug == "cloudflare/@cf/openai/gpt-oss-120b"
        lowered = slug.lower()
        for paid_fragment in ("glm-5", "kimi", "deepseek-v4", "deepseek-ai"):
            assert paid_fragment not in lowered

    def test_cloudflare_route_pins_no_api_base(self):
        # The provider builds the endpoint from CLOUDFLARE_ACCOUNT_ID
        # at call time; a pinned api_base would embed one account's
        # ID in the shipped config.
        params = CLOUDFLARE_MODELS[0]["litellm_params"]
        assert "api_base" not in params
        assert params["api_key"] == "os.environ/CLOUDFLARE_API_KEY"

    def test_cloudflare_position_in_the_ranking(self):
        # The owner's specified slot: directly after openrouter-free,
        # before groq-20b, with only groq-20b between it and the
        # local floor.
        assert STRENGTH_ORDER.index("cloudflare-free") == (
            STRENGTH_ORDER.index("openrouter-free") + 1
        )
        assert STRENGTH_ORDER.index("cloudflare-free") < STRENGTH_ORDER.index(
            "groq-20b"
        )

    def test_cloudflare_position_inside_every_chain(self):
        chains = _fallback_map(_load_gateway_config())
        for source, chain in chains.items():
            if "cloudflare-free" not in chain:
                # Only cloudflare-free's own chain omits it.
                assert source == "cloudflare-free"
                continue
            if "openrouter-free" in chain:
                assert chain.index("openrouter-free") < chain.index(
                    "cloudflare-free"
                ), source
            if "groq-20b" in chain:
                assert chain.index("cloudflare-free") < chain.index(
                    "groq-20b"
                ), source

    def test_app_code_never_calls_cloudflare_directly(self):
        # Like the other fallback-only routes, cloudflare-free is a
        # walk target, not an entry point: no app setting names it.
        assert "cloudflare-free" not in CODE_CALLED_ALIASES

    def test_no_app_or_ui_surface_knows_the_route_exists(self):
        # The unavailable-Cloudflare honesty pin: the route lives
        # ONLY in the gateway config (plus these tests and the
        # routing docs). No backend or frontend source mentions
        # Cloudflare at all, so no settings page, health check or
        # status surface can report Cloudflare as configured or
        # healthy — with its keys missing the hop simply fails and
        # cools down inside the gateway, exactly like a sibling hop
        # whose key is unset, and nothing anywhere claims otherwise.
        for tree in (REPO_ROOT / "backend" / "src", REPO_ROOT / "frontend" / "src"):
            for path in tree.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                text = path.read_bytes().decode("utf-8", errors="ignore")
                assert "cloudflare" not in text.lower(), path


class TestBakeoffCandidates:
    def test_candidate_models_are_exact(self):
        config = _load_gateway_config()
        by_name = {e["model_name"]: e for e in config["model_list"]}
        for entry in CANDIDATE_MODELS:
            assert by_name[entry["model_name"]] == entry

    def test_candidates_are_all_nim_hosted(self):
        for entry in CANDIDATE_MODELS:
            params = entry["litellm_params"]
            assert params["api_base"] == "https://integrate.api.nvidia.com/v1"
            assert params["api_key"] == "os.environ/NVIDIA_API_KEY"
            assert params["model"].startswith("openai/")

    def test_no_production_chain_falls_back_to_a_candidate(self):
        # The candidates are pre-registered, not armed: every chain
        # that shipped before the bake-off must be candidate-free.
        chains = _fallback_map(_load_gateway_config())
        production = set(STRENGTH_ORDER) | {"or-ultra-free"}
        for source in production:
            assert not (set(chains[source]) & set(CANDIDATE_ALIASES)), source

    def test_candidate_chains_walk_the_full_ladder(self):
        chains = _fallback_map(_load_gateway_config())
        for alias in CANDIDATE_ALIASES:
            assert chains[alias] == STRENGTH_ORDER

    def test_app_code_never_calls_a_candidate(self):
        assert not (CODE_CALLED_ALIASES & set(CANDIDATE_ALIASES))


class TestGatewayFallbacks:
    def test_fallback_chains_are_exact(self):
        config = _load_gateway_config()
        assert _fallback_map(config) == EXPECTED_FALLBACKS

    def test_every_alias_has_a_chain_and_no_self_fallback(self):
        config = _load_gateway_config()
        chains = _fallback_map(config)
        names = {entry["model_name"] for entry in config["model_list"]}
        assert set(chains) == names
        assert CODE_CALLED_ALIASES <= set(chains)
        for source, chain in chains.items():
            assert source not in chain

    def test_chains_end_at_the_local_route(self):
        # The ladder's final resort is the local Ollama route: a chain
        # must never dead-end on a remote provider. local-qwen's own
        # chain is the documented exception — it is only consulted
        # when the local route itself failed, so it cannot contain
        # local-qwen and walks the cloud ladder instead.
        chains = _fallback_map(_load_gateway_config())
        for source, chain in chains.items():
            if source == "local-qwen":
                assert "local-qwen" not in chain
            else:
                assert chain[-1] == "local-qwen"

    def test_chains_are_the_strength_ranking_minus_the_source(self):
        # Brain ladder v2: every chain is exactly the global strength
        # ranking with the chain's own route removed, so a failed
        # route always falls to the next strongest brain — never
        # sideways to a weaker free route while a stronger one is
        # still untried (the v1 cost ladder did the opposite).
        # or-ultra-free shares openrouter-free's deployment (same
        # model, same key), so its own route for this rule is
        # openrouter-free: a hop between the twins would buy nothing.
        chains = _fallback_map(_load_gateway_config())
        own_route = {"or-ultra-free": "openrouter-free"}
        for source, chain in chains.items():
            own = own_route.get(source, source)
            assert chain == [name for name in STRENGTH_ORDER if name != own]

    def test_deepseek_leads_every_chain_it_is_in(self):
        chains = _fallback_map(_load_gateway_config())
        for source, chain in chains.items():
            if source == "deepseek-chat":
                # Its own chain starts at the near-equal second.
                assert chain[0] == "clip-assured"
            else:
                assert chain[0] == "deepseek-chat"

    def test_no_gemini_anywhere_in_gateway(self):
        config = _load_gateway_config()
        for entry in config["model_list"]:
            model = entry["litellm_params"]["model"].lower()
            assert "gemini" not in model
            assert not model.startswith("google")
        raw = GATEWAY_CONFIG.read_text(encoding="utf-8").lower()
        assert "gemini" not in raw

    def test_every_fallback_target_exists(self):
        config = _load_gateway_config()
        names = {entry["model_name"] for entry in config["model_list"]}
        for source, chain in _fallback_map(config).items():
            assert source in names
            for target in chain:
                assert target in names

    def test_router_settings_unchanged(self):
        config = _load_gateway_config()
        assert config["router_settings"]["num_retries"] == 1
        assert config["router_settings"]["cooldown_time"] == 60


class TestKeyPlumbing:
    def test_compose_passes_all_gateway_keys(self):
        compose = yaml.safe_load(COMPOSE_OVERRIDE.read_text(encoding="utf-8"))
        environment = compose["services"]["litellm"]["environment"]
        names = [entry.split("=", 1)[0] for entry in environment]
        assert names == [
            "GROQ_API_KEY",
            "NVIDIA_API_KEY",
            "OPENROUTER_API_KEY",
            "DEEPSEEK_API_KEY",
            "CLOUDFLARE_API_KEY",
            "CLOUDFLARE_ACCOUNT_ID",
            "LITELLM_MASTER_KEY",
        ]
        assert "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}" in environment
        # The Cloudflare route needs BOTH values in the gateway
        # container: the token authenticates and the account ID
        # builds the endpoint. Either one missing makes the hop
        # fail (and cool down) exactly like an unset sibling key.
        assert "CLOUDFLARE_API_KEY=${CLOUDFLARE_API_KEY}" in environment
        assert "CLOUDFLARE_ACCOUNT_ID=${CLOUDFLARE_ACCOUNT_ID}" in environment

    def test_env_example_declares_gateway_keys_once(self):
        lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        for key in (
            "DEEPSEEK_API_KEY",
            "OPENROUTER_API_KEY",
            "CLOUDFLARE_API_KEY",
            "CLOUDFLARE_ACCOUNT_ID",
        ):
            declarations = [
                line for line in lines if line.startswith(f"{key}=")
            ]
            assert declarations == [f"{key}="]
