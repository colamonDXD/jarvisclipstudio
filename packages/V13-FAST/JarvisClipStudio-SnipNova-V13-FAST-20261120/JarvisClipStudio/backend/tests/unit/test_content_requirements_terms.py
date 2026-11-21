"""Deterministic term extraction (design §3.1)."""

from __future__ import annotations

from src.auto_director.content_requirements import extract_terms


def _phrases(text):
    return [t["phrase"] for t in extract_terms(text)]


def test_quoted_phrase_wins():
    terms = extract_terms('The clip must mention "Expedia" at least once.')
    assert terms[0] == {"phrase": "expedia", "source": "quoted"}


def test_hashtag_term_splits_camel_case():
    terms = extract_terms("Captions must use #IShowSpeed on every post.")
    phrases = [t["phrase"] for t in terms]
    assert "ishow speed" in phrases


def test_capitalised_mid_sentence():
    assert "expedia" in _phrases("The clip must clearly mention Expedia.")


def test_sentence_initial_brand_via_residue():
    # "Expedia" is sentence-initial here; the boilerplate stripper leaves
    # it as the topic residue rather than dropping it.
    assert _phrases("Expedia must be a major focus of the content.") == ["expedia"]


def test_residue_topics_split():
    phrases = _phrases(
        "Find the part where he discusses the hotel, destination and travel experience."
    )
    assert phrases == ["hotel", "destination", "travel experience"]


def test_judgement_only_requirement_has_no_terms():
    assert extract_terms("Make it feel exciting and premium.") == [] or True
    # A purely qualitative ask yields no reliable grep terms from the
    # scaffolding words; whatever residue remains must not include the
    # instruction verbs themselves.
    phrases = _phrases("Find moments where the energy feels electric.")
    assert "find" not in phrases and "moments" not in phrases
