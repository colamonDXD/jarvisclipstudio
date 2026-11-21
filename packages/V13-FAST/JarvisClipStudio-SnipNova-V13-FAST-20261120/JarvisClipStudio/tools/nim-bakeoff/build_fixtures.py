#!/usr/bin/env python3
"""Regenerate the NIM bake-off fixtures from their in-tree sources.

Every fixture is DERIVED, never hand-written, so the bake-off always
runs on the same evidence the app itself is tested against:

- japan_selection.json  - the selection gold set's transcript-grounded
  ranges. The only Japan transcript evidence in-tree is the range
  transcript cached next to the validation source video
  (~/workspace/qm-source/source.transcript_cache.json, utterances for
  the g1/g3/g4 ranges). Lines are produced by the app's own
  ``format_transcript_for_analysis``. If the cache is absent, the
  builder falls back to each gold window's ``transcript_excerpt``.
- tjr_selection.json    - TJR's real 28.6 s clip (TJR_WORDS in
  backend/tests/unit/test_broll_word_sense.py, a faster-whisper word
  list), grouped into utterance lines and formatted by the same app
  formatter. Unlabelled: sanity metrics only, no gold agreement.
- japan_plan.json       - the g1 segment (travel_vlog profile, real
  word timings from the cached utterances, rebased to clip start).
- tjr_plan.json         - the TJR clip (finance_business profile, real
  word timings from TJR_WORDS).
- classify_cases.json   - the real niche cases from the classifier
  guardrail suites: TJR "Week in the Life" (expected vlog_lifestyle),
  TJR "$10M Trader" (expected finance_business, full transcript kept
  for the guardrail evidence sample), the Japan restaurants source
  (expected travel_vlog, raw-model scoring only - the production
  travel guardrail needs visual evidence this harness does not have),
  and a podcast control (expected podcast_news).
- requirements_focus.json - the content-requirements focus pass
  (AI_MODEL_BAKEOFF.md §2.3, fixture B): the real Expedia brief from
  the extraction suite (EXPEDIA_TEXT) run through the app's own
  deterministic extractor + validator, judged over two real candidate
  windows from the compliance suite (EXPEDIA_LINES). Expected
  verdicts are the app's own deterministic evaluator, not hand-written.
- tjr_longform_selection.json - long-form chunked analysis
  (AI_MODEL_BAKEOFF.md §2.4, fixture C): the full REAL_10M_TRANSCRIPT
  (TJR day-in-the-life, 24,033 chars) as one windowed selection input.
  The Japan cache cannot serve this role: it holds only 36 utterances
  (1,205 chars) and japan_selection.json already IS the whole of it,
  so a "concatenated Japan" fixture would be byte-identical to the core
  fixture. No JRE transcript exists in-tree either (gold set: JRE
  unlabelled, no transcript). The transcript words are verbatim from
  the test constant; line timestamps are ESTIMATED at 150 wpm from word
  count (documented in the fixture) because the constant carries no
  timings. Unlabelled: sanity metrics only, no gold agreement.

Test-file constants are extracted with ``ast.literal_eval`` - the
test modules are never executed.

Usage:  python3 build_fixtures.py [--qm-source ~/workspace/qm-source]
Writes into ./fixtures/ next to this script. Requires the backend
venv python (it imports src.media.transcription for the formatter).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "backend"
GOLD_SET = REPO / "tools" / "selection-eval" / "gold_set.json"
PROFILES = BACKEND / "src" / "auto_director" / "profiles"
FIXTURES = HERE / "fixtures"

sys.path.insert(0, str(BACKEND))

from src.media.transcription import format_transcript_for_analysis  # noqa: E402
from src.auto_director.content_requirements import (  # noqa: E402
    evaluate_candidate_requirements,
    extract_brief_deterministically,
    validate_content_brief,
)
from src.auto_director.content_requirements_llm import (  # noqa: E402
    PERMITTED_ALIASES,
)


def extract_constant(py_file: Path, name: str):
    """Pull a module-level literal constant out of a test file via AST."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                return ast.literal_eval(node.value)
    raise KeyError(f"{name} not found in {py_file}")


def format_lines(segments: list[dict]) -> str:
    """Run segment dicts through the app's own analysis formatter."""
    return "\n".join(format_transcript_for_analysis({"segments": segments}))


