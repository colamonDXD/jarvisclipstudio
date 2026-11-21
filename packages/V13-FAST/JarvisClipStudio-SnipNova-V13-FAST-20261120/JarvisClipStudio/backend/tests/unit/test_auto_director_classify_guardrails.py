"""Guardrail extension tests for the P0 auto-niche fix (2026-11-10).

Two properties, both pinned here:

1. The guardrails run on the FAILURE path too: when every candidate
   fails, the marked fallback still passes through
   apply_evidence_guardrails with whatever evidence exists, so strong
   title/transcript evidence can rescue the niche instead of the task
   silently wearing the podcast profile. A rescue keeps is_fallback
   True — the model never answered — and says so in its reasons.

2. The guardrails reach beyond the travel case with CONSERVATIVE
   title/transcript scorers for gaming, sports, finance_business,
   music_artist and meme_comedy. Positive AND negative cases per
   scorer; the negatives are the point: a genuine podcast (interview
   cues, talking-head evidence, or a podcast-shaped title) is NEVER
   overridden, and weak single-signal evidence never fires either.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.auto_director.classify as classify_module
from src.auto_director.classify import (
    NICHE_CLASSIFICATION_SYSTEM_PROMPT,
    _apply_failure_path_guardrails,
    _evidence_guardrail_rescue,
    _fallback_result,
    _hindi_evidence_score,
    _vlog_format_score,
    apply_evidence_guardrails,
    classify_niche,
)

GAMING_TRANSCRIPT = (
    "Welcome back to the ranked grind. In this gameplay breakdown we "
    "speedrun the new raid, clutch the final boss, and test the patch "
    "notes loadout. My K/D this season is unreal."
)
GAMING_TITLE = "RANKED GRIND — New Raid Speedrun Gameplay"

SPORTS_TRANSCRIPT = (
    "What a match. The striker scored twice, the second goal in stoppage "
    "time, and the keeper saved a penalty to keep the clean sheet. The "
    "title race is wide open after this derby."
)
SPORTS_TITLE = "Derby Day Highlights — Two Goals & A Penalty Save"

FINANCE_TRANSCRIPT = (
    "Today we break down my index fund portfolio, dividend stocks, and "
    "how compound interest does the heavy lifting. We also look at the "
    "latest earnings report and what it means for your retirement account."
)
FINANCE_TITLE = "Index Funds & Dividends — My Portfolio Breakdown"

MUSIC_TRANSCRIPT = (
    "This is my new single, recorded live. The chorus hits after the "
    "second verse, the beat drops, and the lyrics are all mine. Full "
    "album drops next month."
)
MUSIC_TITLE = "My New Single — Live Performance (Official Audio)"

COMEDY_TRANSCRIPT = (
    "POV: your mum finds your search history. This skit broke me, the "
    "timing is perfect, watch till the end for the plot twist lol."
)
COMEDY_TITLE = "POV Skit — Funniest Meme Of The Week 😂"

PODCAST_TRANSCRIPT = (
    "Welcome back to the show. Today my guest is a founder, and we talk "
    "about his journey, the lessons from his first company, and what he "
    "would do differently. Let's get into the conversation."
)
PODCAST_TITLE = "The Founder Podcast — Episode 142: A Founder's Journey"

ALL_MODELS = (
    "openai:local-qwen",
    "openai:clip-fast",
    "openai:clip-assured",
    "groq:openai/gpt-oss-120b",
    "ollama:qwen3:8b",
)


def _config():
    return SimpleNamespace(
        llm="openai:clip-fast",
        analysis_models=["openai:clip-assured"],
        analysis_fallback_model="groq:openai/gpt-oss-120b",
        analysis_direct_fallback_model="ollama:qwen3:8b",
        provider_mode="auto",
        local_llm="openai:local-qwen",
        provider_manager_enabled=True,
        openai_api_key="test-openai-key",
        openrouter_api_key=None,
        openrouter_fallback_model="anthropic/claude-haiku-4-5",
        google_api_key=None,
        anthropic_api_key=None,
        ollama_base_url="http://localhost:11434",
        ollama_api_key=None,
    )


def _install_all_fail(monkeypatch):
    """Every candidate build fails: classification must fall back —
    and the guardrails must still get their chance."""
    from src import provider_manager as pm

    pm._manager = pm.ProviderManager()
    monkeypatch.setattr(
        pm.ProviderManager, "_schedule_persist", lambda self, coro: coro.close()
    )
    monkeypatch.setattr(classify_module, "get_config", _config)
    monkeypatch.setattr(
        classify_module,
        "get_niche_classification_agent",
        lambda: (_ for _ in ()).throw(AssertionError("unused")),
    )
    monkeypatch.setattr(
        classify_module,
        "get_agent_for_model",
        lambda model, text_output=False: (_ for _ in ()).throw(
            RuntimeError("provider down")
        ),
    )
    monkeypatch.setattr(
        classify_module.provider_manager, "manager_enabled", lambda cfg=None: True
    )
    monkeypatch.setattr(
        classify_module.provider_manager,
        "run_recorded",
        AsyncMock(side_effect=AssertionError("no run without an agent")),
    )


def _fallback(**overrides):
    result = {
        "niche": "podcast_news",
        "confidence": 0.0,
        "reasons": ["fallback"],
        "is_fallback": True,
        "evidence": {"title": None},
        "evidence_status": "partial",
    }
    result.update(overrides)
    return result


class TestScorerRescues:
    @pytest.mark.parametrize(
        "title,transcript,expected",
        [
            (GAMING_TITLE, GAMING_TRANSCRIPT, "gaming"),
            (SPORTS_TITLE, SPORTS_TRANSCRIPT, "sports"),
            (FINANCE_TITLE, FINANCE_TRANSCRIPT, "finance_business"),
            (MUSIC_TITLE, MUSIC_TRANSCRIPT, "music_artist"),
            (COMEDY_TITLE, COMEDY_TRANSCRIPT, "meme_comedy"),
        ],
    )
    def test_strong_evidence_rescues(self, title, transcript, expected):
        result = _evidence_guardrail_rescue(
            _fallback(), title=title, transcript_sample=transcript,
            visual=None, audio=None,
        )
        assert result is not None
        assert result["niche"] == expected
        assert 0.55 <= result["confidence"] <= 0.7
        assert result["evidence"]["guardrail"].startswith(
            "podcast_news_overridden_by_title_transcript_evidence:"
        )

    def test_audio_music_likely_boosts_music(self):
        audio = {
            "tempo_bpm": 128.0, "tempo_strength": 2.6, "beat_count": 64,
            "has_drop": True, "music_likely": True,
            "speech_fraction": 0.15, "speech_dominant": False,
        }
        result = _evidence_guardrail_rescue(
            _fallback(),
            title="Midnight Drive (Official Audio)",
            transcript_sample="yeah yeah, uh, let's go, one more time",
            visual=None, audio=audio,
        )
        assert result is not None
        assert result["niche"] == "music_artist"


class TestScorerNegatives:
    def test_genuine_podcast_transcript_is_never_rescued(self):
        assert _evidence_guardrail_rescue(
            _fallback(), title=PODCAST_TITLE,
            transcript_sample=PODCAST_TRANSCRIPT, visual=None, audio=None,
        ) is None

    def test_interview_about_gaming_stays_podcast(self):
        """Interview cues lock the rescue out even with gaming words."""
        transcript = (
            "Welcome back to the show. My guest today is a pro player, "
            "and in this interview we talk about his gaming career, the "
            "esports grind, and his favourite gameplay memories."
        )
        assert _evidence_guardrail_rescue(
            _fallback(), title="The Podcast — Interview With A Pro Gamer",
            transcript_sample=transcript, visual=None, audio=None,
        ) is None

    def test_talking_head_visual_locks_rescue_out(self):
        visual = {
            "static_talking_head_likely": True,
            "face_presence_rate": 0.95,
            "cuts_per_minute": 0.4,
            "cut_count": 3,
            "mean_motion_spread": 0.1,
        }
        assert _evidence_guardrail_rescue(
            _fallback(), title=GAMING_TITLE,
            transcript_sample=GAMING_TRANSCRIPT, visual=visual, audio=None,
        ) is None

    def test_single_weak_keyword_never_fires(self):
        assert _evidence_guardrail_rescue(
            _fallback(), title="Weekly Vlog",
            transcript_sample="we played a game of chess at the park and talked about our week",
            visual=None, audio=None,
        ) is None

    def test_finance_podcast_title_locks_rescue_out(self):
        """A finance PODCAST (title says so) keeps the podcast profile
        even though its transcript is pure finance vocabulary."""
        assert _evidence_guardrail_rescue(
            _fallback(), title="The Money Podcast — Weekly Episode",
            transcript_sample=FINANCE_TRANSCRIPT, visual=None, audio=None,
        ) is None

    def test_close_scores_do_not_fire(self):
        """Gaming and sports signals together, neither dominant:
        ambiguity is a reason to stay with the fallback, not to guess."""
        transcript = (
            "the match was unreal, two goals and a penalty, then later "
            "in the gameplay segment we hit a boss fight and a speedrun "
            "attempt with a new loadout"
        )
        assert _evidence_guardrail_rescue(
            _fallback(), title="Weekly Watch",
            transcript_sample=transcript, visual=None, audio=None,
        ) is None


class TestThroughClassifyNiche:
    @pytest.mark.asyncio
    async def test_all_fail_strong_gaming_evidence_rescues(self, monkeypatch):
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            GAMING_TRANSCRIPT, [], title=GAMING_TITLE,
        )
        assert result["niche"] == "gaming"
        # Honest marking: no model answered, so this stays a fallback
        # result — with the rescue recorded, not dressed as detection.
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True
        assert any("guardrail" in r for r in result["reasons"])
        from src.auto_director.edl import load_profile

        profile = load_profile(result["niche"])
        # The rescued niche drives the profile — B-roll follows the
        # rescued profile (gaming/comedy: off; finance is on at
        # 1.5/min since Slice 5, d8cb6a6), never the podcast
        # profile's stock B-roll.
        assert profile["broll"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_all_fail_genuine_podcast_stays_podcast_and_marked(
        self, monkeypatch, caplog
    ):
        _install_all_fail(monkeypatch)
        with caplog.at_level(logging.WARNING):
            result = await classify_niche(
                PODCAST_TRANSCRIPT, [], title=PODCAST_TITLE,
            )
        assert result["niche"] == "podcast_news"
        assert result["is_fallback"] is True
        assert result.get("guardrail_rescued") is not True
        assert result["attempts"], "the failed chain must be recorded"
        assert result["last_error"]

    @pytest.mark.asyncio
    async def test_all_fail_travel_rescue_still_works_without_visual(
        self, monkeypatch
    ):
        """The pre-existing travel rule now also runs on the failure
        path via the title/transcript scorers (its visual-gated form
        is unchanged on the success path)."""
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            "Okay so tell me what you think of this one. Honestly the "
            "flavour is insane. Would you pay for this again? A hundred "
            "percent. Let's talk about the price difference.",
            [],
            title="CORE $1 VS $10,000 RESTAURANTS IN JAPAN",
        )
        assert result["niche"] == "travel_vlog"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True


class TestSuccessPathUnchanged:
    def test_apply_guardrails_ignores_non_fallback_results(self):
        """The new scorers never touch a real model answer: they exist
        for the fallback path only (the travel rule's success-path
        behaviour is pinned by the evidence suite)."""
        result = {
            "niche": "podcast_news",
            "confidence": 0.9,
            "reasons": ["model said so"],
            "is_fallback": False,
            "evidence": {},
        }
        out = apply_evidence_guardrails(
            result, title=GAMING_TITLE,
            transcript_sample=GAMING_TRANSCRIPT, visual=None, audio=None,
        )
        assert out["niche"] == "podcast_news"
        assert "guardrail" not in out["evidence"]

    def test_prompt_still_requires_json(self):
        assert "Return valid JSON only" in NICHE_CLASSIFICATION_SYSTEM_PROMPT


# --- Hindi guardrail (self-run audit #2, finding A2-1) -----------------------
#
# The failure path had NO hindi_podcast scorer at all: Hindi content
# always kept the podcast_news fallback — with a podcast-flavoured
# title the lock barred rescue, without one nothing scored it. The
# scorer added is language-based, not keyword-based: Devanagari
# dominance (hinglish.py's own >= 0.30 share threshold) is the
# objective signal and may override the podcast lock; romanized
# Hinglish markers are keyword-class evidence and may not. The two
# Hindi transcripts and the control below are the audit's own
# fixtures (selfrun2-work/leg1_classify.py), pinned verbatim.

HINDI_PODCAST_TITLE = "पैसा कैसे सोचता है | Hindi Podcast"
HINDI_PODCAST_TRANSCRIPT = (
    "दोस्तों आज हम बात करेंगे कि पैसा असल में काम कैसे करता है। देखो, अमीर लोग "
    "पैसे को नौकर बनाते हैं और ग़रीब लोग पैसे के नौकर बन जाते हैं। मेरा एक दोस्त "
    "था, उसने छोटी सी दुकान से शुरुआत की थी और आज उसका व्यापार पूरे शहर में "
    "फैला है। उसने मुझे एक बात सिखाई — समय सबसे बड़ी पूंजी है। अगर तुम अपनी "
    "आदतें बदल लो तो ज़िंदगी बदल जाएगी। मेहनत का कोई shortcut नहीं होता, "
    "yaar यह बात समझ लो। आज की बातचीत में हम यही समझेंगे कि सोच कैसे बदलती है "
    "और सफलता कैसे मिलती है।"
)

HINDI_BUSINESS_TITLE = "व्यापार की असली सच्चाई — जो कोई नहीं बताता"
HINDI_BUSINESS_TRANSCRIPT = (
    "दोस्तों, व्यापार में सबसे बड़ी गलती यह होती है कि लोग जल्दी अमीर बनना "
    "चाहते हैं। मेरा अपना अनुभव यह कहता है कि धीरे-धीरे बढ़ने वाला काम ही "
    "टिकता है। गाँव से शहर आया था मैं, जेब में सिर्फ़ दो हज़ार रुपये थे। "
    "पहले साल बहुत मुश्किल थी, लेकिन मैंने हार नहीं मानी। ग्राहक का भरोसा ही "
    "असली कमाई है, यह बात हर नौजवान को समझनी चाहिए। अगर आप भी अपना काम शुरू "
    "करना चाहते हैं तो पहले छोटा सोचो, फिर बड़ा करो। मेहनत और ईमानदारी — "
    "बस यही दो चीज़ें काम आती हैं।"
)

AUDIT_PODCAST_TITLE = "The Mindset Podcast #214 — Habits of Founders"
AUDIT_PODCAST_TRANSCRIPT = (
    "Welcome back to the show. In this episode my guest is a founder who built "
    "three companies before turning thirty. Today's guest joins me to talk about "
    "morning routines, hiring, and what he learned from failure. We get into the "
    "details of his first company and the year everything nearly collapsed. If "
    "you enjoy this episode, share it with a friend."
)

ROMANIZED_HINGLISH_TITLE = "Doston Ke Liye — Ek Sachi Kahani"
ROMANIZED_HINGLISH_TRANSCRIPT = (
    "Doston, aaj main aapko ek sachhi kahani sunata hoon. Mera ek dost tha, "
    "woh hamesha kehta tha ki mehnat ka koi shortcut nahin hota. Kya aap "
    "jaante hain usne kaise apna kaam shuru kiya? Uske paas zyada paisa "
    "nahin tha, bas samajh thi aur bahut saara junoon. Woh kehta tha, bhai, "
    "agar niyat saaf hai to raasta khud banta hai. Aaj wohi dost pure sheher "
    "mein mashhoor hai, kyunki usne kabhi haar nahin maani."
)


class TestHindiScorerMechanics:
    def test_audit_fixtures_are_devanagari_dominant(self):
        """Fixture hygiene: both audit Hindi cases really do cross
        hinglish's dominance threshold (the tests below would be
        meaningless if the fixtures silently rotted below it)."""
        for title, transcript in (
            (HINDI_PODCAST_TITLE, HINDI_PODCAST_TRANSCRIPT),
            (HINDI_BUSINESS_TITLE, HINDI_BUSINESS_TRANSCRIPT),
        ):
            _, dominant, share = _hindi_evidence_score(title, transcript)
            assert dominant is True
            assert share >= 0.30

    def test_threshold_boundary_mirrors_hinglish(self):
        """3 Devanagari letters in 10 letters = exactly 0.30 -> dominant;
        3 in 11 -> not. The boundary IS hinglish's threshold, not a
        second number invented for the classifier."""
        _, dominant_at, share_at = _hindi_evidence_score("", "कखग abcdefg")
        _, dominant_below, share_below = _hindi_evidence_score("", "कखग abcdefgh")
        assert share_at == pytest.approx(0.30)
        assert dominant_at is True
        assert share_below < 0.30
        assert dominant_below is False

    def test_markers_count_distinct_words_only(self):
        """Marker spam is not evidence: the same marker repeated scores
        once, exactly like the keyword scorers' distinct hits."""
        score, dominant, _ = _hindi_evidence_score("", "kya kya kya, sirf kya")
        assert dominant is False
        assert score == 1


