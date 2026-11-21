"""Offline tests for the NIM bake-off harness.

Everything runs against the in-harness fake OpenAI-compatible server
(canned responders with known behaviour), so the whole scoring path -
HTTP client, real app prompt builders, real parsers/validators, the
gold-set scorer, the plan rubric, aggregation and table rendering -
is exercised without any live model or API key.

Run with the backend venv python from the repo root:
    backend/.venv/bin/python -m pytest tools/nim-bakeoff/tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent
REPO = HARNESS.parents[1]
for _p in (str(HARNESS), str(REPO / "backend"), str(REPO / "tools" / "selection-eval")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import bakeoff  # noqa: E402


# --- Pure helpers -----------------------------------------------------------


class TestExtractJson:
    def test_plain_object(self):
        obj, err = bakeoff.extract_json('{"a": 1}')
        assert obj == {"a": 1} and err is None

    def test_fenced_object(self):
        obj, err = bakeoff.extract_json('```json\n{"a": 1}\n```')
        assert obj == {"a": 1} and err is None

    def test_prose_wrapped_object(self):
        obj, err = bakeoff.extract_json('Here you go: {"a": {"b": [1, 2]}} done.')
        assert obj == {"a": {"b": [1, 2]}} and err is None

    def test_braces_inside_strings(self):
        obj, err = bakeoff.extract_json('{"a": "}{"}')
        assert obj == {"a": "}{"} and err is None

    def test_garbage(self):
        obj, err = bakeoff.extract_json("Sorry, no JSON here.")
        assert obj is None and err

    def test_non_object_json(self):
        obj, err = bakeoff.extract_json("[1, 2, 3]")
        assert obj is None and err


class TestTitleViolations:
    def test_clean_title(self):
        assert bakeoff.title_violations("That Avocado Came From a 700-Year-Old Tree") == []

    def test_first_person(self):
        assert "first-person wording" in bakeoff.title_violations("I Tried A Weird Trick")

    def test_too_short(self):
        assert any("word count" in p for p in bakeoff.title_violations("Big Reveal"))

    def test_hashtag_and_quotes(self):
        problems = bakeoff.title_violations('"Chef Cooks #Live On Stage"')
        assert "hashtag" in problems and "surrounding quotes" in problems

    def test_missing(self):
        assert bakeoff.title_violations(None) == ["missing title"]
        assert bakeoff.title_violations("") == ["missing title"]


def _plan_dict(**overrides):
    plan = {
        "hook": {"type": "reveal", "timestamp": 0.0, "confidence": 0.9, "reason": "r"},
        "opening_treatment": {
            "punch_zoom": "subtle", "first_caption_emphasis": True,
            "sfx_accent": None, "at_offset": 0.0, "reason": "r",
        },
        "transition_choices": [],
        "caption_style": None,
        "phases": [{"start_offset": 0.0, "end_offset": 20.0, "intent": "hook"}],
        "sfx_events": [],
        "music_decision": {"use_profile_music": True, "reason": "fits"},
        "plan_confidence": 0.85,
    }
    plan.update(overrides)
    return plan


class TestPlanRubric:
    def test_clean_plan_scores_100(self):
        plan = _plan_dict()
        result = bakeoff.plan_rubric(plan, plan, duration=20.0)
        assert result["total"] == 100.0
        assert result["dropped_effects"] == 0

    def test_no_hook_and_half_phases(self):
        plan = _plan_dict(
            hook={"type": "none_strong", "timestamp": 0.0, "confidence": 0.4, "reason": ""},
            phases=[{"start_offset": 0.0, "end_offset": 10.0, "intent": "hook"}],
        )
        result = bakeoff.plan_rubric(plan, plan, duration=20.0)
        assert result["parts"]["hook"] == 5.0  # timestamp in range only
        assert result["parts"]["phases"] == 10.0

    def test_dropped_effects_cost_restraint(self):
        draft = _plan_dict(
            sfx_events=[{"name": "pop", "at_offset": 9.0, "reason": "x"}],
            transition_choices=[{"name": "fade", "at_offset": 5.0, "reason": "x"}],
        )
        kept = _plan_dict()  # validator dropped both effects
        result = bakeoff.plan_rubric(draft, kept, duration=20.0)
        assert result["dropped_effects"] == 2
        assert result["parts"]["restraint"] == 15.0

    def test_clustered_sfx_costs_placement(self):
        plan = _plan_dict(
            sfx_events=[
                {"name": "pop", "at_offset": 5.0, "reason": "x"},
                {"name": "ding", "at_offset": 5.3, "reason": "x"},
                {"name": "whoosh", "at_offset": 0.1, "reason": "x"},
            ]
        )
        result = bakeoff.plan_rubric(plan, plan, duration=20.0)
        # -5 for the 0.3s pair, -5 for the 0.1s edge event
        assert result["parts"]["sfx_placement"] == 5.0


# --- Evidence + fixtures -----------------------------------------------------


class TestEvidence:
    def test_provisional_ranking_follows_published_scores(self):
        ev = bakeoff.load_evidence()
        ranked = [r for r in ev["provisional_ranking"] if r["rank"] is not None]
        by_slug = {c["slug"]: c for c in ev["candidates"]}
        scores = [by_slug[r["slug"]]["aa_index"] for r in ranked]
        assert scores == sorted(scores, reverse=True)
        assert all(by_slug[r["slug"]]["slug_status"] == "verified" for r in ranked)

    def test_incumbents_are_the_shipped_slugs(self):
        ev = bakeoff.load_evidence()
        by_alias = {c["alias"]: c["slug"] for c in ev["candidates"]}
        assert by_alias["clip-fast"] == "nvidia/nemotron-3-ultra-550b-a55b"
        assert by_alias["nim-deepseek"] == "deepseek-ai/deepseek-v4.1-flash"


class TestFixtures:
    def test_fixtures_load_and_cover_golds(self):
        fixtures = bakeoff.load_fixtures()
        by_id = {f["id"]: f for f in fixtures}
        assert set(by_id) == {
            "japan_selection", "tjr_selection", "japan_plan", "tjr_plan",
            "classify_cases", "requirements_focus", "tjr_longform_selection",
        }
        assert by_id["japan_selection"]["gold_ids_covered"] == ["g1", "g3", "g4"]
        cases = {c["id"]: c for c in by_id["classify_cases"]["cases"]}
        assert cases["tjr-week-in-life"]["expected"] == "vlog_lifestyle"
        assert cases["tjr-10m-trader"]["expected"] == "finance_business"
        assert cases["japan-restaurants"]["guardrail"] is False
        assert cases["podcast-control"]["expected"] == "podcast_news"

    def test_requirements_focus_fixture_is_derived(self):
        fixture = next(
            f for f in bakeoff.load_fixtures() if f["id"] == "requirements_focus"
        )
        assert fixture["kind"] == "requirements"
        assert fixture["permitted_aliases"] == ["clip-assured", "groq-20b", "local-qwen"]
        reqs = fixture["brief"]["requirements"]
        assert [r["id"] for r in reqs] == ["r1"]
        assert reqs[0]["type"] == "topic_focus" and reqs[0]["requires_major"] is True
        assert fixture["expected"] == {
            "0": {"r1": {"focus": "major", "status": "satisfied"}},
            "1": {"r1": {"focus": "absent", "status": "failed"}},
        }
        texts = {c["candidate_id"]: c["text"] for c in fixture["candidates"]}
        assert "Expedia" in texts[0] and "Expedia" not in texts[1]

    def test_longform_fixture_is_the_longest_real_transcript(self):
        fixture = next(
            f for f in bakeoff.load_fixtures() if f["id"] == "tjr_longform_selection"
        )
        assert fixture["kind"] == "selection"
        assert fixture["gold_ids_covered"] == []  # unlabelled: sanity only
        assert fixture["word_count"] == 4610
        assert len(fixture["transcript"]) > 20000  # ~14x the japan_selection input
        assert len(fixture["transcript"].splitlines()) == 109
        assert "150 wpm" in fixture["timestamp_basis"]
        assert fixture["estimated_duration_sec"] == 1844.0
        # It must be strictly longer than the core selection fixtures.
        japan = next(
            f for f in bakeoff.load_fixtures() if f["id"] == "japan_selection"
        )
        assert len(fixture["transcript"]) > 10 * len(japan["transcript"])


# --- End-to-end against the fake server --------------------------------------


@pytest.fixture(scope="module")
def fake_results():
    with bakeoff.fake_server() as base_url:
        return bakeoff.run_bakeoff(
            models=["fake-good", "fake-mid", "fake-bad"],
            base_url=base_url,
            api_key="fake",
            retry_delay=0.0,
        )


class TestFakeEndToEnd:
    def test_good_model_is_perfect_on_gold(self, fake_results):
        agg = fake_results["fake-good"]["aggregate"]
        assert agg["japan"]["recall"] == 1.0
        assert agg["japan"]["mean_overlap"] == 1.0
        assert agg["japan"]["traps_taken"] == 0
        assert agg["japan"]["duplicate_picks"] == 0

    def test_good_model_validity_and_niche(self, fake_results):
        agg = fake_results["fake-good"]["aggregate"]
        assert agg["validity_rate"] == 1.0
        assert agg["calls"] == 10  # 8 core + requirements + long-form
        assert agg["niche_raw"] == "4/4"
        assert agg["niche_corrected"] == "3/3"
        # Japan 3 + TJR 1 + long-form 1 hook titles, none violated.
        assert agg["titles"] == {"checked": 5, "violations": 0}

    def test_good_model_focus_agreement(self, fake_results):
        agg = fake_results["fake-good"]["aggregate"]
        assert agg["focus_agreement"] == "2/2"
        assert agg["focus_accepted"] == "2/2"
        req = next(
            r for r in fake_results["fake-good"]["requests"]
            if r.get("workload") == "requirements"
        )
        assert req["valid"] is True
        assert req["focus"]["discarded"] == 0
        assert req["focus"]["missing"] == 0

    def test_mid_model_invented_evidence_is_discarded(self, fake_results):
        req = next(
            r for r in fake_results["fake-mid"]["requests"]
            if r.get("workload") == "requirements"
        )
        assert req["valid"] is True  # schema-valid JSON, wrong judgements
        assert req["focus"]["returned"] == 2
        assert req["focus"]["accepted"] == 0
        assert req["focus"]["discarded"] == 2
        assert req["focus"]["agreed"] == 0
        assert fake_results["fake-mid"]["aggregate"]["focus_agreement"] == "0/2"

    def test_longform_fixture_discriminates(self, fake_results):
        good = next(
            r for r in fake_results["fake-good"]["requests"]
            if r.get("fixture") == "tjr_longform_selection"
        )
        mid = next(
            r for r in fake_results["fake-mid"]["requests"]
            if r.get("fixture") == "tjr_longform_selection"
        )
        assert good["valid"] is True and good["grounded"] == 1
        assert good["out_of_bounds"] == 0
        assert good["titles"]["violations"] == 0
        assert mid["valid"] is True
        assert mid["grounded"] == 0  # unanchored pick off the long transcript
        assert mid["titles"]["violations"] >= 1  # first-person title

    def test_mid_model_defects_are_measured(self, fake_results):
        agg = fake_results["fake-mid"]["aggregate"]
        assert agg["japan"]["recall"] < 1.0
        assert agg["japan"]["traps_taken"] >= 1
        assert agg["titles"]["violations"] >= 1
        assert agg["niche_raw"] == "1/4"
        # The production guardrails rescue the mid model's podcast bias.
        assert agg["niche_corrected"] == "3/3"

    def test_bad_model_scores_zero(self, fake_results):
        agg = fake_results["fake-bad"]["aggregate"]
        assert agg["validity_rate"] == 0.0
        assert agg["japan"] is None
        assert agg["plan_rubric_mean"] is None

    def test_plan_rubric_discriminates(self, fake_results):
        good = fake_results["fake-good"]["aggregate"]["plan_rubric_mean"]
        mid = fake_results["fake-mid"]["aggregate"]["plan_rubric_mean"]
        assert good >= 90.0
        assert mid <= 50.0
        assert good > mid + 40.0

    def test_table_renders_all_models(self, fake_results):
        table = bakeoff.render_table(fake_results)
        for model in ("fake-good", "fake-mid", "fake-bad"):
            assert f"`{model}`" in table
        assert "Japan mean overlap" in table
        assert "Niche +guardrails" in table
        assert "Focus agree" in table
        assert "2/2" in table

    def test_report_renders_with_results(self, fake_results):
        report = bakeoff.render_report(fake_results)
        assert "Live-run protocol" in report
        assert "fake-good" in report
        assert "PROVISIONAL" in report


# --- Fixture A: premium selection with reasoning_effort=high --------------


@pytest.fixture(scope="module")
def premium_results():
    fixtures = bakeoff.load_fixtures()
    selection = [f for f in fixtures if f["id"] in ("japan_selection", "tjr_selection")]
    with bakeoff.fake_server() as base_url:
        return bakeoff.run_bakeoff(
            models=["fake-good", "fake-accepts-reasoning", "fake-rejects-reasoning"],
            base_url=base_url,
            api_key="fake",
            fixtures=selection,
            reasoning_effort="high",
            retry_delay=0.0,
        )


class TestReasoningEffort:
    def test_parameter_state_is_recorded_per_call(self, premium_results):
        for model, expected_state in (
            ("fake-good", "ignored"),
            ("fake-accepts-reasoning", "accepted"),
            ("fake-rejects-reasoning", "rejected"),
        ):
            states = {
                r.get("reasoning_effort_state")
                for r in premium_results[model]["requests"]
            }
            assert states == {expected_state}, model

    def test_rejection_is_a_failure_not_a_downgrade(self, premium_results):
        rejected = premium_results["fake-rejects-reasoning"]["requests"]
        assert len(rejected) == 2  # both selection fixtures attempted
        for req in rejected:
            assert req["valid"] is False
            assert req["attempts"] == 1  # no retry, no re-send without the parameter
            assert "400" in req["error"]
        agg = premium_results["fake-rejects-reasoning"]["aggregate"]
        assert agg["reasoning_effort"] == {"rejected": 2}
        assert agg["schema_valid"] == 0

    def test_accepted_and_ignored_still_grade(self, premium_results):
        assert premium_results["fake-good"]["aggregate"]["japan"]["recall"] == 1.0
        assert premium_results["fake-accepts-reasoning"]["aggregate"]["japan"]["recall"] == 1.0
        assert premium_results["fake-accepts-reasoning"]["aggregate"]["reasoning_effort"] == {"accepted": 2}
        assert premium_results["fake-good"]["aggregate"]["reasoning_effort"] == {"ignored": 2}

    def test_premium_table_shows_reasoning_column(self, premium_results):
        table = bakeoff.render_table(premium_results)
        assert "Reasoning effort" in table
        assert "accepted:2" in table
        assert "rejected:2" in table

    def test_fixture_filtering_selects_one_family(self):
        fixtures = bakeoff.load_fixtures()
        app = bakeoff._load_app()
        requests = bakeoff.build_requests(
            app, [f for f in fixtures if f["id"] == "requirements_focus"]
        )
        assert len(requests) == 1
        assert requests[0]["workload"] == "requirements"
        assert "campaign content requirements" in requests[0]["system"]
