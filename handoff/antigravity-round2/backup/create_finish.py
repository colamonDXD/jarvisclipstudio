"""Use the same audio and reaction detectors on Create's selected media."""
import os

from .burn_transcript import MIN_RETAINED, select_audio
from .game_shots import game_shots
from .moments import keep_streamer_words
from ..media.word_align import align_words_to_audio, caption_coverage_for


def enabled(name, default="on"):
    return os.getenv(name, default).lower() in {"1", "true", "on", "yes"}


def voice_intervals(media):
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    speech = get_speech_timestamps(decode_audio(str(media)), VadOptions(
        min_silence_duration_ms=350, speech_pad_ms=0))
    return [(s["start"]/16000, s["end"]/16000) for s in speech]


def prepare(window, length, captions, gaming, edl, report):
    words = []
    if captions:
        # Discovery may leave a separate torch Whisper model resident. The
        # offline decoder runs in a child process and needs that GPU memory.
        from ..media.transcription import _WHISPER_MODEL_CACHE, release_faster_whisper_models
        release_faster_whisper_models()
        if _WHISPER_MODEL_CACHE:
            import gc
            import torch
            _WHISPER_MODEL_CACHE.clear()
            gc.collect()
            torch.cuda.empty_cache()
        blocks = voice_intervals(window) if enabled("CAPTION_WORD_ALIGN") else []
        def covers_voice(words):
            from ..media.word_align import check_caption_coverage
            cues = [{"start": w["start"]/1000, "end": w["end"]/1000} for w in words]
            return bool(blocks) and check_caption_coverage(blocks, cues)["ok"]
        data, provider, counts = select_audio(window, lambda: None, validate_primary=covers_voice)
        original = data["words"]
        if gaming and enabled("STREAMER_WORD_FILTER"):
            data = keep_streamer_words(window, data)
            if len(data["words"]) < MIN_RETAINED * max(counts.values()):
                data = {**data, "words": original}
            report["streamer_words_retained"] = len(data["words"])
        words = [{**w, "word": w["text"], "start": w["start"]/1000,
                  "end": w["end"]/1000} for w in data["words"]]
        words, _ = align_words_to_audio(words, window)
        if enabled("CAPTION_WORD_ALIGN"):
            for start, end in blocks:
                # A short, isolated utterance and a single overlapping ASR
                # token give us both edges without guessing other words.
                indices = [i for i, w in enumerate(words) if w["start"] < end and w["end"] > start]
                if len(indices) == 1 and end - start <= 1.5:
                    i = indices[0]
                    if (i == 0 or words[i-1]["end"] <= start) and (i+1 == len(words) or words[i+1]["start"] >= end):
                        words[i] = {**words[i], "start": start, "end": end}
        report.update(transcript_provider=provider, decoder_counts=counts,
                      expected_words=len(original))
    if gaming and enabled("GAME_SHOTS"):
        edl["game_shots"] = game_shots(window, 0, length, words)
    if gaming and enabled("REACTION_PUNCH", "off"):
        from .vod import _scan_chunk, add_reaction_features, find_candidates
        series = _scan_chunk((str(window), 0, length))
        if series:
            moments = find_candidates(add_reaction_features(series, window, length), [], 1)
            if moments:
                edl.update(peak_energy_timestamp=moments[0]["onset"],
                           reaction_signals=moments[0]["signals"])
                report["reaction_signals"] = moments[0]["signals"]
    return words


def check_coverage(media, report):
    burned = report.get("burned_words") or []
    if len(burned) < MIN_RETAINED * report["expected_words"]:
        raise ValueError("Rendered captions contain too few of the decoded words")
    if enabled("CAPTION_WORD_ALIGN"):
        coverage = caption_coverage_for(media, report.get("caption_chunks") or [])
        if coverage is None:
            raise ValueError("Could not measure rendered caption coverage")
        report["audio_level_coverage"] = coverage
        from ..media.word_align import check_caption_coverage
        blocks = voice_intervals(media)
        cues = report.get("caption_chunks") or []
        coverage = check_caption_coverage(blocks, cues)
        seconds = sum(end-start for start, end in blocks)
        retained = max(0., 1 - coverage["uncovered_seconds"]/seconds) if seconds else 1.
        missing = [(start, end) for start, end in blocks if not any(
            cue["start"] < end and cue["end"] > start for cue in cues)]
        report["caption_coverage"] = {**coverage, "speech_seconds": seconds,
            "covered_fraction": retained, "missing_utterances": missing,
            "ok": retained >= MIN_RETAINED and not missing}
        if not report["caption_coverage"]["ok"]:
            raise ValueError(f"Rendered captions missed speech: {report['caption_coverage']}")