class TestHindiRescues:
    def test_audit_variant_a_podcast_titled_hindi_is_rescued(self):
        """The audit's locked variant: title says 'Podcast', transcript
        is Devanagari-dominant. The language signal overrides the lock —
        hindi_podcast is the CORRECT profile for a Hindi podcast."""
        fallback = _fallback_result({}, "no_evidence")
        result = _apply_failure_path_guardrails(
            fallback,
            title=HINDI_PODCAST_TITLE,
            transcript_sample=HINDI_PODCAST_TRANSCRIPT,
            visual=None,
            audio=None,
        )
        assert result["niche"] == "hindi_podcast"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True
        assert 0.55 <= result["confidence"] <= 0.7
        assert result["evidence"]["guardrail"].startswith(
            "podcast_news_overridden_by_title_transcript_evidence:"
        )
        assert result["evidence"]["devanagari_share"] >= 0.30

    def test_audit_variant_b_devanagari_without_podcast_word_is_rescued(self):
        """The audit's unlocked variant: no 'podcast' anywhere, and
        before the fix every rival scored 0 so nothing even attempted
        a rescue."""
        fallback = _fallback_result({}, "no_evidence")
        result = _apply_failure_path_guardrails(
            fallback,
            title=HINDI_BUSINESS_TITLE,
            transcript_sample=HINDI_BUSINESS_TRANSCRIPT,
            visual=None,
            audio=None,
        )
        assert result["niche"] == "hindi_podcast"
        assert result["guardrail_rescued"] is True

    def test_rescue_survives_the_substituted_agent_exclude_shape(self):
        """The patched-agent path calls the rescue directly with
        travel_vlog excluded; hindi must still be a candidate there."""
        result = _evidence_guardrail_rescue(
            _fallback(),
            title=HINDI_BUSINESS_TITLE,
            transcript_sample=HINDI_BUSINESS_TRANSCRIPT,
            visual=None,
            audio=None,
            exclude=frozenset({"travel_vlog"}),
        )
        assert result is not None
        assert result["niche"] == "hindi_podcast"

    def test_romanized_hinglish_markers_rescue_when_unlocked(self):
        """Whisper's minority output shape: Hindi speech transcribed in
        Latin script. No Devanagari at all — the marker signal alone
        (many distinct function words) carries the rescue."""
        _, dominant, share = _hindi_evidence_score(
            ROMANIZED_HINGLISH_TITLE, ROMANIZED_HINGLISH_TRANSCRIPT
        )
        assert share == 0.0 and dominant is False
        result = _evidence_guardrail_rescue(
            _fallback(),
            title=ROMANIZED_HINGLISH_TITLE,
            transcript_sample=ROMANIZED_HINGLISH_TRANSCRIPT,
            visual=None,
            audio=None,
        )
        assert result is not None
        assert result["niche"] == "hindi_podcast"

    @pytest.mark.asyncio
    async def test_all_fail_hindi_business_rescues_end_to_end(self, monkeypatch):
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            HINDI_BUSINESS_TRANSCRIPT, [], title=HINDI_BUSINESS_TITLE,
        )
        assert result["niche"] == "hindi_podcast"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True
        from src.auto_director.edl import load_profile

        assert load_profile(result["niche"])["niche"] == "hindi_podcast"


