"""Devanagari -> casual Hinglish (Roman letters), word by word.

Adapted from NeonClipping (``translit.py``, MIT — see NOTICE): common
words come from a dictionary (hai, nahi, kya, mein...), everything else
is transliterated letter by letter with Hindi schwa deletion ("kamal,
not kamala"; "samajhna, not samajahana"). Latin words (English in the
middle of Hindi) are left untouched.

This is the pipeline-stage sibling of ``media.hinglish``: that module
romanizes only the ``hindi_podcast`` profile inside the ASS builder,
using the sanscript library. This port is the deterministic,
dependency-free Neon engine, applied earlier — after word alignment,
before caption build, on every transcript source — when the
``TRANSLIT_HINGLISH`` switch is on (default OFF). The conversion is
word by word so word timings survive: only a word's text changes,
never its ``start``/``end`` or any other field.

Detection: a word list counts as Hindi when at least
``TRANSLIT_MIN_DEVANAGARI_SHARE`` of its word tokens contain
Devanagari. Below that share the list is returned untouched, so an
English transcript passes through byte-identical.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

# Fraction of word tokens that must contain Devanagari before a word
# list counts as Hindi and gets transliterated at all.
TRANSLIT_MIN_DEVANAGARI_SHARE = 0.5


def translit_enabled() -> bool:
    """``TRANSLIT_HINGLISH`` switch (default OFF)."""
    return os.getenv("TRANSLIT_HINGLISH", "off").strip().lower() in (
        "on", "1", "true", "yes",
    )


# ---------------------------------------------------------------------------
# NeonClipping translit.py, adapted verbatim (MIT)
# ---------------------------------------------------------------------------

COMMON = {
 'है': 'hai', 'हैं': 'hain', 'हूँ': 'hoon', 'हूं': 'hoon', 'था': 'tha', 'थी': 'thi', 'थे': 'the', 'हो': 'ho', 'होगा': 'hoga', 'होगी': 'hogi',
 'में': 'mein', 'मैं': 'main', 'मे': 'mein', 'का': 'ka', 'की': 'ki', 'के': 'ke', 'को': 'ko', 'से': 'se', 'पर': 'par', 'ने': 'ne', 'तक': 'tak',
 'यह': 'yeh', 'ये': 'ye', 'वह': 'woh', 'वो': 'woh', 'यहाँ': 'yahan', 'यहां': 'yahan', 'वहाँ': 'wahan', 'वहां': 'wahan', 'कहाँ': 'kahan', 'कहां': 'kahan',
 'और': 'aur', 'या': 'ya', 'कि': 'ki', 'जो': 'jo', 'तो': 'toh', 'ही': 'hi', 'भी': 'bhi', 'नहीं': 'nahi', 'ना': 'na', 'न': 'na', 'मत': 'mat',
 'क्या': 'kya', 'कैसे': 'kaise', 'कैसा': 'kaisa', 'कौन': 'kaun', 'कब': 'kab', 'क्यों': 'kyun', 'कितना': 'kitna', 'कुछ': 'kuch', 'कोई': 'koi',
 'तुम': 'tum', 'तू': 'tu', 'तुम्हें': 'tumhe', 'तुम्हारा': 'tumhara', 'आप': 'aap', 'हम': 'hum', 'हमें': 'hame', 'हमारे': 'hamare', 'हमारा': 'hamara',
 'मुझे': 'mujhe', 'मेरा': 'mera', 'मेरी': 'meri', 'मेरे': 'mere', 'तेरा': 'tera', 'तेरी': 'teri', 'उसे': 'use', 'इसे': 'ise', 'उसका': 'uska', 'उसकी': 'uski',
 'इस': 'is', 'उस': 'us', 'इन': 'in', 'उन': 'un', 'अब': 'ab', 'अभी': 'abhi', 'फिर': 'phir', 'जब': 'jab', 'तब': 'tab', 'सब': 'sab', 'बस': 'bas',
 'कर': 'kar', 'करो': 'karo', 'करना': 'karna', 'करता': 'karta', 'करती': 'karti', 'करते': 'karte', 'किया': 'kiya', 'कहा': 'kaha', 'देखो': 'dekho',
 'रहा': 'raha', 'रही': 'rahi', 'रहे': 'rahe', 'गया': 'gaya', 'गई': 'gayi', 'गए': 'gaye', 'लिए': 'liye', 'साथ': 'saath', 'बहुत': 'bahut',
 'अच्छा': 'accha', 'ठीक': 'theek', 'सही': 'sahi', 'गलत': 'galat', 'भाई': 'bhai', 'यार': 'yaar', 'दोस्तो': 'dosto', 'चलो': 'chalo', 'चल': 'chal',
 'ओके': 'okay', 'हाँ': 'haan', 'हां': 'haan', 'अरे': 'arre', 'वाह': 'wah', 'मतलब': 'matlab', 'बिल्कुल': 'bilkul', 'लेकिन': 'lekin', 'पर्': 'par',
 'एक': 'ek', 'दो': 'do', 'तीन': 'teen', 'चार': 'char', 'पाँच': 'paanch', 'सच': 'sach', 'बात': 'baat', 'लोग': 'log', 'लोगों': 'logon', 'वीडियो': 'video',
 'गेम': 'game', 'प्लीज': 'please', 'सॉरी': 'sorry', 'थैंक्स': 'thanks', 'हेलो': 'hello', 'सब्सक्राइब': 'subscribe', 'चैनल': 'channel',
}

CONS = {'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'ng', 'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'n', 'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh',
        'ण': 'n', 'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n', 'प': 'p', 'फ': 'ph', 'ब': 'b', 'भ': 'bh', 'म': 'm', 'य': 'y', 'र': 'r', 'ल': 'l',
        'व': 'v', 'श': 'sh', 'ष': 'sh', 'स': 's', 'ह': 'h', 'ळ': 'l', 'क्ष': 'ksh', 'ज्ञ': 'gy'}
NUKTA = {'क': 'q', 'ख': 'kh', 'ग': 'g', 'ज': 'z', 'ड': 'd', 'ढ': 'rh', 'फ': 'f'}
VOWELS = {'अ': 'a', 'आ': 'aa', 'इ': 'i', 'ई': 'ee', 'उ': 'u', 'ऊ': 'oo', 'ऋ': 'ri', 'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au', 'ऑ': 'o', 'ऍ': 'e'}
MATRAS = {'ा': 'aa', 'ि': 'i', 'ी': 'ee', 'ु': 'u', 'ू': 'oo', 'ृ': 'ri', 'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au', 'ॉ': 'o', 'ॅ': 'e'}
VIRAMA, NUKTA_C, ANUSVARA, CHANDRA = '्', '़', 'ं', 'ँ'
DEVA = re.compile(r'[ऀ-ॿ]')

def _syllables(word):
    """[(consonant-cluster, vowel or None-for-inherent, trailing-nasal)]"""
    out, i, n = [], 0, len(word)
    while i < n:
        ch = word[i]
        if ch in VOWELS:
            out.append(['', VOWELS[ch], '']); i += 1
        elif ch in CONS or word[i:i + 3] in ('क्ष', 'ज्ञ'):
            cluster = ''
            while i < n:
                if word[i:i + 3] in ('क्ष', 'ज्ञ'):
                    c = CONS[word[i:i + 3]]; i += 3
                elif word[i] in CONS:
                    c = CONS[word[i]]; i += 1
                    if i < n and word[i] == NUKTA_C: c = NUKTA.get(word[i - 1], c); i += 1
                else:
                    break
                cluster += c
                if i < n and word[i] == VIRAMA: i += 1; continue      # conjunct: next consonant joins this cluster
                break
            vowel = None
            if i < n and word[i] in MATRAS: vowel = MATRAS[word[i]]; i += 1
            out.append([cluster, vowel, ''])
        elif ch in (ANUSVARA, CHANDRA):
            if out: out[-1][2] = 'n'
            i += 1
        elif ch in ('ः',):
            if out: out[-1][2] = 'h'
            i += 1
        else:
            i += 1
    return out

def word_to_hinglish(word):
    if word in COMMON: return COMMON[word]
    syl = _syllables(word)
    if not syl: return word
    inherent = [s[1] is None and s[0] != '' for s in syl]
    delete = [False] * len(syl)
    if len(syl) > 1 and inherent[-1]: delete[-1] = True            # final schwa
    for i in range(len(syl) - 2, 0, -1):                           # medial schwa, right to left, never the first syllable
        if inherent[i] and not delete[i + 1]:
            delete[i] = True
    out = ''
    for i, (c, v, nas) in enumerate(syl):
        if v is None and c != '': v = '' if delete[i] else 'a'
        out += c + (v or '') + nas
    return _casual(out)

def _casual(w):
    """Everyday Hinglish spelling: pata, mota, haraami, tarike instead of pataa, motaa, haraamee, tareeke."""
    if len(w) > 3: w = re.sub(r'aa$', 'a', w)
    w = re.sub(r'ee$', 'i', w)
    w = w.replace('ee', 'i') if len(w) > 4 else w
    return w

PUNCT = set('.,!?;:"()[]{}-') | {chr(c) for c in (0x27, 0x2026, 0x964, 0x965, 0x2013, 0x2014, 0x201c, 0x201d, 0x2018, 0x2019)}

def to_hinglish(token):
    """Convert one word token; Latin tokens and punctuation pass through. Keeps leading/trailing punctuation.
    (Not \\W: Devanagari vowel signs are marks, which Python's \\w does not count as word characters.)"""
    if not DEVA.search(token): return token
    a, b = 0, len(token)
    while a < b and token[a] in PUNCT: a += 1
    while b > a and token[b - 1] in PUNCT: b -= 1
    lead, core, tail = token[:a], token[a:b], token[b:]
    parts = re.split(r'([^ऀ-ॿ]+)', core)
    return (lead + ''.join(word_to_hinglish(p) if DEVA.search(p) else p for p in parts) + tail).replace(chr(0x964), '.').replace(chr(0x965), '.')

# ---------------------------------------------------------------------------
# Word-dict layer (ours): timings and every other field survive untouched
# ---------------------------------------------------------------------------


def _word_text(word: Dict[str, Any]) -> str:
    if "text" in word:
        return str(word.get("text", ""))
    return str(word.get("word", ""))


def devanagari_word_share(words: List[Dict[str, Any]]) -> float:
    """Fraction of word tokens whose text contains any Devanagari."""
    if not words:
        return 0.0
    hits = sum(1 for w in words if DEVA.search(_word_text(w)))
    return hits / len(words)


def is_hindi_word_list(words: List[Dict[str, Any]]) -> bool:
    return devanagari_word_share(words) >= TRANSLIT_MIN_DEVANAGARI_SHARE


def transliterate_words(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return ``words`` with Devanagari text transliterated to Hinglish.

    The input list and its dicts are never mutated: converted words are
    shallow copies whose only changed field is the text (``text``, or
    ``word`` for faster-whisper-shaped dicts), so ``start`` / ``end``
    and every other field survive exactly. When the list is not
    Hindi by ``TRANSLIT_MIN_DEVANAGARI_SHARE``, the original list is
    returned unchanged — an English transcript passes byte-identical.
    """
    if not words or not is_hindi_word_list(words):
        return words
    converted: List[Dict[str, Any]] = []
    for word in words:
        key = "text" if "text" in word else "word"
        text = _word_text(word)
        hinglish = to_hinglish(text)
        if hinglish == text:
            converted.append(word)
        else:
            replacement = dict(word)
            replacement[key] = hinglish
            converted.append(replacement)
    return converted
