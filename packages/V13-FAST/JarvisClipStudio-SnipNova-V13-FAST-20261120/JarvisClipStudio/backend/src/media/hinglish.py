"""Devanagari -> Roman Hinglish transliteration for burned-in captions.

Whisper transcribes Hindi speech in Devanagari script (~90% of the time),
but viral Hindi podcast clips (Raj Shamani / TRS style) burn captions in
ROMAN Hinglish — the loose phonetic Latin spelling viewers actually read.
Devanagari captions are the #1 amateur tell, so the ``hindi_podcast``
profile converts caption words before the ASS builder sees them.

The conversion is WORD BY WORD: each word dict keeps its ``start``/``end``
(and every other field) exactly as transcribed, so karaoke word timing is
untouched. Only the ``text`` of words that contain Devanagari changes.

Engine: ``indic-transliteration`` (sanscript, MIT, offline, deterministic)
Devanagari -> ITRANS, plus the normalization pass below. ITRANS is a
scholarly scheme ("Apa", "haiM", "bahuta"); viral Hinglish is loose
phonetic ("aap", "hain", "bahut"), so ITRANS output is rewritten:

- an explicit dictionary for the most common function words, where the
  general rules produce a spelling nobody uses (में -> "mein",
  अच्छा -> "achha", नहीं -> "nahin"),
- schwa deletion: a word-final inherent "a" is dropped ("bahuta" ->
  "bahut", "aura" -> "aur"). This runs on the raw ITRANS string, where a
  final lowercase "a" is always the inherent vowel — a long final "A"
  ("rahA" -> "raha", "usakA" -> "uska") is kept,
- long vowels: "A" -> "aa", "I" -> "ee", "U" -> "oo", except word-finally
  where they shorten ("kyA" -> "kya", "paisA" -> "paisa", "pAnI" ->
  "paani"),
- capitals collapse to the Hinglish digraphs: "Th" -> "th", "Ch" -> "chh",
  "Dh" -> "dh", "Sh" -> "sh", retroflex "T"/"D"/"N" -> "t"/"d"/"n",
  nukta ".D" -> "d" (बड़ा -> "bada"), anusvara "M" and candrabindu "~N"
  -> "n" ("haiM" -> "hain"), "j~n" (ज्ञ) -> "gy".

The rules are deliberately conservative: readable Hinglish is the goal,
not scholarly fidelity. Known accepted misses: medial schwa is kept
(लड़का -> "ladaka", commonly written "ladka") and "khAnA" keeps its long
vowel ("khaana").

The library is imported lazily. If it is missing at runtime, callers get
the original text back unchanged plus a loud log warning — a Hindi task
must never crash over an optional cosmetic conversion.
"""

import re
from typing import Any, Dict, Iterable, List, Optional

from .common import logger

# The only profile whose captions are romanized.
ROMANIZE_PROFILE = "hindi_podcast"

# Fraction of letters that must be Devanagari before a transcript (or a
# title) counts as Devanagari-dominant and gets converted at all.
DEVANAGARI_SHARE_THRESHOLD = 0.30

_DEVANAGARI_FIRST = "\u0900"
_DEVANAGARI_LAST = "\u097f"

# Devanagari block minus the danda punctuation marks (U+0964, U+0965): a
# danda is sentence punctuation, so it belongs to a token's preserved
# prefix/suffix, never to the transliterated core. Written as escapes
# because the literal boundary characters are combining marks.
_DEVANAGARI_CLASS = "\u0900-\u0963\u0966-\u097f"

# Whole-word overrides, keyed by the Devanagari token: the required viral
# spellings plus common function words the general rules spell unusually.
_WORD_OVERRIDES: Dict[str, str] = {
    "आप": "aap",
    "कैसे": "kaise",
    "हैं": "hain",
    "क्या": "kya",
    "है": "hai",
    "मैं": "main",
    "नहीं": "nahin",
    "बहुत": "bahut",
    "अच्छा": "achha",
    "ठीक": "theek",
    "पैसा": "paisa",
    "और": "aur",
    "में": "mein",
    "यह": "yeh",
    "वह": "woh",
    "हूँ": "hoon",
    "भी": "bhi",
    "अभी": "abhi",
    "कभी": "kabhi",
    "सभी": "sabhi",
}

_sanscript_module: Optional[Any] = None
_sanscript_unavailable = False


def _load_sanscript() -> Optional[Any]:
    """Lazy-import indic-transliteration; warn loudly (once) if absent."""
    global _sanscript_module, _sanscript_unavailable
    if _sanscript_module is not None:
        return _sanscript_module
    if _sanscript_unavailable:
        return None
    try:
        from indic_transliteration import sanscript

        _sanscript_module = sanscript
    except Exception as exc:  # ImportError or a broken install
        _sanscript_unavailable = True
        logger.warning(
            "indic-transliteration is not available (%s); hindi_podcast "
            "captions will keep their original script. Install the "
            "indic-transliteration package to enable Roman Hinglish captions.",
            exc,
        )
        return None
    return _sanscript_module


def _is_devanagari(char: str) -> bool:
    return _DEVANAGARI_FIRST <= char <= _DEVANAGARI_LAST