class TestHindiNegatives:
    def test_audit_control_genuine_english_podcast_stays(self):
        fallback = _fallback_result({}, "no_evidence")
        result = _apply_failure_path_guardrails(
            fallback,
            title=AUDIT_PODCAST_TITLE,
            transcript_sample=AUDIT_PODCAST_TRANSCRIPT,
            visual=None,
            audio=None,
        )
        assert result["niche"] == "podcast_news"
        assert result.get("guardrail_rescued") is not True

    def test_romanized_markers_never_override_the_podcast_lock(self):
        """A romanized-Hinglish PODCAST keeps podcast_news: markers are
        keyword-class evidence, and keyword evidence never passes the
        lock. Only the objective script signal may."""
        transcript = (
            "Welcome back to the show doston. Aaj ke episode mein hum "
            "baat karenge ki paisa kaise kaam karta hai, kyunki samajh "
            "hi sab kuch hai aur mehnat ka koi shortcut nahin hota, "
            "bhaiyon yaar yeh baat bahut important hai."
        )
        assert _evidence_guardrail_rescue(
            _fallback(),
            title="Mera Podcast — Doston Ki Baatein",
            transcript_sample=transcript,
            visual=None,
            audio=None,
        ) is None

    def test_devanagari_light_english_finance_is_not_stolen(self):
        """An English finance talk with a couple of Hindi words and one
        'yaar': share far below 0.30, so finance wins on its own
        evidence and hindi never fires."""
        transcript = (
            FINANCE_TRANSCRIPT + " Ek dost ne kaha, yaar, यह सच है।"
        )
        _, dominant, share = _hindi_evidence_score(FINANCE_TITLE, transcript)
        assert dominant is False
        assert share < 0.30
        result = _evidence_guardrail_rescue(
            _fallback(),
            title=FINANCE_TITLE,
            transcript_sample=transcript,
            visual=None,
            audio=None,
        )
        assert result is not None
        assert result["niche"] == "finance_business"

    def test_isolated_marker_collisions_do_not_fire(self):
        """Precision pin: 'chow mein' contains the marker 'mein', and a
        neighbour named Abhi is not evidence of anything. One distinct
        marker in plain English text scores 1 — nowhere near rescue."""
        transcript = (
            "We ordered chow mein from the place near the station and "
            "ate on the balcony. Abhi, our neighbour, joined us with "
            "his little dog. Later we watched the rain and talked about "
            "our school days and the teachers we still remember."
        )
        score, dominant, _ = _hindi_evidence_score(
            "A Quiet Evening at Home", transcript
        )
        assert dominant is False
        assert score == 1
        assert _evidence_guardrail_rescue(
            _fallback(),
            title="A Quiet Evening at Home",
            transcript_sample=transcript,
            visual=None,
            audio=None,
        ) is None


