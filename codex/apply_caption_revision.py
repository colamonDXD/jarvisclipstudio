from pathlib import Path
b=Path('codex/JarvisClipStudio/backend/src')
def edit(file, fn):
    p=b/file
    s=p.read_text(encoding='utf8')
    new=fn(s)
    assert new!=s,file
    p.write_text(new,encoding='utf8')
def fin(s):
    a=s.index('    from .groq_transcribe import transcribe_with_groq')
    z=s.index('    coverage:',a)
    s=s[:a]+'''    from .burn_transcript import MIN_RETAINED, select_audio
    def last_resort():
        if source_url:
            from ..media.yt_captions import edited_captions, fetch_youtube_captions
            captions = fetch_youtube_captions(source_url)
            if captions:
                return edited_captions(captions, source_start, total, teaser)
        return None

    try:
        data, provider, counts = select_audio(window, last_resort)
        full = data
        _write_cache(window.with_suffix(".transcript_cache.full.json"), full)
        if streamer_words_only:
            data = keep_streamer_words(window, data)
        if len(data["words"]) < MIN_RETAINED * max(counts.values(), default=len(full["words"])):
            # OCR removed too much. Decode again and retain speech conservatively.
            retry, retry_provider, retry_counts = select_audio(window, last_resort)
            if len(retry["words"]) >= len(full["words"]):
                full, provider, counts = retry, retry_provider, retry_counts
            data = full
        _write_cache(window.with_suffix(".transcript_cache.json"), data)
    except Exception as exc:
        return {"ok": False, "error": f"Live caption preparation failed: {exc}"}
'''+s[z:]
    s=s.replace('from ..media.transcription import get_video_transcript, load_cached_transcript_data','')
    s=s.replace('caption_template=(profile.get','extend_to_sentence=False, caption_template=(profile.get')
    s=s.replace('if comment_question:\n        result[','result["transcript_provider"] = provider\n    result["decoder_word_counts"] = counts\n    result["burned_words"] = layout_report.get("burned_words", [])\n    if comment_question:\n        result[')
    return s
edit('live/finisher.py',fin)
edit('live/moments.py',lambda s:s.replace('groups.setdefault(entry["line"], []).append(entry["text"])','groups.setdefault(entry["line"], []).append(entry["text"] if entry.get("confidence", 0) >= 85 else "")').replace('sec - 1.5 <= t < sec + 2.5','sec - 0.5 <= t < sec + 1.5').replace('if size >= 2 and sum(len(s) for s in screen[offset:offset + size]) >= 6:', 'if size >= 4 and size >= 0.8 * len(screen) and all(screen[offset:offset + size]):'))
edit('auto_director/reference_style.py',lambda s:s.replace('"text": text,\n                "left": float(row','"text": text,\n                "confidence": conf,\n                "left": float(row'))
edit('caption_templates.py',lambda s:s[:s.index('    "seam_red":')]+s[s.index('    "seam_red":'):s.index('# Defaults for')].replace('"font_size": 68','"font_size": 42').replace('"font_size": 64','"font_size": 42').replace('"word_pop": True','"word_pop": False,\n        "spoken_pop": True').replace('"animation": "karaoke",\n        "word_pop"','"animation": "karaoke",\n        "karaoke_fill": True,\n        "word_pop"').replace('"max_words_per_line": 2','"max_words_per_line": 3')+s[s.index('# Defaults for'):])
def captions(s):
    s=s.replace('    effective_font_family = font_family or','    seam = bool(template.get("spoken_pop"))\n    effective_font_family = font_family or')
    s=s.replace('if relevant_words and loop_filter_enabled()', 'if relevant_words and not seam and loop_filter_enabled()').replace('if relevant_words and caption_placements is None:', 'if relevant_words and not seam and caption_placements is None:')
    s=s.replace('window_end = min(window_end, next_start)','window_end = next_start if seam and next_start - window_end <= 0.65 else min(window_end, next_start)')
    s=s.replace('min(next_start, word_end + KARAOKE_GAP_BRIDGE_SECONDS)','min(next_start, word_end + (0.65 if seam else KARAOKE_GAP_BRIDGE_SECONDS))')
    s=s.replace('    if caption_placements is not None:\n        base_spans', '    if seam and caption_placements is None:\n        base_spans = []\n        i = 0\n        while i < len(relevant_words):\n            end = i + 1\n            while end < len(relevant_words) and float(relevant_words[end]["start"]) - float(relevant_words[end-1]["end"]) <= 0.65:\n                end += 1\n            while i < end:\n                stop = min(end, i + (2 if end - i == 4 else 3))\n                base_spans.append((i, stop))\n                i = stop\n    elif caption_placements is not None:\n        base_spans')
    s=s.replace('parts.append(active_span(disp) if (local_j == local_i or spoken) else idle_span(gj, disp))',r'''if seam:
                        full = scaled(100, chunk_scale)
                        peak = scaled(115, chunk_scale)
                        scale_tags = f"\\fscx{full}\\fscy{full}"
                        if local_j == local_i:
                            scale_tags = f"\\fscx{peak}\\fscy{peak}\\t(0,110,\\fscx{full}\\fscy{full})"
                        color = highlight if local_j == local_i or spoken else primary
                        parts.append(f"{{{font_tag}\\c{color}{scale_tags}}}{disp}")
                    else:
                        parts.append(active_span(disp) if (local_j == local_i or spoken) else idle_span(gj, disp))''')
    return s.replace('    all_events = hook_events + events','    if caption_layout_report is not None and seam:\n        from .profanity import mask_profanity\n        caption_layout_report["burned_words"] = [{**w, "text": mask_profanity(str(w["text"]))} for w in relevant_words]\n    all_events = hook_events + events')
edit('media/captions.py',captions)
edit('media/profanity.py',lambda s:s.replace('"damn", "goddamn", "dammit", ',''))
edit('auto_director/reaction_hooks.py',lambda s:s.replace('|asshole|damn|crap|dick','|asshole|dick'))
edit('media/hook_finish.py',lambda s:s.replace('0.047','0.055').replace('0.042','0.055').replace('    words = text.split()\n    if len(words) > 9:', '    import unicodedata\n    text = "".join(c for c in text if unicodedata.category(c) != "So" and c not in "\\ufe0f\\u200d")\n    words = text.split()\n    if len(words) > 9:').replace('if use_emoji else []','if use_emoji and not quote.get("live_title") else []'))
edit('live/finisher.py',lambda s:s.replace('edl["quote_title"] = quote_title','edl["quote_title"] = {**quote_title, "live_title": True}'))
edit('live/groq_transcribe.py',lambda s:s.replace('os.getenv("LIVE_GROQ_MODEL", "whisper-large-v3-turbo")','"whisper-large-v3-turbo"'))

