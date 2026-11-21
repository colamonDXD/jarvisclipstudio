"""Pin the feature-flag env passthroughs in the shipped docker-compose.yml.

The containers receive ONLY the variables listed in the compose files
(there is no env_file:), so a flag the code reads via os.environ is
dead on an installed build unless docker-compose.yml passes it through
for every service whose process reads it. This suite exists because
two reframe flags shipped without passthroughs (found by the v4
packaging pass, 2026-11-12):

- ``ACTIVE_SPEAKER_TARGETING_ENABLED`` — read by
  ``media.active_speaker.active_speaker_enabled()`` (opt-in, default
  OFF); without the line the feature could not be enabled at all.
- ``BORDER_TRIM_ENABLED`` — read by
  ``media.border_trim.border_trim_enabled()`` (default ON, env var is
  the kill switch); without the line the kill switch was unreachable.

Both readers are consulted from ``media.reframe_plan``, which runs in
the backend and worker processes — the same two services that carry
the established ``SEPARATION_ENABLED`` / ``BROLL_VISION_VETO_ENABLED``
passthroughs pinned alongside them here. The ``:-`` fallbacks equal
the code defaults, so a silent .env leaves behaviour unchanged —
with ONE deliberate exception: the veto's compose fallback is
``true`` since Slice 4 (0106ca2, calibrated on 44 real stock videos
/ 220 scored pairs) while the bare code default in
``media.broll_vision.vision_veto_enabled`` stays fail-safe OFF for
non-compose runs. In a compose deployment the container always
receives the variable, so the compose fallback IS the deployment
default; the env var still turns the veto off per deployment, and
Plain is structurally exempt.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"

# Services whose processes run the reframe pipeline.
PIPELINE_SERVICES = ("backend", "worker")

# Exact environment lines, as shipped.
EXPECTED_LINES = {
    "SEPARATION_ENABLED": "SEPARATION_ENABLED=${SEPARATION_ENABLED:-false}",
    # Slice 4 (0106ca2) flipped the compose default false -> true:
    # calibration supports the veto ON by default for Auto template
    # mode; env still turns it off, Plain is structurally exempt.
    "BROLL_VISION_VETO_ENABLED": (
        "BROLL_VISION_VETO_ENABLED=${BROLL_VISION_VETO_ENABLED:-true}"
    ),
    "ACTIVE_SPEAKER_TARGETING_ENABLED": (
        "ACTIVE_SPEAKER_TARGETING_ENABLED="
        "${ACTIVE_SPEAKER_TARGETING_ENABLED:-false}"
    ),
    # Code default is ON: the fallback must be true, or a silent .env
    # would silently disable border trim.
    "BORDER_TRIM_ENABLED": "BORDER_TRIM_ENABLED=${BORDER_TRIM_ENABLED:-true}",
}


def _environment(service: str) -> list:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return compose["services"][service]["environment"]


class TestFlagPassthroughs:
    def test_flags_passed_to_pipeline_services(self):
        for service in PIPELINE_SERVICES:
            environment = _environment(service)
            for flag, line in EXPECTED_LINES.items():
                assert line in environment, (
                    f"{service} is missing the {flag} passthrough "
                    f"(expected exact line: {line!r})"
                )

    def test_each_flag_appears_exactly_once_per_service(self):
        for service in PIPELINE_SERVICES:
            environment = _environment(service)
            for flag in EXPECTED_LINES:
                hits = [e for e in environment if e.startswith(flag + "=")]
                assert len(hits) == 1, (
                    f"{service} carries {flag} {len(hits)} times: {hits}"
                )