# --- Vlog-format guardrail (TJR misclassification, 2026-11-12) ---------------
#
# Real source: TJR, "Week in the Life of a Millionaire in Los Angeles"
# — a single-speaker lifestyle vlog (Puerto Rico -> Miami -> Bay Area
# -> LA; the creator says "welcome to another vlog" on camera and the
# spoken content is travel/lifestyle throughout). It classified
# podcast_news on BOTH paths: the travel rule's keywords name
# destination food/travel ("in Los Angeles"/"Miami" are not in its
# list, and the title names a FORMAT, not a place), and the failure
# scorers had no vlog_lifestyle entry at all — every score came back 0.
# The fix is a second evidence guardrail in apply_evidence_guardrails
# (so it runs on the success path and, via _apply_failure_path_guardrails,
# on the failure path too): a declared vlog format corrects the
# podcast pick, barred by the same podcast lock as every other rescue.

TJR_TITLE = "Week in the Life of a Millionaire in Los Angeles"
# Verbatim excerpt of the real transcript's first 2000 characters —
# the exact sample shape the pipeline feeds the classifier.
TJR_TRANSCRIPT = (
    "What is up, boys? Welcome to another vlog. This is kind of going "
    "to be all over the place and I honestly don't even know where "
    "it's going to end up. We are currently in the process of going "
    "to Miami. I'm going to get a haircut. Then, we're going to get "
    "on a flight, go to California, and then I'll probably vlog a "
    "bit of me being back home."
)