def load_japan_cache(qm_source: Path) -> list[dict] | None:
    cache = qm_source / "source.transcript_cache.json"
    if not cache.exists():
        return None
    doc = json.loads(cache.read_text(encoding="utf-8"))
    return doc.get("utterances") or None


def build_japan_selection(qm_source: Path) -> dict:
    gold = json.loads(GOLD_SET.read_text(encoding="utf-8"))
    golds = [w for w in gold["windows"] if w.get("kind") == "gold"]
    utterances = load_japan_cache(qm_source)
    segments: list[dict] = []
    covered: list[str] = []
    if utterances:
        for utt in utterances:
            segments.append(
                {
                    "start": utt["start"] / 1000.0,
                    "end": utt["end"] / 1000.0,
                    "text": (utt.get("text") or "").strip(),
                }
            )
        clusters: list[tuple[float, float]] = []
        for seg in segments:
            if clusters and seg["start"] - clusters[-1][1] < 5.0:
                clusters[-1] = (clusters[-1][0], seg["end"])
            else:
                clusters.append((seg["start"], seg["end"]))
        for g in golds:
            # 1.5 s tolerance: gold ends are rounded to whole seconds,
            # the last utterance of a range can end a hair earlier.
            if any(
                c0 <= g["start"] + 1.0 and g["end"] - 1.5 <= c1 for c0, c1 in clusters
            ):
                covered.append(g["id"])
        provenance = (
            "Transcript lines formatted by the app's "
            "format_transcript_for_analysis from the range transcript "
            "cached at ~/workspace/qm-source/source.transcript_cache.json "
            "(faster-whisper utterances, times in ms)."
        )
    else:
        for g in golds:
            if g.get("transcript_excerpt"):
                segments.append(
                    {"start": g["start"], "end": g["end"], "text": g["transcript_excerpt"]}
                )
                covered.append(g["id"])
        provenance = (
            "qm-source transcript cache absent: fell back to the gold "
            "windows' transcript_excerpt fields in gold_set.json."
        )
    return {
        "id": "japan_selection",
        "kind": "selection",
        "source_id": "japan-restaurants-2026-11-09",
        "gold_ids_covered": covered,
        "transcript": format_lines(segments),
        "notes": (
            "Agreement is scored ONLY on the covered golds "
            f"({', '.join(covered)}) and their traps: those are the "
            "ranges with real transcript evidence in-tree. g2/g5 have "
            "no cached transcript (gold_set.json marks them "
            "transcript_grounded=false) and are excluded rather than "
            "invented. " + provenance
        ),
    }


def build_tjr_selection() -> dict:
    words = extract_constant(
        BACKEND / "tests" / "unit" / "test_broll_word_sense.py", "TJR_WORDS"
    )
    segments: list[dict] = []
    current: list[tuple[str, float]] = []
    prev_start = None
    for word, start in words:
        # Group into utterance-like lines: break on sentence-ending
        # punctuation or a long pause between word starts.
        if current and prev_start is not None and (
            current[-1][0].endswith((".", "?", "!")) or start - prev_start > 1.4
        ):
            segments.append(
                {
                    "start": current[0][1],
                    "end": prev_start + 0.4,
                    "text": " ".join(w for w, _ in current),
                }
            )
            current = []
        current.append((word, start))
        prev_start = start
    if current:
        segments.append(
            {
                "start": current[0][1],
                "end": prev_start + 0.4,
                "text": " ".join(w for w, _ in current),
            }
        )
    return {
        "id": "tjr_selection",
        "kind": "selection",
        "source_id": None,
        "gold_ids_covered": [],
        "transcript": format_lines(segments),
        "notes": (
            "TJR's real 28.6 s trading clip (faster-whisper word list, "
            "golden fixture of the B-roll word-sense suite). Unlabelled "
            "for selection: schema validity, grounding and title "
            "checks only - no gold agreement is claimed."
        ),
    }


def _profile(niche: str) -> dict:
    return json.loads((PROFILES / f"{niche}.json").read_text(encoding="utf-8"))