def devanagari_share(texts: Iterable[str]) -> float:
    """Fraction of letters (across ``texts``) inside the Devanagari block."""
    letters = 0
    devanagari = 0
    for text in texts:
        for char in str(text):
            if char.isalpha():
                letters += 1
                if _is_devanagari(char):
                    devanagari += 1
    if letters == 0:
        return 0.0
    return devanagari / letters


def is_devanagari_dominant(texts: Iterable[str]) -> bool:
    return devanagari_share(texts) >= DEVANAGARI_SHARE_THRESHOLD


def _delete_final_schwa(itrans: str) -> str:
    """Drop a word-final inherent "a" from raw ITRANS ("bahuta" -> "bahut").

    Runs before the vowel rewrites, while a final lowercase "a" can only be
    the inherent vowel. The remaining stem must still contain a vowel and
    at least two characters, so "ka" (क) and "na" (न) survive.
    """
    if len(itrans) >= 3 and itrans.endswith("a"):
        stem = itrans[:-1]
        if any(vowel in stem for vowel in "aeiouAEIOU"):
            return stem
    return itrans


def _normalize_itrans(itrans: str) -> str:
    """Rewrite scholarly ITRANS as loose phonetic Hinglish."""
    text = itrans
    # ज्ञ and the nasal marks first: they contain characters later rules use.
    text = text.replace("j~n", "gy")
    text = text.replace("~N", "n").replace(".N", "n")
    # Schwa deletion must see the raw ITRANS final "a" (see module docstring).
    text = _delete_final_schwa(text)
    # Nukta consonants (ड़/ढ़) are written as plain d/dh in Hinglish.
    text = text.replace(".Dh", "dh").replace(".D", "d")
    # Aspirate / retroflex capitals collapse to the Hinglish digraphs.
    text = text.replace("Ch", "chh").replace("Th", "th")
    text = text.replace("Dh", "dh").replace("Sh", "sh")
    text = text.replace("D", "d").replace("T", "t").replace("N", "n")
    # Anusvara and a leftover visarga.
    text = text.replace("M", "n").replace("H", "h")
    # ऋ vocalic r.
    text = text.replace("R^i", "ri").replace("RRi", "ri").replace("^", "")
    # Long vowels: full length inside a word, short at the end of one.
    if len(text) == 1:
        return {"A": "aa", "I": "ee", "U": "oo"}.get(text, text.lower())
    if text.endswith("A"):
        text = text[:-1] + "a"
    elif text.endswith("I"):
        text = text[:-1] + "i"
    elif text.endswith("U"):
        text = text[:-1] + "u"
    text = text.replace("A", "aa").replace("I", "ee").replace("U", "oo")
    return text.lower()


# A token is prefix punctuation + a Devanagari-bearing core + suffix
# punctuation. Only the core is transliterated; the rest is preserved.
_TOKEN_SPLIT = re.compile(
    rf"^(?P<prefix>[^{_DEVANAGARI_CLASS}]*)"
    rf"(?P<core>[{_DEVANAGARI_CLASS}].*[{_DEVANAGARI_CLASS}]|[{_DEVANAGARI_CLASS}])"
    rf"(?P<suffix>[^{_DEVANAGARI_CLASS}]*)$"
)


def romanize_token(token: str) -> str:
    """Romanize one whitespace-delimited token; non-Devanagari is untouched."""
    if not token or not any(_is_devanagari(char) for char in token):
        return token
    match = _TOKEN_SPLIT.match(token)
    if not match:
        return token
    core = match.group("core")
    if core in _WORD_OVERRIDES:
        converted = _WORD_OVERRIDES[core]
    else:
        sanscript = _load_sanscript()
        if sanscript is None:
            return token
        try:
            itrans = sanscript.transliterate(
                core, sanscript.DEVANAGARI, sanscript.ITRANS
            )
        except Exception as exc:
            logger.warning("Transliteration failed for %r (%s); keeping it", core, exc)
            return token
        converted = _normalize_itrans(itrans)
    assembled = f"{match.group('prefix')}{converted}{match.group('suffix')}"
    # A sentence-final danda reads as a full stop in Latin captions.
    return assembled.replace("।", ".").replace("॥", ".")


def romanize_text(text: str) -> str:
    """Romanize a free-text string (e.g. a hook title), word by word.

    Returns the string unchanged unless it is Devanagari-dominant, so a
    title the LLM already wrote in Latin passes through byte-identical.
    """
    if not text or not is_devanagari_dominant([text]):
        return text
    if _load_sanscript() is None:
        return text
    return " ".join(romanize_token(token) for token in text.split())


def romanize_caption_words(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return caption word dicts with Devanagari ``text`` romanized.

    The input list and its dicts are never mutated: converted words are
    shallow copies whose only changed field is ``text``, so ``start`` /
    ``end`` (karaoke timing) and every other field survive exactly. When
    the transcript is not Devanagari-dominant, or the transliteration
    library is unavailable, the original list is returned unchanged.
    """
    if not words:
        return words
    if not is_devanagari_dominant([str(word.get("text", "")) for word in words]):
        return words
    if _load_sanscript() is None:
        return words
    converted: List[Dict[str, Any]] = []
    for word in words:
        text = str(word.get("text", ""))
        romanized = romanize_token(text)
        if romanized == text:
            converted.append(word)
        else:
            replacement = dict(word)
            replacement["text"] = romanized
            converted.append(replacement)
    return converted