_VLOG_ACTIVITY_VISUAL = {
    "sample_count": 24,
    "face_presence_rate": 0.62,
    "multi_face_rate": 0.08,
    "mean_face_count": 0.71,
    "mean_largest_face_area_frac": 0.11,
    "cut_count": 42,
    "cuts_per_minute": 6.4,
    "span_seconds": 394.0,
    "mean_motion_spread": 0.48,
    "static_talking_head_likely": False,
    "scene_variety_high": True,
}

_VLOG_STATIC_VISUAL = {
    "sample_count": 200,
    "face_presence_rate": 0.97,
    "multi_face_rate": 0.9,
    "mean_face_count": 1.9,
    "mean_largest_face_area_frac": 0.12,
    "cut_count": 4,
    "cuts_per_minute": 0.4,
    "span_seconds": 3600.0,
    "mean_motion_spread": 0.12,
    "static_talking_head_likely": True,
    "scene_variety_high": False,
}


def _model_podcast(confidence=0.82):
    return {
        "niche": "podcast_news",
        "confidence": confidence,
        "reasons": ["solo talking-head monologue"],
        "is_fallback": False,
        "evidence": {},
    }


class TestVlogFormatGuardrail:
    def test_vlog_format_score_mechanics(self):
        # The real TJR evidence: life-format phrase (3) + "vlog" said
        # on camera (1). A bare "vlog" title alone stays weak (2).
        assert _vlog_format_score(TJR_TITLE, TJR_TRANSCRIPT) == 4
        assert _vlog_format_score("Day in the Life of a Surgeon", "") == 3
        assert _vlog_format_score("Weekly Vlog", "we talked about our week") == 2
        assert _vlog_format_score("The Founder Podcast", PODCAST_TRANSCRIPT) == 0

    def test_success_path_model_podcast_answer_is_corrected(self):
        """The on-machine shape: the model answered podcast_news for a
        solo-monologue vlog; the declared format corrects it even with
        no visual evidence."""
        out = apply_evidence_guardrails(
            _model_podcast(), title=TJR_TITLE,
            transcript_sample=TJR_TRANSCRIPT, visual=None, audio=None,
        )
        assert out["niche"] == "vlog_lifestyle"
        assert out["is_fallback"] is False
        assert 0.55 <= out["confidence"] <= 0.7
        assert (
            out["evidence"]["guardrail"]
            == "podcast_news_overridden_by_vlog_format_evidence"
        )

    def test_success_path_with_activity_visual_is_corrected(self):
        out = apply_evidence_guardrails(
            _model_podcast(), title=TJR_TITLE,
            transcript_sample=TJR_TRANSCRIPT,
            visual=_VLOG_ACTIVITY_VISUAL, audio=None,
        )
        assert out["niche"] == "vlog_lifestyle"

    def test_failure_path_fallback_is_rescued_and_stays_marked(self):
        out = _apply_failure_path_guardrails(
            _fallback(), title=TJR_TITLE,
            transcript_sample=TJR_TRANSCRIPT, visual=None, audio=None,
        )
        assert out["niche"] == "vlog_lifestyle"
        assert out["is_fallback"] is True
        assert out["guardrail_rescued"] is True

    def test_podcast_cues_lock_the_vlog_rule_out(self):
        """A life-format title over real conversation cues (a guest,
        a show) is a podcast episode ABOUT a life, not a vlog."""
        out = apply_evidence_guardrails(
            _model_podcast(), title="A Week in the Life of a CEO",
            transcript_sample=PODCAST_TRANSCRIPT,
            visual=_VLOG_ACTIVITY_VISUAL, audio=None,
        )
        assert out["niche"] == "podcast_news"
        assert "guardrail" not in out["evidence"]

    def test_podcast_titled_life_format_stays_podcast(self):
        out = _apply_failure_path_guardrails(
            _fallback(),
            title="Day in the Life — The Money Podcast, Episode 3",
            transcript_sample=TJR_TRANSCRIPT, visual=None, audio=None,
        )
        assert out["niche"] == "podcast_news"
        assert out.get("guardrail_rescued") is not True

    def test_static_talking_head_visual_locks_the_vlog_rule_out(self):
        out = apply_evidence_guardrails(
            _model_podcast(), title=TJR_TITLE,
            transcript_sample=TJR_TRANSCRIPT,
            visual=_VLOG_STATIC_VISUAL, audio=None,
        )
        assert out["niche"] == "podcast_news"
        assert "guardrail" not in out["evidence"]

    def test_weak_vlog_title_alone_never_fires(self):
        """The pinned weak-signal discipline, on the success path too:
        'Weekly Vlog' + a chat transcript with no on-camera vlog
        declaration scores 2 < 3."""
        out = apply_evidence_guardrails(
            _model_podcast(), title="Weekly Vlog",
            transcript_sample=(
                "we played a game of chess at the park and talked "
                "about our week"
            ),
            visual=None, audio=None,
        )
        assert out["niche"] == "podcast_news"

    def test_travel_rule_keeps_precedence_over_vlog_rule(self):
        """A title tripping BOTH rules still lands on travel_vlog:
        the older, destination-specific rule runs first."""
        out = apply_evidence_guardrails(
            _model_podcast(),
            title="Japan Food Vlog — Eating My Way Through Tokyo",
            transcript_sample=(
                "Welcome to the vlog. Okay so tell me what you think "
                "of this one. Honestly the flavour is insane. Would "
                "you pay for this again? A hundred percent."
            ),
            visual={
                "sample_count": 200,
                "face_presence_rate": 0.6,
                "multi_face_rate": 0.45,
                "mean_face_count": 1.2,
                "mean_largest_face_area_frac": 0.05,
                "cut_count": 180,
                "cuts_per_minute": 3.0,
                "span_seconds": 3600.0,
                "mean_motion_spread": 0.52,
                "static_talking_head_likely": False,
                "scene_variety_high": True,
            },
            audio=None,
        )
        assert out["niche"] == "travel_vlog"
        assert (
            out["evidence"]["guardrail"]
            == "podcast_news_overridden_by_title_and_visual_evidence"
        )

    @pytest.mark.asyncio
    async def test_all_fail_tjr_rescues_end_to_end(self, monkeypatch):
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            TJR_TRANSCRIPT, [], title=TJR_TITLE,
        )
        assert result["niche"] == "vlog_lifestyle"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True
        from src.auto_director.edl import load_profile

        assert load_profile(result["niche"])["niche"] == "vlog_lifestyle"