def build_japan_plan(qm_source: Path) -> dict:
    gold = json.loads(GOLD_SET.read_text(encoding="utf-8"))
    g1 = next(w for w in gold["windows"] if w.get("id") == "g1")
    utterances = load_japan_cache(qm_source)
    words: list[dict] = []
    texts: list[str] = []
    if utterances:
        for utt in utterances:
            if utt["start"] / 1000.0 < g1["start"] or utt["end"] / 1000.0 > g1["end"]:
                continue
            texts.append((utt.get("text") or "").strip())
            for w in utt.get("words") or []:
                words.append(
                    {
                        "word": (w.get("text") or "").strip(),
                        "start": round(w["start"] / 1000.0 - g1["start"], 3),
                        "end": round(w["end"] / 1000.0 - g1["start"], 3),
                    }
                )
    if not words:  # cache fallback: no word timings available
        texts = [g1.get("transcript_excerpt") or ""]
    return {
        "id": "japan_plan",
        "kind": "plan",
        "niche": "travel_vlog",
        "profile": _profile("travel_vlog"),
        "duration": float(g1["end"] - g1["start"]),
        "text": " ".join(texts),
        "words": words,
        "audio_features": {"energy_peaks": [3.2, 12.7], "pauses": [{"start": 9.8, "end": 10.4}]},
        "selection_scores": {"relevance_score": 0.92, "total_score": g1.get("as_run_score", 92)},
        "notes": (
            "The g1 gold segment (avocado / 700-year-old tree), the "
            "run's TOP PICK. Word timings are the real cached "
            "faster-whisper words rebased to clip start; audio "
            "features are fixture stand-ins (peaks/pauses are not "
            "cached in-tree) and are identical for every model."
        ),
    }


def build_tjr_plan() -> dict:
    words_raw = extract_constant(
        BACKEND / "tests" / "unit" / "test_broll_word_sense.py", "TJR_WORDS"
    )
    words = []
    for i, (word, start) in enumerate(words_raw):
        end = words_raw[i + 1][1] if i + 1 < len(words_raw) else start + 0.35
        words.append({"word": word, "start": start, "end": round(end, 3)})
    return {
        "id": "tjr_plan",
        "kind": "plan",
        "niche": "finance_business",
        "profile": _profile("finance_business"),
        "duration": 28.6,
        "text": " ".join(w for w, _ in words_raw),
        "words": words,
        "audio_features": {"energy_peaks": [13.5, 19.0], "pauses": []},
        "selection_scores": {"relevance_score": 0.9, "total_score": 88},
        "notes": (
            "TJR's 28.6 s trading clip under the finance_business "
            "profile it now classifies to. Word timings are the real "
            "faster-whisper starts; each word's end is the next "
            "word's start."
        ),
    }


def build_classify_cases() -> dict:
    guardrails = BACKEND / "tests" / "unit" / "test_auto_director_classify_guardrails.py"
    evidence = BACKEND / "tests" / "unit" / "test_auto_director_classify_evidence_window.py"
    gold = json.loads(GOLD_SET.read_text(encoding="utf-8"))
    japan_excerpts = " ".join(
        w.get("transcript_excerpt") or ""
        for w in gold["windows"]
        if w.get("id") in ("g1", "g3", "g4")
    )
    full_10m = extract_constant(evidence, "REAL_10M_TRANSCRIPT")
    cases = [
        {
            "id": "tjr-week-in-life",
            "title": extract_constant(guardrails, "TJR_TITLE"),
            "transcript_sample": extract_constant(guardrails, "TJR_TRANSCRIPT"),
            "full_transcript": None,
            "expected": "vlog_lifestyle",
            "notes": "Head sample in the exact shape the pipeline feeds the classifier.",
        },
        {
            "id": "tjr-10m-trader",
            "title": extract_constant(evidence, "REAL_10M_TITLE"),
            "transcript_sample": full_10m[:2000],
            "full_transcript": full_10m,
            "expected": "finance_business",
            "notes": (
                "Model sees the same 2,000-char head sample as "
                "production; the guardrail pass additionally scores "
                "the widened evidence sample over the full transcript."
            ),
        },
        {
            "id": "japan-restaurants",
            "title": "CORE $1 VS $10,000 RESTAURANTS IN JAPAN",
            "transcript_sample": japan_excerpts,
            "full_transcript": None,
            "expected": "travel_vlog",
            "guardrail": False,
            "notes": (
                "Scored on the RAW model answer: the production "
                "travel guardrail also needs visual evidence, which "
                "this harness does not have, so no corrected score "
                "is claimed for this case."
            ),
        },
        {
            "id": "podcast-control",
            "title": extract_constant(guardrails, "PODCAST_TITLE"),
            "transcript_sample": extract_constant(guardrails, "PODCAST_TRANSCRIPT"),
            "full_transcript": None,
            "expected": "podcast_news",
            "notes": "Control: a genuine studio podcast must stay podcast_news.",
        },
    ]
    return {"id": "classify_cases", "kind": "classify", "cases": cases}


