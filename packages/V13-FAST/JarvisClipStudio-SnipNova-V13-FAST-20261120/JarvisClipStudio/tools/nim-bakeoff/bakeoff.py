#!/usr/bin/env python3
"""NIM model bake-off harness for SnipNova's brain ladder.

Question: which NIM-hosted model should hold each NVIDIA rung of the
ladder (``clip-fast`` = Nemotron 3 Ultra today, ``nim-deepseek`` =
DeepSeek V4.1 Flash today) for THIS app's workloads?

Method - no hand-copied prompts, no invented grading:

- Every prompt is built by the app's own builders
  (``src.ai.build_transcript_system_prompt`` /
  ``build_transcript_analysis_prompt`` for selection,
  ``src.auto_director.plan_service.PLAN_SYSTEM_PROMPT`` /
  ``_build_plan_prompt`` for planning,
  ``src.auto_director.classify.NICHE_CLASSIFICATION_SYSTEM_PROMPT`` /
  ``_build_niche_classification_prompt`` for niche classification).
- Every response is parsed and validated with the app's own machinery
  (``TranscriptAnalysis`` / ``ClipPlanDraft`` pydantic schemas,
  ``_parse_json_text_classification``, ``validate_plan_dict`` and the
  real ``apply_evidence_guardrails`` for the corrected niche score).
- Selection agreement is graded by the existing gold-set scorer
  (``tools/selection-eval/score_selection.py``) against the labelled
  windows - restricted to the golds with real transcript evidence
  in-tree (g1/g3/g4; see fixtures and build_fixtures.py).
- Plan quality uses a deterministic rubric (``plan_rubric`` below),
  computed on the validator-surviving plan vs the raw draft.

Validity definition: a call is "schema-valid" when the response text
yields a JSON object that validates against the production pydantic
schema in ONE shot. Production additionally gets pydantic-ai output
retries, so this rate is a lower bound - identical conditions for
every model is what makes it a fair comparison.

Config (env):
  BAKEOFF_BASE_URL   default https://integrate.api.nvidia.com/v1
  BAKEOFF_API_KEY    required for live runs (never commit it)
  BAKEOFF_MODELS     comma-separated slugs (default: evidence list)
  BAKEOFF_TIMEOUT    per-call seconds (default 300)
  BAKEOFF_TEMPERATURE default 0
  BAKEOFF_REASONING_EFFORT  none (default) or high: with high, only the
    two selection fixtures run, each call carrying
    reasoning_effort=high; per call the harness records whether the
    parameter was accepted, ignored, or rejected (4xx). A rejection is
    a FAILED call, never a silent downgrade to temperature 0.

Usage:
  python3 bakeoff.py --offline [--report PATH]   # evidence report only
  python3 bakeoff.py --self-check                # fake-server end-to-end
  BAKEOFF_API_KEY=... python3 bakeoff.py --out results/run1
  BAKEOFF_API_KEY=... python3 bakeoff.py --reasoning-effort high --out results/premium1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "backend"
SELECTION_EVAL = REPO / "tools" / "selection-eval"
FIXTURES_DIR = HERE / "fixtures"
EVIDENCE_PATH = HERE / "offline_evidence.json"
DEFAULT_REPORT = REPO.parent / "research_notes" / "sprint-7day" / "NIM_BAKEOFF.md"

for _p in (str(BACKEND), str(SELECTION_EVAL)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Pure helpers (no app imports - unit-testable in isolation)
# ---------------------------------------------------------------------------

_FIRST_PERSON = re.compile(
    r"\b(i|i'm|i've|i'll|i'd|my|mine|we|we're|we've|we'll|our|ours)\b",
    re.IGNORECASE,
)
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff☀-➿⬀-⯿]"
)


def extract_json(text: str) -> tuple[Optional[dict], Optional[str]]:
    """Best-effort JSON-object extraction from a model response.

    Strips code fences, then tries a direct parse, then a balanced
    brace scan. Returns (obj, None) or (None, error)."""
    if not text or not text.strip():
        return None, "empty response"
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        return (obj if isinstance(obj, dict) else None), (
            None if isinstance(obj, dict) else "top-level JSON is not an object"
        )
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    if start < 0:
        return None, "no JSON object found"
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(cleaned[start : i + 1])
                    return (obj if isinstance(obj, dict) else None), (
                        None if isinstance(obj, dict) else "top-level JSON is not an object"
                    )
                except json.JSONDecodeError as exc:
                    return None, f"unparseable JSON object: {exc}"
    return None, "unterminated JSON object"


def title_violations(title: Optional[str]) -> list[str]:
    """House hook-title rules from the selection prompt itself:
    3-9 words, third person only, no hashtags/emojis/quotes."""
    if title is None or not str(title).strip():
        return ["missing title"]
    text = str(title).strip()
    problems = []
    words = text.split()
    if not 3 <= len(words) <= 9:
        problems.append(f"word count {len(words)} outside 3-9")
    if _FIRST_PERSON.search(text):
        problems.append("first-person wording")
    if "#" in text:
        problems.append("hashtag")
    if _EMOJI.search(text):
        problems.append("emoji")
    if text != text.strip("\"'"):
        problems.append("surrounding quotes")
    return problems


def _coverage_fraction(phases: list[dict], duration: float) -> float:
    if duration <= 0:
        return 0.0
    spans = []
    for p in phases:
        s = max(0.0, min(float(p.get("start_offset", 0.0)), duration))
        e = max(0.0, min(float(p.get("end_offset", 0.0)), duration))
        if e > s:
            spans.append((s, e))
    spans.sort()
    covered = 0.0
    cur_s = cur_e = None
    for s, e in spans:
        if cur_s is None:
            cur_s, cur_e = s, e
        elif s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            covered += cur_e - cur_s
            cur_s, cur_e = s, e
    if cur_s is not None:
        covered += cur_e - cur_s
    return covered / duration


def plan_rubric(draft: dict, plan: dict, duration: float) -> dict:
    """Deterministic plan-quality rubric v1 (/100).

    Computed on the RAW draft (what the model returned, as a dict)
    vs the VALIDATED plan (what validate_plan_dict kept, as a dict).
    Every deduction names the rule the plan prompt itself states.
    """
    parts: dict[str, float] = {}

    hook = plan.get("hook") or {}
    hook_pts = 0.0
    if hook.get("type") and hook.get("type") != "none_strong":
        hook_pts += 15
    if (hook.get("reason") or "").strip():
        hook_pts += 5
    ts = hook.get("timestamp")
    if isinstance(ts, (int, float)) and 0.0 <= float(ts) <= duration:
        hook_pts += 5
    parts["hook"] = hook_pts

    parts["phases"] = round(20.0 * _coverage_fraction(plan.get("phases") or [], duration), 2)

    restraint = 25.0
    d_trans = draft.get("transition_choices") or []
    d_sfx = draft.get("sfx_events") or []
    p_trans = plan.get("transition_choices") or []
    p_sfx = plan.get("sfx_events") or []
    if len(d_trans) > 2:
        restraint -= 5  # prompt rule: at most 2 non-default transitions
    if len(d_sfx) > 4:
        restraint -= 5  # prompt rule: at most 4 SFX accents
    dropped = (len(d_sfx) - len(p_sfx)) + (len(d_trans) - len(p_trans))
    if draft.get("caption_style") and not plan.get("caption_style"):
        dropped += 1
    d_accent = (draft.get("opening_treatment") or {}).get("sfx_accent")
    p_accent = (plan.get("opening_treatment") or {}).get("sfx_accent")
    if d_accent and not p_accent:
        dropped += 1
    restraint -= 5 * dropped  # validator discarded it: unjustified effect
    parts["restraint"] = max(0.0, restraint)

    sfx_pts = 15.0
    offsets = sorted(float(e.get("at_offset", 0.0)) for e in p_sfx)
    for off in offsets:
        if off < 0.35 or off > duration - 0.35:
            sfx_pts -= 5
    for a, b in zip(offsets, offsets[1:]):
        if b - a < 0.8:
            sfx_pts -= 5
    parts["sfx_placement"] = max(0.0, sfx_pts)

    reasons_pts = 0.0
    if ((plan.get("music_decision") or {}).get("reason") or "").strip():
        reasons_pts += 5
    surviving = list(p_trans) + list(p_sfx)
    if plan.get("caption_style"):
        surviving.append(plan["caption_style"])
    if not surviving:
        reasons_pts += 10  # a clean plan needs no justifications
    else:
        with_reason = sum(1 for e in surviving if (e.get("reason") or "").strip())
        reasons_pts += 10.0 * with_reason / len(surviving)
    parts["reasons_music"] = round(reasons_pts, 2)

    return {"total": round(sum(parts.values()), 2), "parts": parts, "dropped_effects": dropped}


# ---------------------------------------------------------------------------
# HTTP client (OpenAI-compatible chat completions, stdlib only)
# ---------------------------------------------------------------------------

RETRYABLE = {429, 500, 502, 503, 504}


def chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    timeout: float = 300.0,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    retry_delay: float = 20.0,
    reasoning_effort: Optional[str] = None,
) -> dict:
    """One chat call with a single retry on transient HTTP errors.

    When ``reasoning_effort`` is set it is sent verbatim in the
    request. The returned dict carries ``reasoning_effort_state``:

    - ``accepted`` - the response shows reasoning took place
      (``usage.reasoning_tokens`` or a ``reasoning_content`` field);
    - ``ignored`` - the call succeeded but the response carries no
      sign the parameter had any effect (the endpoint may have
      accepted-and-ignored it; this is recorded, not assumed);
    - ``rejected`` - the call failed with a 4xx while carrying the
      parameter. The call is FAILED, never retried without the
      parameter and never silently downgraded to temperature 0;
    - ``error`` - the call failed for a non-4xx reason.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    attempts = 0
    last_error: Optional[str] = None
    last_status: Optional[int] = None
    while attempts < 2:
        attempts += 1
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            latency = time.perf_counter() - started
            choice = (body.get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content")) or ""
            state = None
            if reasoning_effort is not None:
                usage = body.get("usage") or {}
                message = choice.get("message") or {}
                if usage.get("reasoning_tokens") or message.get("reasoning_content"):
                    state = "accepted"
                else:
                    state = "ignored"
            return {
                "ok": True,
                "text": text,
                "latency_sec": round(latency, 3),
                "attempts": attempts,
                "usage": body.get("usage") or {},
                "finish_reason": choice.get("finish_reason"),
                "error": None,
                "reasoning_effort_state": state,
            }
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            last_error = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
            if exc.code not in RETRYABLE or attempts >= 2:
                break
        except Exception as exc:  # timeouts, DNS, connection resets
            last_error = f"{type(exc).__name__}: {exc}"
            if attempts >= 2:
                break
        time.sleep(retry_delay)
    state = None
    if reasoning_effort is not None:
        if last_status is not None and 400 <= last_status < 500:
            state = "rejected"
        else:
            state = "error"
    return {
        "ok": False,
        "text": "",
        "latency_sec": None,
        "attempts": attempts,
        "usage": {},
        "finish_reason": None,
        "error": last_error,
        "reasoning_effort_state": state,
    }


# ---------------------------------------------------------------------------
# Workload runners (app prompt builders + app parsers/validators)
# ---------------------------------------------------------------------------


def _load_app():
    """Import the app's builders/schemas/scorers (backend venv python)."""
    import score_selection  # tools/selection-eval (on sys.path)

    from src import ai as ai_mod
    from src.auto_director import classify as classify_mod
    from src.auto_director import content_requirements as content_req_mod
    from src.auto_director import content_requirements_llm as content_req_llm_mod
    from src.auto_director import plan_service
    from src.auto_director.clip_plan import validate_plan_dict
    from src.clip_duration import clip_duration

    return {
        "score_selection": score_selection,
        "ai": ai_mod,
        "classify": classify_mod,
        "content_requirements": content_req_mod,
        "requirements_llm": content_req_llm_mod,
        "plan_service": plan_service,
        "validate_plan_dict": validate_plan_dict,
        "clip_duration": clip_duration,
    }


_LINE_TS = re.compile(r"^\[(\d+):(\d{2}) - (\d+):(\d{2})\]")


def _transcript_line_bounds(transcript: str) -> tuple[list[float], list[float]]:
    starts, ends = [], []
    for line in transcript.splitlines():
        m = _LINE_TS.match(line.strip())
        if m:
            starts.append(int(m.group(1)) * 60 + int(m.group(2)))
            ends.append(int(m.group(3)) * 60 + int(m.group(4)))
    return starts, ends


def run_selection(app: dict, fixture: dict, call: dict) -> dict:
    """Score one selection response. ``call`` is a chat_completion dict
    plus the request/response bookkeeping the caller attaches."""
    out: dict[str, Any] = {"fixture": fixture["id"], "valid": False}
    if not call["ok"]:
        out["error"] = call["error"]
        return out
    obj, err = extract_json(call["text"])
    if obj is None:
        out["error"] = f"json: {err}"
        return out
    try:
        analysis = app["ai"].TranscriptAnalysis.model_validate(obj)
    except Exception as exc:
        out["error"] = f"schema: {str(exc)[:300]}"
        return out
    out["valid"] = True
    ss = app["score_selection"]
    bounds = app["clip_duration"]("auto")
    line_starts, line_ends = _transcript_line_bounds(fixture["transcript"])
    candidates = []
    titles = {"checked": 0, "violations": 0, "details": []}
    grounded = 0
    out_of_bounds = 0
    for i, seg in enumerate(analysis.most_relevant_segments):
        try:
            start = ss.parse_ts(seg.start_time)
            end = ss.parse_ts(seg.end_time)
        except Exception:
            out_of_bounds += 1
            continue
        candidates.append(
            {"id": f"seg-{i + 1}", "start": start, "end": end,
             "score": float(seg.virality.total_score)}
        )
        if not (bounds.minimum <= end - start <= bounds.maximum):
            out_of_bounds += 1
        if line_starts and (
            min(abs(start - s) for s in line_starts) <= 2.0
            and min(abs(end - e) for e in line_ends) <= 2.0
        ):
            grounded += 1
        titles["checked"] += 1
        problems = title_violations(seg.hook_title)
        if problems:
            titles["violations"] += 1
            titles["details"].append({"title": seg.hook_title, "problems": problems})
    out["candidates"] = candidates
    out["n_segments"] = len(candidates)
    out["grounded"] = grounded
    out["out_of_bounds"] = out_of_bounds
    out["titles"] = titles
    if fixture.get("source_id") and fixture.get("gold_ids_covered"):
        gold_doc = ss.load_gold(SELECTION_EVAL / "gold_set.json")
        golds, traps = ss.windows_for_source(gold_doc, fixture["source_id"])
        covered = set(fixture["gold_ids_covered"])
        golds = [g for g in golds if g["id"] in covered]
        traps = [t for t in traps if t.get("parent_gold") in covered]
        out["gold"] = ss.score_candidates(golds, traps, candidates)
    return out


def run_plan(app: dict, fixture: dict, call: dict) -> dict:
    out: dict[str, Any] = {"fixture": fixture["id"], "valid": False}
    if not call["ok"]:
        out["error"] = call["error"]
        return out
    obj, err = extract_json(call["text"])
    if obj is None:
        out["error"] = f"json: {err}"
        return out
    try:
        draft = app["plan_service"].ClipPlanDraft.model_validate(obj)
    except Exception as exc:
        out["error"] = f"schema: {str(exc)[:300]}"
        return out
    out["valid"] = True
    raw = app["plan_service"]._draft_to_plan_dict(draft)
    try:
        plan = app["validate_plan_dict"](
            raw,
            niche=fixture["niche"],
            duration=float(fixture["duration"]),
            profile=fixture["profile"],
            pauses=(fixture.get("audio_features") or {}).get("pauses"),
        )
        out["rubric"] = plan_rubric(raw, plan.to_dict(), float(fixture["duration"]))
    except Exception as exc:
        out["error"] = f"validate: {str(exc)[:300]}"
    return out


def run_classify_case(app: dict, case: dict, call: dict) -> dict:
    out: dict[str, Any] = {"case": case["id"], "valid": False}
    if not call["ok"]:
        out["error"] = call["error"]
        return out
    try:
        parsed = app["classify"]._parse_json_text_classification(call["text"])
    except Exception as exc:
        out["error"] = f"parse: {str(exc)[:300]}"
        return out
    out["valid"] = True
    out["raw_niche"] = parsed.niche
    out["raw_correct"] = parsed.niche == case["expected"]
    if case.get("guardrail", True):
        result = {
            "niche": parsed.niche,
            "confidence": parsed.confidence,
            "reasons": parsed.reasons,
        }
        evidence_sample = None
        if case.get("full_transcript"):
            evidence_sample = app["classify"].guardrail_evidence_sample(
                case["full_transcript"]
            )
        corrected = app["classify"].apply_evidence_guardrails(
            result,
            title=case.get("title"),
            transcript_sample=case["transcript_sample"],
            visual=None,
            audio=None,
            evidence_sample=evidence_sample,
        )
        out["corrected_niche"] = corrected.get("niche")
        out["corrected_correct"] = corrected.get("niche") == case["expected"]
    return out


def run_requirements(app: dict, fixture: dict, call: dict) -> dict:
    """Score one content-requirements focus response (fixture B).

    The response is validated against the pass's own
    ``FocusJudgements`` schema, then every returned judgement is run
    through the app's own ``_merge_llm_judgement`` verbatim-evidence
    gate against the deterministic entry for that candidate and
    requirement: invented evidence is discarded (never scored), a
    low-confidence promotion of a zero-hit candidate is discarded,
    and only a merged judgement counts towards focus agreement.
    """
    out: dict[str, Any] = {"fixture": fixture["id"], "valid": False}
    if not call["ok"]:
        out["error"] = call["error"]
        return out
    obj, err = extract_json(call["text"])
    if obj is None:
        out["error"] = f"json: {err}"
        return out
    try:
        judgements = app["requirements_llm"].FocusJudgements.model_validate(obj)
    except Exception as exc:
        out["error"] = f"schema: {str(exc)[:300]}"
        return out
    out["valid"] = True
    by_id = {c["candidate_id"]: c for c in fixture["candidates"]}
    brief_reqs = {
        r["id"]: r for r in (fixture.get("brief") or {}).get("requirements", [])
    }
    expected = fixture.get("expected") or {}
    accepted = discarded = agreed = 0
    seen: set[tuple[Any, Any]] = set()
    details: list[dict] = []
    for item in judgements.judgements or []:
        cand = by_id.get(item.candidate_id)
        exp = (expected.get(str(item.candidate_id)) or {}).get(item.requirement_id)
        req = brief_reqs.get(item.requirement_id)
        if cand is None or exp is None or req is None:
            details.append(
                {
                    "candidate_id": item.candidate_id,
                    "requirement_id": item.requirement_id,
                    "outcome": "unexpected",
                }
            )
            continue
        seen.add((item.candidate_id, item.requirement_id))
        entry = {
            "id": item.requirement_id,
            "type": req.get("type"),
            "kind": req.get("kind"),
            "focus": exp["focus"],
            "status": exp["status"],
            "requires_major": bool(req.get("requires_major")),
            "grey_zone": False,
            "judgement_source": "deterministic",
            "decided_by": "deterministic",
        }
        merged = app["content_requirements"]._merge_llm_judgement(
            entry,
            {
                "focus": item.focus,
                "confidence": item.confidence,
                "evidence_excerpt": item.evidence_excerpt,
                "reason": item.reason,
            },
            cand["text"],
        )
        if merged is None:
            discarded += 1
            details.append(
                {
                    "candidate_id": item.candidate_id,
                    "requirement_id": item.requirement_id,
                    "outcome": "discarded",
                }
            )
        else:
            accepted += 1
            is_agreement = merged["focus"] == exp["focus"]
            if is_agreement:
                agreed += 1
            details.append(
                {
                    "candidate_id": item.candidate_id,
                    "requirement_id": item.requirement_id,
                    "outcome": "accepted",
                    "agreed": is_agreement,
                }
            )
    total_expected = sum(len(v) for v in expected.values())
    out["focus"] = {
        "returned": len(judgements.judgements or []),
        "expected": total_expected,
        "accepted": accepted,
        "discarded": discarded,
        "agreed": agreed,
        "missing": total_expected - len(seen),
        "details": details,
    }
    return out


def build_requests(app: dict, fixtures: list[dict]) -> list[dict]:
    """One request spec per (fixture | classify case), prompts built by
    the app's own builders - identical for every model."""
    requests = []
    for fixture in fixtures:
        kind = fixture["kind"]
        if kind == "selection":
            requests.append(
                {
                    "workload": "selection",
                    "fixture": fixture,
                    "system": app["ai"].build_transcript_system_prompt("auto"),
                    "user": app["ai"].build_transcript_analysis_prompt(
                        fixture["transcript"]
                    ),
                }
            )
        elif kind == "plan":
            requests.append(
                {
                    "workload": "plan",
                    "fixture": fixture,
                    "system": app["plan_service"].PLAN_SYSTEM_PROMPT,
                    "user": app["plan_service"]._build_plan_prompt(
                        niche=fixture["niche"],
                        profile=fixture["profile"],
                        duration=float(fixture["duration"]),
                        text=fixture["text"],
                        words=fixture.get("words") or [],
                        audio_features=fixture.get("audio_features"),
                        selection_scores=fixture.get("selection_scores"),
                    ),
                }
            )
        elif kind == "classify":
            for case in fixture["cases"]:
                requests.append(
                    {
                        "workload": "classify",
                        "fixture": fixture,
                        "case": case,
                        "system": app["classify"].NICHE_CLASSIFICATION_SYSTEM_PROMPT,
                        "user": app["classify"]._build_niche_classification_prompt(
                            case["transcript_sample"], [], title=case.get("title")
                        ),
                    }
                )
        elif kind == "requirements":
            requests.append(
                {
                    "workload": "requirements",
                    "fixture": fixture,
                    "system": app["requirements_llm"].FOCUS_SYSTEM_PROMPT,
                    "user": app["requirements_llm"].build_focus_prompt(
                        fixture["candidates"], fixture["brief"]
                    ),
                }
            )
        else:
            raise ValueError(f"unknown fixture kind {kind!r} in {fixture.get('id')}")
    return requests


def load_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> list[dict]:
    fixtures = []
    for path in sorted(fixtures_dir.glob("*.json")):
        fixtures.append(json.loads(path.read_text(encoding="utf-8")))
    return fixtures


def run_bakeoff(
    *,
    models: list[str],
    base_url: str,
    api_key: str,
    fixtures: Optional[list[dict]] = None,
    timeout: float = 300.0,
    temperature: float = 0.0,
    retry_delay: float = 20.0,
    reasoning_effort: Optional[str] = None,
    fixture_ids: Optional[list[str]] = None,
) -> dict:
    """Run every workload against every model; return raw + aggregate.

    ``reasoning_effort`` (fixture A, premium family): when set, the
    caller passes only the two selection fixtures and every call
    carries the parameter; each scored request records its
    ``reasoning_effort_state`` (accepted / ignored / rejected /
    error). ``fixture_ids`` restricts the run to a named subset (one
    family at a time) without touching the fixture files.
    """
    app = _load_app()
    fixtures = fixtures if fixtures is not None else load_fixtures()
    if fixture_ids is not None:
        wanted = set(fixture_ids)
        fixtures = [f for f in fixtures if f["id"] in wanted]
        missing = wanted - {f["id"] for f in fixtures}
        if missing:
            raise ValueError(f"unknown fixture ids: {sorted(missing)}")
    requests = build_requests(app, fixtures)
    results: dict[str, dict] = {}
    for model in models:
        per_request = []
        for spec in requests:
            call = chat_completion(
                base_url=base_url,
                api_key=api_key,
                model=model,
                system=spec["system"],
                user=spec["user"],
                timeout=timeout,
                temperature=temperature,
                retry_delay=retry_delay,
                reasoning_effort=reasoning_effort,
            )
            if spec["workload"] == "selection":
                scored = run_selection(app, spec["fixture"], call)
            elif spec["workload"] == "plan":
                scored = run_plan(app, spec["fixture"], call)
            elif spec["workload"] == "requirements":
                scored = run_requirements(app, spec["fixture"], call)
            else:
                scored = run_classify_case(app, spec["case"], call)
            scored["workload"] = spec["workload"]
            scored["latency_sec"] = call["latency_sec"]
            scored["attempts"] = call["attempts"]
            if reasoning_effort is not None:
                scored["reasoning_effort"] = reasoning_effort
                scored["reasoning_effort_state"] = call.get("reasoning_effort_state")
            per_request.append(scored)
            print(
                f"[{model}] {spec['workload']}:"
                f"{scored.get('fixture') or scored.get('case')}"
                f" valid={scored['valid']} latency={call['latency_sec']}",
                file=sys.stderr,
            )
        results[model] = {
            "requests": per_request,
            "aggregate": aggregate(per_request),
        }
    return results


def aggregate(per_request: list[dict]) -> dict:
    total = len(per_request)
    valid = sum(1 for r in per_request if r.get("valid"))
    latencies = [r["latency_sec"] for r in per_request if r.get("latency_sec")]
    agg: dict[str, Any] = {
        "calls": total,
        "schema_valid": valid,
        "validity_rate": round(valid / total, 4) if total else 0.0,
        "median_latency_sec": round(statistics.median(latencies), 3) if latencies else None,
        "mean_latency_sec": round(statistics.mean(latencies), 3) if latencies else None,
    }
    japan = next(
        (r for r in per_request if r.get("fixture") == "japan_selection"), None
    )
    if japan and japan.get("gold"):
        g = japan["gold"]
        agg["japan"] = {
            "recall": g["recall"],
            "precision_at_5": g["precision_at_5"],
            "mean_overlap": g["mean_overlap"],
            "traps_taken": len(g["traps_taken"]),
            "duplicate_picks": g["duplicate_picks"],
        }
    else:
        agg["japan"] = None
    longform = next(
        (r for r in per_request if r.get("fixture") == "tjr_longform_selection"), None
    )
    if longform is not None:
        agg["longform"] = {
            "valid": bool(longform.get("valid")),
            "segments": longform.get("n_segments"),
            "grounded": longform.get("grounded"),
            "out_of_bounds": longform.get("out_of_bounds"),
        }
    reqs = [r for r in per_request if r.get("workload") == "requirements"]
    if reqs:
        focus = [r.get("focus") or {} for r in reqs]
        agg["focus_agreement"] = (
            f"{sum(x.get('agreed', 0) for x in focus)}"
            f"/{sum(x.get('expected', 0) for x in focus)}"
        )
        agg["focus_accepted"] = (
            f"{sum(x.get('accepted', 0) for x in focus)}"
            f"/{sum(x.get('returned', 0) for x in focus)}"
        )
    states = [
        r.get("reasoning_effort_state")
        for r in per_request
        if r.get("reasoning_effort_state")
    ]
    if states:
        agg["reasoning_effort"] = {
            s: states.count(s)
            for s in ("accepted", "ignored", "rejected", "error")
            if states.count(s)
        }
    classify_reqs = [r for r in per_request if r.get("workload") == "classify"]
    if classify_reqs:
        agg["niche_raw"] = (
            f"{sum(1 for r in classify_reqs if r.get('raw_correct'))}/{len(classify_reqs)}"
        )
        corrected = [r for r in classify_reqs if "corrected_correct" in r]
        agg["niche_corrected"] = (
            f"{sum(1 for r in corrected if r.get('corrected_correct'))}/{len(corrected)}"
            if corrected
            else "n/a"
        )
    titles_checked = sum(
        (r.get("titles") or {}).get("checked", 0) for r in per_request
    )
    titles_bad = sum(
        (r.get("titles") or {}).get("violations", 0) for r in per_request
    )
    agg["titles"] = {"checked": titles_checked, "violations": titles_bad}
    rubrics = [
        r["rubric"]["total"]
        for r in per_request
        if r.get("workload") == "plan" and r.get("rubric")
    ]
    agg["plan_rubric_mean"] = round(statistics.mean(rubrics), 2) if rubrics else None
    agg["errors"] = [
        f"{r.get('workload')}:{r.get('fixture') or r.get('case')}: {r['error']}"
        for r in per_request
        if r.get("error")
    ]
    return agg


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render_table(results: dict) -> str:
    """The comparison table: one row per model, identical workloads.

    The Focus and Reasoning-effort columns appear only when the run
    carried those workloads, so the core 8-call table is unchanged.
    """
    show_focus = any(
        res["aggregate"].get("focus_agreement") for res in results.values()
    )
    show_reasoning = any(
        res["aggregate"].get("reasoning_effort") for res in results.values()
    )
    header = (
        "| Model | Japan recall | Japan mean overlap | P@5 | Traps | Schema valid | "
        "Niche raw | Niche +guardrails | Title violations | Plan rubric /100 | Median latency |"
    )
    divider = "|---|---|---|---|---|---|---|---|---|---|---|"
    if show_focus:
        header += " Focus agree |"
        divider += "---|"
    if show_reasoning:
        header += " Reasoning effort |"
        divider += "---|"
    lines = [header, divider]
    for model, res in results.items():
        a = res["aggregate"]
        j = a.get("japan") or {}
        titles = a.get("titles") or {}
        row = (
            "| {model} | {recall} | {overlap} | {p5} | {traps} | {valid} | {raw} | "
            "{corr} | {tv} | {rubric} | {lat} |".format(
                model=f"`{model}`",
                recall=j.get("recall", "—"),
                overlap=j.get("mean_overlap", "—"),
                p5=j.get("precision_at_5", "—"),
                traps=j.get("traps_taken", "—"),
                valid=f"{a['schema_valid']}/{a['calls']}",
                raw=a.get("niche_raw", "—"),
                corr=a.get("niche_corrected", "—"),
                tv=f"{titles.get('violations', 0)}/{titles.get('checked', 0)}",
                rubric=a["plan_rubric_mean"] if a.get("plan_rubric_mean") is not None else "—",
                lat=f"{a['median_latency_sec']}s" if a.get("median_latency_sec") else "—",
            )
        )
        if show_focus:
            row += f" {a.get('focus_agreement', '—')} |"
        if show_reasoning:
            counts = a.get("reasoning_effort") or {}
            row += (
                " "
                + "/".join(f"{s}:{counts.get(s, 0)}" for s in ("accepted", "ignored", "rejected", "error") if counts.get(s))
                + " |"
                if counts
                else " — |"
            )
        lines.append(row)
    return "\n".join(lines)


def load_evidence() -> dict:
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


def render_report(results: Optional[dict] = None) -> str:
    ev = load_evidence()
    lines: list[str] = []
    lines.append("# NIM model bake-off — which brain should hold the NIM rungs?")
    lines.append("")
    lines.append(f"_Offline phase: 2026-11-12. Live phase: {'DONE - results below' if results else 'PENDING (protocol in §5)' }._")
    lines.append("")
    lines.append("## 1. Status")
    lines.append("")
    lines.append(
        "- The harness (`tools/nim-bakeoff/bakeoff.py`) is built and proven offline "
        "against a mocked OpenAI-compatible server (fake-good / fake-mid / fake-bad "
        "responders score exactly as designed - see the harness tests)."
    )
    lines.append(
        "- Candidate deployments are PRE-REGISTERED in `litellm_config.yaml` "
        "(aliases `nim-glm-flash`, `nim-kimi-k2`, `nim-mistral-large4`, "
        "`nim-qwen3-235b`, `nim-llama4-maverick`, `nim-deepseek-pro`) but NO "
        "production chain falls back to them and no app code calls them - "
        "nothing in production behaviour changes until a one-line slug flip "
        "arms one of them (see §4)."
    )
    lines.append(
        "- **No live call has been made from the build sandbox** (no NIM key "
        "exists there, by design). All model-quality statements below are "
        "PROVISIONAL, from published benchmarks, and are labelled as such."
    )
    lines.append("")
    lines.append("## 2. Method (what the live run measures)")
    lines.append("")
    lines.append(
        "Every workload prompt is built by the app's own builders and every "
        "response is parsed/validated by the app's own schemas, parsers and "
        "validators - no hand-copied prompts. Workloads: (a) moment selection "
        "on the Japan gold ranges (recall / precision@5 / mean overlap / traps "
        "via `tools/selection-eval/score_selection.py`, scored on g1/g3/g4 - "
        "the only golds with real transcript evidence in-tree) plus a TJR "
        "sanity selection; (b) per-clip planning on two real segments, graded "
        "by the deterministic plan rubric v1 (hook, phase coverage, restraint "
        "vs the production validator, SFX placement, reasons/music); "
        "(c) niche classification on 4 real cases, scored raw AND after the "
        "production evidence guardrails; (d) hook-title rule violations "
        "(3-9 words, third person, no hashtags/emoji) and one-shot schema "
        "validity across all 8 calls; (e) latency per call."
    )
    lines.append("")
    lines.append("## 3. Candidates and slug verification")
    lines.append("")
    lines.append("| Alias | Slug | Status | Published AA index | Role |")
    lines.append("|---|---|---|---|---|")
    for c in ev["candidates"]:
        lines.append(
            f"| `{c['alias']}` | `{c['slug']}` | {c['slug_status']} | "
            f"{c['aa_index'] if c['aa_index'] is not None else 'not published'} | {c['role']} |"
        )
    lines.append("")
    lines.append("Verification notes (offline sources, 2026-11-12):")
    for c in ev["candidates"]:
        lines.append(f"- `{c['slug']}` — {c['verification']}")
    lines.append("")
    lines.append("## 4. Provisional ranking (OFFLINE evidence only - NOT our workload)")
    lines.append("")
    lines.append(
        "Source: " + ev["benchmark_source"] + " These numbers measure general "
        "intelligence; they say nothing directly about JSON discipline, moment "
        "selection or plan restraint on THIS app's prompts. They exist to pick "
        "the order in which the live run tries candidates and to name the "
        "provisional swap - nothing more."
    )
    lines.append("")
    for r in ev["provisional_ranking"]:
        rank = r["rank"] if r["rank"] is not None else "unranked"
        lines.append(f"- **{rank}. `{r['slug']}`** — {r['basis']}")
    lines.append("")
    swap = ev["provisional_swap"]
    lines.append("**Prepared (not armed) swap:**")
    lines.append(f"- `clip-fast` rung: `{swap['clip-fast']}`")
    lines.append(f"- `nim-deepseek` rung: {swap['nim-deepseek']}")
    lines.append("")
    lines.append(
        "Arming it later = ONE line in `litellm_config.yaml` (the `model:` of "
        "the `clip-fast` entry) plus the same slug updated in the pinned "
        "expectation in `backend/tests/unit/test_litellm_gateway_config.py`, "
        "then the gateway suite. Until the live run justifies it, today's "
        "slugs stay."
    )
    lines.append("")
    lines.append("## 5. Live-run protocol (for the parent, with a real NIM key)")
    lines.append("")
    lines.append("Run from the repo root, with the backend venv python "
                 "(the harness imports the app, so plain system python3 "
                 "without the backend deps will not work - in the build "
                 "sandbox that python is "
                 "`~/workspace/jcs-modes/backend/.venv/bin/python`; set "
                 "`PY` to it below).")
    lines.append("")
    lines.append("**Step 0 - probe the slugs (2 minutes, saves the run):**")
    lines.append("")
    lines.append("```bash")
    lines.append("export NVIDIA_API_KEY=<the key>   # never commit, never paste in chat")
    lines.append('curl -s https://integrate.api.nvidia.com/v1/models -H "Authorization: Bearer $NVIDIA_API_KEY" | python3 -c "import json,sys; print(sorted(m[\'id\'] for m in json.load(sys.stdin)[\'data\']))"')
    lines.append("```")
    lines.append(
        "Check the eight candidate slugs from §3 against the listing. Any "
        "slug missing from the listing (or answering a 1-token probe with "
        "404) is dropped from BAKEOFF_MODELS for this run - the harness "
        "records its calls as errors either way, but dropping it keeps the "
        "run inside the 40 RPM / 10,000 req/day free ceilings."
    )
    lines.append("")
    lines.append("**Step 1 - the run (8 calls per model, ~64 calls total):**")
    lines.append("")
    lines.append("```bash")
    lines.append("PY=~/workspace/jcs-modes/backend/.venv/bin/python   # the backend venv python")
    lines.append("cd tools/nim-bakeoff")
    lines.append("export BAKEOFF_BASE_URL=https://integrate.api.nvidia.com/v1")
    lines.append("export BAKEOFF_API_KEY=$NVIDIA_API_KEY")
    lines.append("export BAKEOFF_MODELS='z-ai/glm-5.3-flash,deepseek-ai/deepseek-v4.1-flash,moonshotai/kimi-k2.6,nvidia/nemotron-3-ultra-550b-a55b,mistralai/mistral-large-4-1t-a52b,qwen/qwen3-235b-a22b,meta/llama-4-maverick-17b-128e-instruct,deepseek-ai/deepseek-v4-pro'")
    lines.append("$PY bakeoff.py --out results/live-YYYYMMDD   # writes results.json + table.md")
    lines.append("$PY bakeoff.py --results results/live-YYYYMMDD/results.json --report ~/workspace/research_notes/sprint-7day/NIM_BAKEOFF.md")
    lines.append("```")
    lines.append("")
    lines.append(
        "Sample sizes: the full fixture set is 8 calls per model (2 selection, "
        "2 plan, 4 classify). That is a smoke comparison, not a statistic - "
        "the gold set's own README sets the noise floor at ~0.1 on overlap "
        "metrics. For a decision-grade run, repeat the whole set 3 times "
        "(24 calls per model, still trivial against the daily ceiling) and "
        "compare medians."
    )
    lines.append("")
    lines.append("**Decision rule (what margin justifies a swap):**")
    lines.append("")
    lines.append(
        "1. A challenger must first be RELIABLE: schema validity 8/8 on the "
        "single run (or >= 23/24 across 3 repeats) - a rung that returns "
        "unparseable JSON costs a fallback hop on real traffic.\n"
        "2. It must WIN the workload, not the benchmark: Japan mean overlap "
        "or recall better than the incumbent rung's model by MORE than the "
        "0.1 noise floor, with no regression in traps taken, niche "
        "(guardrail-corrected) accuracy, title violations or plan rubric "
        "(rubric regression > 10 points vetoes).\n"
        "3. Latency is a tie-breaker only, and only for the clip-fast rung "
        "(median per-call latency worse than 2x the incumbent needs a "
        "quality win at the top of the margin band to compensate).\n"
        "4. If no challenger clears (1)+(2), the incumbent stays - the "
        "published-benchmark ranking alone NEVER arms a swap."
    )
    lines.append("")
    lines.append("## 6. Results")
    lines.append("")
    if results:
        lines.append(render_table(results))
        lines.append("")
        for model, res in results.items():
            errs = res["aggregate"].get("errors") or []
            if errs:
                lines.append(f"Errors for `{model}`:")
                for e in errs:
                    lines.append(f"- {e}")
                lines.append("")
    else:
        lines.append(
            "_Live run pending._ When it completes, this section carries the "
            "comparison table (Japan recall / mean overlap / P@5 / traps, "
            "schema validity, niche raw + guardrail-corrected, title "
            "violations, plan rubric, median latency) for all eight models "
            "on identical workloads."
        )
        lines.append("")
    lines.append("## 7. Limitations (read before quoting any number)")
    lines.append("")
    lines.append(
        "- Japan agreement covers 3 of 5 gold windows (g1/g3/g4) - the only "
        "ranges with cached transcript. g2/g5 are excluded, not estimated. "
        "A full-source Japan run needs the transcript regenerated from the "
        "cached source video (faster-whisper) and a fixture rebuild.\n"
        "- Fixture transcripts are excerpt-scale; production analyses long "
        "sources in windows. Relative model comparison is unaffected (same "
        "input for every model), absolute agreement numbers are not "
        "production predictions.\n"
        "- Schema validity is one-shot against the production schemas; "
        "production also gets pydantic-ai output retries, so live validity "
        "understates production validity for every model equally.\n"
        "- The classify fixtures run without named entities and (except the "
        "$10M case's guardrail sample) without visual/audio evidence - the "
        "same evidence shape for every model.\n"
        "- NIM free endpoints are shared preview capacity: latency numbers "
        "measure the day, not the model. Treat latency as a tie-breaker "
        "only (see the decision rule)."
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible server (offline proof + tests)
# ---------------------------------------------------------------------------

_GOLD_TITLES = {
    "g1": "That Avocado Came From a 700-Year-Old Tree",
    "g3": "Diners Queue For A Single Seat Restaurant",
    "g4": "Chef Serves A 400 Dollar Tasting Course",
}


def _fake_selection(model: str, user: str) -> str:
    # Long-form fixture (tjr_longform_selection): a ~24k-char transcript.
    # The fake-good model returns the first transcript line's exact span
    # (grounded, in bounds); fake-mid returns an ungrounded pick with a
    # first-person title, so the long-form sanity metrics discriminate.
    if len(user) > 5000:
        # First timestamped line of the ACTUAL transcript: the prompt
        # header itself carries example lines ("[00:12 - 00:21] Spoken
        # text here"), which must be skipped.
        first = None
        for m in re.finditer(r"\[(\d+):(\d{2}) - (\d+):(\d{2})\] ([^\n]*)", user):
            if m.group(5).strip() in ("Spoken text here", "More spoken text here"):
                continue
            first = m
            break
        m = first
        if model == "fake-mid":
            segs = [
                {"start_time": "00:02", "end_time": "00:25",
                 "text": "unanchored pick off the long transcript",
                 "relevance_score": 0.9, "reasoning": "Long-form drift.",
                 "virality": {"total_score": 80},
                 "hook_title": "I Win More Than I Lose"}
            ]
        elif m:
            start = f"{int(m.group(1)):02d}:{m.group(2)}"
            end = f"{int(m.group(3)):02d}:{m.group(4)}"
            segs = [
                {"start_time": start, "end_time": end,
                 "text": "first line of the long transcript",
                 "relevance_score": 0.9, "reasoning": "Opening line of the day.",
                 "virality": {"total_score": 88},
                 "hook_title": "Trader Explains Why Losses Fuel Bigger Wins"}
            ]
        else:
            segs = []
        return json.dumps({"most_relevant_segments": segs, "summary": "s", "key_topics": ["trading"]})
    if "probabilities within the market" in user:  # TJR clip fixture
        if model == "fake-mid":
            segs = [
                {"start_time": "00:02", "end_time": "00:25",
                 "text": "I know that 10 times out of 10, when I execute on my strategy, I have higher odds of winning than losing.",
                 "relevance_score": 0.9, "reasoning": "Core strategy claim.",
                 "virality": {"total_score": 80},
                 "hook_title": "I Win More Than I Lose"}
            ]
        else:
            segs = [
                {"start_time": "00:02", "end_time": "00:25",
                 "text": "This loss today, or these two loss today, are actually helping me get closer to my next win.",
                 "relevance_score": 0.9, "reasoning": "Counterintuitive losses claim with payoff.",
                 "virality": {"total_score": 88},
                 "hook_title": "Trader Explains Why Losses Fuel Bigger Wins"}
            ]
        return json.dumps({"most_relevant_segments": segs, "summary": "s", "key_topics": ["trading"]})
    # Japan fixture
    if model == "fake-mid":
        segs = [
            {"start_time": "14:58", "end_time": "15:18",  # g1 shifted +8s: duplicate trap
             "text": "That's pretty good avocado.", "relevance_score": 0.9,
             "reasoning": "Avocado moment.", "virality": {"total_score": 90},
             "hook_title": "I Tried A 700 Year Old Avocado Tree"},
            {"start_time": "22:20", "end_time": "22:29",  # g3 tail: fragment trap
             "text": "tail fragment", "relevance_score": 0.7,
             "reasoning": "Queue moment.", "virality": {"total_score": 70},
             "hook_title": "Queue For One Seat"},
        ]
        return json.dumps({"most_relevant_segments": segs, "summary": "s", "key_topics": ["food"]})
    segs = []
    for gid, (start, end) in {
        "g1": ("14:50", "15:10"),
        "g3": ("22:04", "22:29"),
        "g4": ("40:39", "41:09"),
    }.items():
        segs.append(
            {"start_time": start, "end_time": end, "text": "gold segment",
             "relevance_score": 0.92, "reasoning": "Exact gold window.",
             "virality": {"total_score": 90}, "hook_title": _GOLD_TITLES[gid]}
        )
    return json.dumps({"most_relevant_segments": segs, "summary": "s", "key_topics": ["food"]})


def _fake_plan(model: str, user: str) -> str:
    m = re.search(r"Clip duration: ([\d.]+) seconds", user)
    duration = float(m.group(1)) if m else 20.0
    if model == "fake-mid":
        plan = {
            "hook_type": "none_strong", "hook_timestamp": 0.0,
            "hook_confidence": 0.4, "hook_reason": "",
            "punch_zoom": "strong", "first_caption_emphasis": False,
            "sfx_accent": "impact", "opening_at_offset": 0.0,
            "opening_reason": "for engagement",
            "transition_choices": [
                {"name": "zoomin", "at_offset": 2.0, "reason": "for engagement"},
                {"name": "pixelize", "at_offset": 4.0, "reason": "more engaging"},
                {"name": "radial", "at_offset": 6.0, "reason": "viral"},
            ],
            "caption_style_name": "neon", "caption_style_reason": "looks cool",
            "phases": [
                {"start_offset": 0.0, "end_offset": duration / 2, "intent": "hook"},
            ],
            "sfx_events": [
                {"name": "whoosh", "at_offset": 5.0, "reason": "for engagement"},
                {"name": "pop", "at_offset": 5.2, "reason": "for engagement"},
                {"name": "ding", "at_offset": 5.4, "reason": "for engagement"},
                {"name": "riser", "at_offset": 5.6, "reason": "for engagement"},
                {"name": "impact", "at_offset": 5.8, "reason": "for engagement"},
            ],
            "use_profile_music": True, "music_reason": "",
            "plan_confidence": 0.5,
        }
        return json.dumps(plan)
    third = round(duration / 3, 2)
    plan = {
        "hook_type": "reveal", "hook_timestamp": 0.0,
        "hook_confidence": 0.9,
        "hook_reason": "The opening line lands the reveal immediately.",
        "punch_zoom": "subtle", "first_caption_emphasis": True,
        "sfx_accent": None, "opening_at_offset": 0.0,
        "opening_reason": "The first line is the reveal, so a subtle punch underlines it.",
        "transition_choices": [],
        "caption_style_name": None, "caption_style_reason": "",
        "phases": [
            {"start_offset": 0.0, "end_offset": third, "intent": "hook"},
            {"start_offset": third, "end_offset": 2 * third, "intent": "build"},
            {"start_offset": 2 * third, "end_offset": duration, "intent": "payoff"},
        ],
        "sfx_events": [
            {"name": "ding", "at_offset": round(duration * 0.66, 2),
             "reason": "The payoff lands here, a soft ding marks it."},
        ],
        "use_profile_music": True,
        "music_reason": "Profile music supports the pacing of this clip.",
        "plan_confidence": 0.85,
    }
    return json.dumps(plan)


def _fake_classify(model: str, user: str) -> str:
    if model == "fake-mid":
        niche = "podcast_news"
    elif "Week in the Life of a Millionaire" in user:
        niche = "vlog_lifestyle"
    elif "$10M Trader" in user:
        niche = "finance_business"
    elif "RESTAURANTS IN JAPAN" in user:
        niche = "travel_vlog"
    else:
        niche = "podcast_news"
    return json.dumps({"niche": niche, "confidence": 0.9, "reasons": ["fake evidence"]})


def _fake_requirements(model: str, user: str) -> str:
    """Canned focus-pass answers for the requirements fixture.

    The prompt is ``Judge each candidate...\\n{json}`` with the
    candidates and their topic_focus requirements. ``fake-good``
    judges from the candidate text itself (Expedia present -> major,
    else absent) with a verbatim excerpt, so the app's own merge gate
    accepts it; ``fake-mid`` calls everything major with invented
    evidence, so the gate discards both judgements.
    """
    start = user.find("{")
    payload = json.loads(user[start:])
    out = []
    for cand in payload.get("candidates") or []:
        text = cand.get("text") or ""
        if model == "fake-mid":
            focus = "major"
            excerpt = "Expedia is everywhere always"
        else:
            focus = "major" if "Expedia" in text else "absent"
            excerpt = text[:60].strip()
        for req in payload.get("requirements") or []:
            out.append(
                {
                    "candidate_id": cand.get("candidate_id"),
                    "requirement_id": req.get("id"),
                    "focus": focus,
                    "confidence": 0.9,
                    "evidence_excerpt": excerpt,
                    "reason": "fake evidence",
                }
            )
    return json.dumps({"judgements": out})


class _FakeHTTPError(Exception):
    """The fake server's way to answer with a non-200 status."""

    def __init__(self, status: int, body: str):
        super().__init__(body)
        self.status = status
        self.body = body


def fake_responder(model: str, messages: list[dict], reasoning_effort: Optional[str] = None) -> str:
    """Canned responses for the offline proof. ``fake-bad`` never
    returns JSON; ``fake-mid`` returns valid JSON with known defects;
    anything else behaves as ``fake-good``.

    ``fake-rejects-reasoning`` answers any call carrying
    ``reasoning_effort`` with a 400 (the premium pass must record
    that as a failed call, not a silent downgrade);
    ``fake-accepts-reasoning`` behaves as ``fake-good`` and the
    handler adds ``usage.reasoning_tokens`` to its response, so the
    accepted state is provable end to end.
    """
    if model == "fake-rejects-reasoning" and reasoning_effort is not None:
        raise _FakeHTTPError(
            400, "reasoning_effort is not supported by this model"
        )
    if model == "fake-bad":
        return "Sorry, I cannot help with that request."
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    if "satisfy campaign content requirements" in system:
        return _fake_requirements(model, user)
    if "content classifier for short-form" in system:
        return _fake_classify(model, user)
    if "viral editing planner" in system:
        return _fake_plan(model, user)
    return _fake_selection(model, user)


class _FakeHandler:
    """Build a BaseHTTPRequestHandler subclass bound to no state."""

    @staticmethod
    def make():
        from http.server import BaseHTTPRequestHandler

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 (stdlib naming)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                try:
                    content = fake_responder(
                        body.get("model", ""),
                        body.get("messages") or [],
                        body.get("reasoning_effort"),
                    )
                except _FakeHTTPError as exc:
                    data = json.dumps(
                        {"error": {"message": exc.body, "code": exc.status}}
                    ).encode("utf-8")
                    self.send_response(exc.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                usage = {"prompt_tokens": 10, "completion_tokens": 20,
                         "total_tokens": 30}
                if body.get("reasoning_effort") and body.get("model") == "fake-accepts-reasoning":
                    usage["reasoning_tokens"] = 150
                resp = {
                    "choices": [
                        {"message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}
                    ],
                    "usage": usage,
                }
                data = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # silence
                pass

        return Handler


class fake_server:
    """Context manager: serve the fake responder on a local port."""

    def __enter__(self):
        from http.server import ThreadingHTTPServer

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler.make())
        import threading

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{port}/v1"
        return self.base_url

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", help="comma-separated slugs (default: BAKEOFF_MODELS or evidence list)")
    ap.add_argument("--fixtures-dir", type=Path, default=FIXTURES_DIR)
    ap.add_argument("--fixtures", help="comma-separated fixture ids to run (default: all)")
    ap.add_argument(
        "--reasoning-effort",
        choices=["none", "high"],
        default=None,
        help="premium pass: re-run the two selection fixtures with "
        "reasoning_effort=high (default: BAKEOFF_REASONING_EFFORT or none)",
    )
    ap.add_argument("--out", type=Path, help="write results.json + table.md here (live run)")
    ap.add_argument("--results", type=Path, help="existing results.json to render a report from")
    ap.add_argument("--report", type=Path, help="write the markdown report here")
    ap.add_argument("--offline", action="store_true", help="report from offline evidence only, no calls")
    ap.add_argument("--self-check", action="store_true", help="run the fake-server end-to-end check")
    args = ap.parse_args(argv)

    if args.self_check:
        with fake_server() as base_url:
            results = run_bakeoff(
                models=["fake-good", "fake-mid", "fake-bad"],
                base_url=base_url,
                api_key="fake",
                fixtures=load_fixtures(args.fixtures_dir),
                retry_delay=0.0,
            )
            selection = [
                f
                for f in load_fixtures(args.fixtures_dir)
                if f["id"] in ("japan_selection", "tjr_selection")
            ]
            premium = run_bakeoff(
                models=["fake-good", "fake-accepts-reasoning", "fake-rejects-reasoning"],
                base_url=base_url,
                api_key="fake",
                fixtures=selection,
                reasoning_effort="high",
                retry_delay=0.0,
            )
        print(render_table(results))
        print()
        print("Premium pass (reasoning_effort=high, selection fixtures only):")
        print(render_table(premium))
        good = results["fake-good"]["aggregate"]
        bad = results["fake-bad"]["aggregate"]
        prem_good = premium["fake-good"]["requests"]
        prem_accepts = premium["fake-accepts-reasoning"]["requests"]
        prem_rejects = premium["fake-rejects-reasoning"]["requests"]
        ok = (
            good["japan"] and good["japan"]["recall"] == 1.0
            and good["japan"]["mean_overlap"] == 1.0
            and good["japan"]["traps_taken"] == 0
            and good["validity_rate"] == 1.0
            and bad["validity_rate"] == 0.0
            and good.get("focus_agreement") == "2/2"
            and good.get("longform", {}).get("valid") is True
            and all(r.get("reasoning_effort_state") == "ignored" for r in prem_good)
            and all(r.get("reasoning_effort_state") == "accepted" for r in prem_accepts)
            and all(r.get("reasoning_effort_state") == "rejected" for r in prem_rejects)
            and all(r["valid"] is False and r["attempts"] == 1 for r in prem_rejects)
        )
        print("\nSELF-CHECK", "PASSED" if ok else "FAILED")
        return 0 if ok else 1

    if args.offline:
        report = render_report(None)
        target = args.report or DEFAULT_REPORT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
        print(f"wrote {target}")
        return 0

    if args.results:
        results = json.loads(args.results.read_text(encoding="utf-8"))
        report = render_report(results)
        target = args.report or DEFAULT_REPORT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
        print(f"wrote {target}")
        return 0

    evidence = load_evidence()
    default_models = ",".join(c["slug"] for c in evidence["candidates"])
    models = (
        args.models or os.environ.get("BAKEOFF_MODELS") or default_models
    ).split(",")
    models = [m.strip() for m in models if m.strip()]
    base_url = os.environ.get("BAKEOFF_BASE_URL", "https://integrate.api.nvidia.com/v1")
    api_key = os.environ.get("BAKEOFF_API_KEY", "")
    if not api_key:
        print("error: BAKEOFF_API_KEY is required for a live run", file=sys.stderr)
        return 2
    reasoning_effort = (
        args.reasoning_effort
        or os.environ.get("BAKEOFF_REASONING_EFFORT", "none")
    )
    if reasoning_effort not in ("none", "high"):
        print(
            f"error: BAKEOFF_REASONING_EFFORT must be none or high, got {reasoning_effort!r}",
            file=sys.stderr,
        )
        return 2
    fixtures = load_fixtures(args.fixtures_dir)
    fixture_ids = (
        [f.strip() for f in args.fixtures.split(",") if f.strip()]
        if args.fixtures
        else None
    )
    if reasoning_effort == "high":
        # The premium pass is the two selection fixtures only.
        premium_ids = {"japan_selection", "tjr_selection"}
        if fixture_ids is not None and not set(fixture_ids) <= premium_ids:
            print(
                "error: --reasoning-effort high runs the selection fixtures "
                "only; --fixtures must be a subset of japan_selection,tjr_selection",
                file=sys.stderr,
            )
            return 2
        fixture_ids = sorted(premium_ids)
    results = run_bakeoff(
        models=models,
        base_url=base_url,
        api_key=api_key,
        fixtures=fixtures,
        fixture_ids=fixture_ids,
        timeout=float(os.environ.get("BAKEOFF_TIMEOUT", "300")),
        temperature=float(os.environ.get("BAKEOFF_TEMPERATURE", "0")),
        reasoning_effort=reasoning_effort if reasoning_effort == "high" else None,
    )
    table = render_table(results)
    print(table)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "results.json").write_text(
            json.dumps(results, indent=1), encoding="utf-8"
        )
        (args.out / "table.md").write_text(table + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}/results.json and table.md")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(results), encoding="utf-8")
        print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