# ---------------------------------------------------------------------------
# The TJR trading case (2026-11-12, niche correction #2): "What a $10M
# Trader Actually Does in a Day" — a single-speaker TRADING monologue
# (funded accounts, P&L, strategy) stamped Podcast & news on every clip.
# Two stacked gaps, both measured on the real transcript:
#
# * Success path: apply_evidence_guardrails had NO finance correction
#   at all — the same structural hole the vlog rule closed.
# * Both paths: the finance scorer's keyword list spoke INVESTING
#   (portfolio, dividends, index funds) while TJR speaks DAY TRADING.
#   2,500 chars of his pure trading speech scored 1 (only "trading");
#   the committed 96-word clip transcript scored 0.
#
# The fix mirrors the vlog rule: the shared finance scorer learns the
# day-trading dialect (the failure-path rescue then sees it too), and a
# success-path correction — barred by the same podcast lock, the same
# visual-activity requirement, and the rescue's own strength standard
# (>= 4 distinct hits AND >= 2 over every rival) — overrides a podcast
# answer the evidence contradicts. One honest limit, pinned below: the
# classifier's evidence window is the transcript's first 2,000 chars,
# and THIS video opens with a morning routine — its window scores 2
# (the title's "Trader" alone), under the floor, so the correction
# cannot fire for it until the evidence window itself widens. That is
# a separate change; lowering the floor to chase it would break the
# precision every other fixture here pins.