def extract_expedia_lines() -> list[dict]:
    """Pull EXPEDIA_LINES out of the compliance suite via AST.

    The value is ``_lines([...] + [...])``: two list comprehensions
    over ``range()`` producing (second, text) pairs. The comprehensions
    are evaluated structurally (range args + text literals only) - the
    test module is never executed.
    """
    path = BACKEND / "tests" / "unit" / "test_content_requirements_compliance.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == "EXPEDIA_LINES"):
                continue
            call = node.value
            if not isinstance(call, ast.Call) or not call.args:
                raise ValueError(f"unexpected EXPEDIA_LINES shape in {path}")
            expr = call.args[0]
            if not (isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add)):
                raise ValueError(f"unexpected EXPEDIA_LINES shape in {path}")
            lines: list[dict] = []
            for comp in (expr.left, expr.right):
                if not isinstance(comp, ast.ListComp):
                    raise ValueError(f"unexpected EXPEDIA_LINES shape in {path}")
                gen = comp.generators[0]
                rargs = [ast.literal_eval(a) for a in gen.iter.args]
                start, stop = rargs[0], rargs[1]
                step = rargs[2] if len(rargs) > 2 else 1
                text = ast.literal_eval(comp.elt.elts[1])
                for s in range(start, stop, step):
                    lines.append(
                        {
                            "start_seconds": float(s),
                            "end_seconds": float(s + 10),
                            "text": text,
                        }
                    )
            return lines
    raise KeyError(f"EXPEDIA_LINES not found in {path}")


def build_requirements_focus() -> dict:
    """Fixture B: the content-requirements focus pass.

    Brief: the real Expedia brief (EXPEDIA_TEXT, extraction suite)
    through the app's own deterministic extractor + validator.
    Candidates: the two real windows from the compliance suite
    (EXPEDIA_LINES) - 180-230 s is Expedia-heavy, 230-480 s is generic
    chatter. Candidate text is the window's DISTINCT lines in order
    (the suite repeats one line per 10 s step; the repetition is a
    test-construction artefact, not speech). Expected verdicts are the
    app's own deterministic evaluator over the full line list - never
    hand-written.
    """
    brief_text = extract_constant(
        BACKEND / "tests" / "unit" / "test_content_requirements_extract.py",
        "EXPEDIA_TEXT",
    )
    lines = extract_expedia_lines()
    brief = validate_content_brief(extract_brief_deterministically(brief_text))
    candidates: list[dict] = []
    expected: dict[str, dict[str, dict[str, str]]] = {}
    for cid, (start, end) in enumerate([(180.0, 230.0), (230.0, 480.0)]):
        window_lines = [
            line
            for line in lines
            if line["start_seconds"] >= start and line["end_seconds"] <= end
        ]
        distinct: list[str] = []
        for line in window_lines:
            if line["text"] not in distinct:
                distinct.append(line["text"])
        candidates.append(
            {
                "candidate_id": cid,
                "text": " ".join(distinct),
                "start_seconds": start,
                "end_seconds": end,
            }
        )
        verdict = evaluate_candidate_requirements(
            lines, start, end, brief, llm_available=False
        )
        expected[str(cid)] = {
            entry["id"]: {"focus": entry["focus"], "status": entry["status"]}
            for entry in verdict["requirements"]
        }
    return {
        "id": "requirements_focus",
        "kind": "requirements",
        "brief": brief,
        "candidates": candidates,
        "expected": expected,
        "permitted_aliases": sorted(PERMITTED_ALIASES),
        "notes": (
            "The content-requirements focus pass "
            "(backend/src/auto_director/content_requirements_llm.py): "
            "PERMITTED_ALIASES are exactly clip-assured, groq-20b, "
            "local-qwen. Brief and candidate windows are the real "
            "Expedia fixtures from the app's tests (see builder); "
            "expected verdicts are the app's own deterministic "
            "evaluator (candidate 0 major/satisfied, candidate 1 "
            "absent/failed). The harness grades a response with the "
            "pass's own FocusJudgements schema plus the app's own "
            "_merge_llm_judgement verbatim-evidence gate: invented "
            "evidence is discarded, not scored."
        ),
    }