TRADING_TITLE = "What a $10M Trader Actually Does in a Day"
# Verbatim excerpts of the real transcript's trading section.
TRADING_TRANSCRIPT = (
    "Okay, so today we did end up losing both trades. I ended up "
    "losing 52,835 bucks. Because in trading, your profit margins are "
    "100% besides the small little fees that you pay for different "
    "sort of subscriptions like your Trading View subscription and "
    "whatever the brokerage fees that you're trading are on. So the "
    "only losses in expenses that you are actually taking are going "
    "to be the losses that you take throughout the month."
)
FUNDED_TRANSCRIPT = (
    "The same thing as you guys when you guys are trading on these "
    "funded accounts. You guys buy a funded account, you guys might "
    "pass the eval. You guys might even be like one or two days away "
    "from getting a payout, but then you guys you fail that funded "
    "account. Because if we focus on the individual outcomes of every "
    "single day within trading, we're never going to be a winner."
)
# Verbatim excerpt of the real transcript's OPENING — the actual
# 2,000-char evidence window the pipeline feeds the classifier for
# this video: a morning routine, zero trading words.
PRODUCTION_OPENING = (
    "Good morning, guys. It's like 6:00 a.m. This is an early wake "
    "up. I'm trying to slowly but surely be able to wake up at like "
    "5:30, 5:00 a.m. I usually wake up like anywhere from like 6 to "
    "7 a.m. every single morning. But today, I'm going to bring you "
    "guys through little day in my life, a little behind the scenes "
    "of everything that I got going on."
)
# The real 28.6 s clip transcript (the B-roll lane's golden fixture):
# pure trading speech, but 96 words is under the density floor.
TJR_CLIP_TRANSCRIPT = (
    "trade worth $96,000 in profit. This loss today or these two loss "
    "today are actually helping me get closer to my next win that I "
    "know it sounds stupid but I'm due to my probabilities within the "
    "market. I know that 10 times out of 10 when I execute on my "
    "strategy I have higher odds of winning than losing and I know "
    "that when I win my winners are going to be larger than my losers "
    "and that's all that I care about. So whenever my strategy "
    "presents itself I'm taking that fucking trade"
)
FINANCE_PODCAST_TITLE = "How Trading Really Works — A Fund Manager Explains"
FINANCE_PODCAST_TRANSCRIPT = (
    "Welcome back to the show. My guest today runs a trading desk. "
    "In this episode we break down his stop loss rules, how he sizes "
    "a take profit, and why most funded account traders blow up "
    "before their first payout. Let's get into the conversation."
)