# Estimated speaking rate for the long-form fixture (fixture C). The
# REAL_10M_TRANSCRIPT constant carries words but no timings, so line
# timestamps are estimated from word count at this rate. The words
# themselves are verbatim; the times are estimates, never measured.
LONGFORM_WORDS_PER_MINUTE = 150.0


def build_tjr_longform_selection() -> dict:
    """Fixture C: long-form chunked analysis (one windowed input).

    Source: the full REAL_10M_TRANSCRIPT (TJR 'What a $10M Trader
    Actually Does in a Day', evidence-window suite) - 24,033 chars,
    the longest real transcript in-tree. Sentences are accumulated
    into ~50-word segments (~20 s at 150 wpm) and formatted by the
    app's own format_transcript_for_analysis. Graded with the same
    builders, schema and scorer as japan_selection, but unlabelled:
    sanity metrics only (schema validity, grounding, title checks),
    no gold agreement is claimed.
    """
    transcript = extract_constant(
        BACKEND
        / "tests"
        / "unit"
        / "test_auto_director_classify_evidence_window.py",
        "REAL_10M_TRANSCRIPT",
    ).strip()
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", transcript) if s.strip()
    ]
    grouped: list[str] = []
    current: list[str] = []
    for sent in sentences:
        words = sent.split()
        if current and len(current) + len(words) > 50:
            grouped.append(" ".join(current))
            current = list(words)
        else:
            current.extend(words)
    if current:
        grouped.append(" ".join(current))
    words_per_second = LONGFORM_WORDS_PER_MINUTE / 60.0
    segments: list[dict] = []
    pos = 0
    for text in grouped:
        count = len(text.split())
        segments.append(
            {
                "start": round(pos / words_per_second, 3),
                "end": round((pos + count) / words_per_second, 3),
                "text": text,
            }
        )
        pos += count
    total_words = sum(len(seg["text"].split()) for seg in segments)
    return {
        "id": "tjr_longform_selection",
        "kind": "selection",
        "source_id": None,
        "gold_ids_covered": [],
        "transcript": format_lines(segments),
        "timestamp_basis": (
            f"estimated at {LONGFORM_WORDS_PER_MINUTE:.0f} wpm from word "
            "count; the transcript constant carries no timings"
        ),
        "word_count": total_words,
        "estimated_duration_sec": round(total_words / words_per_second, 1),
        "notes": (
            "Long-form chunked-analysis input: the full TJR $10M "
            "day-in-the-life transcript (24,033 chars, "
            f"{total_words} words, ~{len(segments)} lines, "
            f"~{round(total_words / words_per_second)} s estimated) as "
            "ONE windowed selection input, graded against no golds "
            "(sanity metrics only). It exercises what the excerpt "
            "fixtures cannot: holding coherence across a long window "
            "without drifting, repeating, or picking outside the "
            "transcript. Timestamps are ESTIMATED (see "
            "timestamp_basis) - absolute times are not production "
            "times, but line alignment and grounding are still "
            "measured. See the builder docstring for why the Japan "
            "cache and the JRE could not serve this role."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--qm-source", type=Path, default=Path.home() / "workspace" / "qm-source")
    args = ap.parse_args()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    built = {
        "japan_selection.json": build_japan_selection(args.qm_source),
        "tjr_selection.json": build_tjr_selection(),
        "japan_plan.json": build_japan_plan(args.qm_source),
        "tjr_plan.json": build_tjr_plan(),
        "classify_cases.json": build_classify_cases(),
        "requirements_focus.json": build_requirements_focus(),
        "tjr_longform_selection.json": build_tjr_longform_selection(),
    }
    for name, doc in built.items():
        (FIXTURES / name).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        print(f"wrote fixtures/{name} ({len(json.dumps(doc))} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