class TestFinanceEvidenceGuardrail:
    def test_finance_scorer_sees_the_day_trading_dialect(self):
        from src.auto_director.classify import _NICHE_KEYWORDS, _keyword_score

        kws = _NICHE_KEYWORDS["finance_business"]
        # Real trading speech now reaches the rescue's strength floor.
        assert _keyword_score(kws, TRADING_TITLE, TRADING_TRANSCRIPT) >= 4
        assert _keyword_score(kws, TRADING_TITLE, FUNDED_TRANSCRIPT) >= 4
        # The vlog sibling's transcript carries no finance evidence:
        # the finance rule must never steal the Week-in-the-Life case.
        assert _keyword_score(kws, TJR_TITLE, TJR_TRANSCRIPT) == 0
        # 96 words of clip speech alone stay under the floor (2: the
        # P&L pair) — density, not a single hot word, is the evidence.
        assert _keyword_score(kws, None, TJR_CLIP_TRANSCRIPT) < 4

    def test_success_path_trading_monologue_is_corrected(self):
        """The on-machine shape: the model answered podcast_news for a
        solo trading monologue; strong finance evidence corrects it
        even with no visual evidence."""
        out = apply_evidence_guardrails(
            _model_podcast(), title=TRADING_TITLE,
            transcript_sample=TRADING_TRANSCRIPT, visual=None, audio=None,
        )
        assert out["niche"] == "finance_business"
        assert out["is_fallback"] is False
        assert 0.55 <= out["confidence"] <= 0.7
        assert (
            out["evidence"]["guardrail"]
            == "podcast_news_overridden_by_finance_evidence"
        )

    def test_success_path_with_activity_visual_is_corrected(self):
        out = apply_evidence_guardrails(
            _model_podcast(), title=TRADING_TITLE,
            transcript_sample=TRADING_TRANSCRIPT,
            visual=_VLOG_ACTIVITY_VISUAL, audio=None,
        )
        assert out["niche"] == "finance_business"

    def test_failure_path_funded_monologue_rescues_and_stays_marked(self):
        """The failure path keeps its own rescue — the dialect words
        now let it see day-trading speech it scored 1 on before."""
        out = _apply_failure_path_guardrails(
            _fallback(), title=TRADING_TITLE,
            transcript_sample=FUNDED_TRANSCRIPT, visual=None, audio=None,
        )
        assert out["niche"] == "finance_business"
        assert out["is_fallback"] is True
        assert out["guardrail_rescued"] is True

    @pytest.mark.asyncio
    async def test_all_fail_trading_monologue_rescues_end_to_end(
        self, monkeypatch
    ):
        _install_all_fail(monkeypatch)
        result = await classify_niche(
            TRADING_TRANSCRIPT, [], title=TRADING_TITLE,
        )
        assert result["niche"] == "finance_business"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True
        from src.auto_director.edl import load_profile

        # The rescued profile drives the render: finance B-roll is on
        # at 1.5/min since Slice 5 (d8cb6a6) — never the podcast
        # profile's stock B-roll, and wrong-sense inserts are caught
        # by the sense / concept / veto layers instead.
        profile = load_profile(result["niche"])
        assert profile["broll"]["enabled"] is True
        assert profile["broll"]["density_per_min"] == 1.5

    def test_finance_podcast_interview_cues_lock_the_rule_out(self):
        """A genuine finance PODCAST — two-person interview about
        markets, dense with the same trading words — stays podcast:
        the conversation cues lock the correction out."""
        out = apply_evidence_guardrails(
            _model_podcast(), title=FINANCE_PODCAST_TITLE,
            transcript_sample=FINANCE_PODCAST_TRANSCRIPT,
            visual=_VLOG_ACTIVITY_VISUAL, audio=None,
        )
        assert out["niche"] == "podcast_news"
        assert "guardrail" not in out["evidence"]

    def test_finance_podcast_title_locks_the_rule_out(self):
        out = apply_evidence_guardrails(
            _model_podcast(), title="The Trading Podcast — Weekly Episode",
            transcript_sample=TRADING_TRANSCRIPT, visual=None, audio=None,
        )
        assert out["niche"] == "podcast_news"
        assert "guardrail" not in out["evidence"]

    def test_static_talking_head_visual_locks_the_rule_out(self):
        out = apply_evidence_guardrails(
            _model_podcast(), title=TRADING_TITLE,
            transcript_sample=TRADING_TRANSCRIPT,
            visual=_VLOG_STATIC_VISUAL, audio=None,
        )
        assert out["niche"] == "podcast_news"
        assert "guardrail" not in out["evidence"]

    def test_vlog_rule_keeps_precedence_over_finance(self):
        """A life-format title over trading speech still lands on the
        vlog profile: the format rule runs before the finance rule."""
        out = apply_evidence_guardrails(
            _model_podcast(), title="Day in the Life of a Trader",
            transcript_sample=TRADING_TRANSCRIPT, visual=None, audio=None,
        )
        assert out["niche"] == "vlog_lifestyle"

    def test_travel_rule_keeps_precedence_over_finance(self):
        out = apply_evidence_guardrails(
            _model_podcast(), title="Tokyo Food Tour — Eating Through Japan",
            transcript_sample=TRADING_TRANSCRIPT,
            visual=_VLOG_ACTIVITY_VISUAL, audio=None,
        )
        assert out["niche"] == "travel_vlog"
        assert (
            out["evidence"]["guardrail"]
            == "podcast_news_overridden_by_title_and_visual_evidence"
        )

    def test_real_production_window_stays_podcast_below_the_floor(self):
        """The honest limit, pinned: this video's real evidence window
        (its morning-routine opening) carries no trading speech, so
        the score stays under the floor and the result stands. The
        floor must NOT be lowered to chase this one video."""
        from src.auto_director.classify import _NICHE_KEYWORDS, _keyword_score

        assert (
            _keyword_score(
                _NICHE_KEYWORDS["finance_business"],
                TRADING_TITLE, PRODUCTION_OPENING,
            )
            < 4
        )
        out = apply_evidence_guardrails(
            _model_podcast(), title=TRADING_TITLE,
            transcript_sample=PRODUCTION_OPENING, visual=None, audio=None,
        )
        assert out["niche"] == "podcast_news"

    def test_weak_single_finance_word_never_fires(self):
        out = apply_evidence_guardrails(
            _model_podcast(), title="My Budget",
            transcript_sample=(
                "we sat around the table and talked about our week, "
                "the kids, and what to cook on Sunday"
            ),
            visual=None, audio=None,
        )
        assert out["niche"] == "podcast_news"
