"""Package the real offline encode traces and corresponding comparison media."""
import hashlib
import html
import json
import subprocess
from pathlib import Path

import numpy as np

proof = Path(__file__).resolve().parent / "verified-kai/caption-proof"
out = proof / "editorial"
page = ['''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kai · editorial review</title><style>
body{max-width:1060px;margin:3em auto;padding:0 1.3em;font:16px/1.55 system-ui;color:#202529}h2{margin-top:2.2em}
section{border-top:1px solid #999;margin:2em 0}.pair{display:flex;gap:16px}.pair figure{margin:0;max-width:46%}video{width:100%;max-height:570px;background:#111}
td,th{padding:.45em;text-align:left;vertical-align:top;border-bottom:1px solid #ddd}img{width:100%}a{color:#15599e}code{overflow-wrap:anywhere}
</style><h1>Kai · before and after</h1>
<p>The earlier versions are the two editorial exports in the <code>release</code> folder Luffy identified. The numbered download copies were unavailable for a byte-for-byte match. Each pair comes from the same source recording. The clocks can diverge after a cut.</p>''']
def run(*args):
    return subprocess.check_output(args)
def pcm(path,t,span):
    return np.frombuffer(run("ffmpeg","-v","error","-ss",str(t),"-i",str(path),"-t",str(span),"-vn","-ac","1","-ar","8000","-f","s16le","-"),"<i2").astype(float)
for n,name in [(2,"The breathing prompt"),(3,"The warning in the field")]:
    stem = f"clip_{n:03d}"
    new = out / (stem+".mp4")
    old = proof/"release"/new.name
    source = proof / ("window_002.mp4" if n==2 else "window_003_cold.mp4")
    d = json.loads(new.with_suffix(".json").read_text())
    ranges = d["source_ranges"]
    facts = {"sha256":{}, "source_audio_checks":[]}
    for key,p in [("old",old),("new",new),("source",source)]:
        facts["sha256"][key] = hashlib.sha256(p.read_bytes()).hexdigest()
    offset = 0.
    cuts = []
    for (a,b), offset in zip(ranges,d.get("source_offsets",[0.])):
        cuts.append(f"{offset:.2f}–{offset+b-a:.2f} s uses source {a:.2f}–{b:.2f} s.")
        at = .35*(b-a)
        reference = pcm(source, a+at-.15, .95)
        actual = pcm(new, offset+at, .65)
        match = int(np.argmax(np.correlate(reference,actual,"valid")))
        corr = np.corrcoef(reference[match:match+len(actual)], actual)[0,1]
        facts["source_audio_checks"].append(dict(at=round(offset+at,2),offset_ms=round(match/8-150),correlation=round(float(corr),4)))

    run("ffmpeg","-v","error","-i",str(new),"-f","null","-")
    run("ffmpeg","-v","error","-y","-i",str(new),"-vf","fps=1/2,scale=162:288,tile=9x2","-frames:v","1",str(out/f"film{n}.jpg"))
    (out/(stem+"-checks.json")).write_text(json.dumps(facts,indent=2))
    (out/(stem+"-ffprobe.json")).write_text(json.dumps(d["ffprobe"],indent=2))
    summary = ("The opening restores the original instruction “Release L2 and R2 to breathe out.” It uses the first 2.35 seconds, then skips the wait before the response. The image cuts back to Kai at his first response. During the change at the dock the game fills the frame again. The ending keeps his verdict, “That was a W, chat.” The opening transcript still has a disputed word."
               if n==2 else "The opening follows the avatar toward the wreckage. The flare-gun pickup supplies context, the spoken warning changes the stakes, and the next gameplay close-up shows the confrontation. Original game audio carries the pursuit.")
    if len(ranges) > 1:
        cuts.append("The source splice uses a 150 ms picture and audio crossfade, confirmed in the encode graph. The gameplay/reaction view changes are straight cuts.")
    shots = d["applied_game_shots"]
    words = d["burned_words"]
    page.extend([f"<section><h2>{n:03d} · {name}</h2><div class=pair>",
        f'<figure><figcaption>Earlier editorial export</figcaption><video controls preload="metadata" src="../release/{stem}.mp4"></video></figure>',
        f'<figure><figcaption>New development export</figcaption><video controls preload="metadata" src="{stem}.mp4"></video></figure></div>',
        f"<p>{summary}</p><button onclick=\"const p=this.parentNode.querySelectorAll('video');p.forEach((v,i)=>{{v.currentTime=0;v.muted=i===0;v.play()}})\">Replay both · hear new mix</button>",
        "<h3>Source cuts</h3><ul>"+''.join("<li>"+c+"</li>" for c in cuts)+"</ul>",
        "<h3>Picture changes on the export clock</h3><ul>"+''.join(f"<li>{s['start']:.2f}–{s['end']:.2f} s · full-height game crop, {s['reason']}. Straight cut back to the face/game stack.</li>" for s in shots)+"</ul>",
        "<p>These changes use an instantaneous change of shot size. There is no animated zoom. No graphic title was burned. Caption words stay on the seam and the original audio continues through picture-only cuts.</p>",
        f'<img src="film{n}.jpg" alt="Export sampled every two seconds"><p><b>Caption samples</b> '+html.escape(" · ".join(f"{w['start']:.2f}s {w['text']}" for w in words[:9]))+"</p>",
        f"<p>Burned {len(words)} of {d['expected_words_in_edit']} selected ASR words. Recognizers disagreed on some words. Network connections recorded by the Python process: {len(d['network_connections'])}. The render container also had external networking disabled.</p>",
        f'<p><a href="{stem}.edit.json">Per-render ASR cache and model receipt</a> · <a href="{stem}.json">Final EDL, caption timings, provider selection, environment overrides and encoder filter graphs</a> · <a href="{stem}-ffprobe.json">ffprobe</a> · <a href="{stem}-checks.json">File hashes and source audio alignment</a></p></section>'])
page.append('''<h2>Why the earlier edits stayed static</h2>
<p>The release launcher supplied an editorial headline and preloaded cached source decodes. Both release sidecars record the selected Groq transcript. The gaming branch rebuilt the EDL as one uninterrupted segment and cleared zooms, overlays and SFX. It called the EDL builder with B-roll disabled and set music volume to zero. Its plain-layout delegation produced the face/game stack. An enabled finish pass could still draw the title. Enabling that pass alone supplied no reaction timestamp or evidence for a punch-in.</p>
<p>For the new exports the planner samples the central gameplay picture twice per second, measures changes away from the embedded webcam, and uses the timed transcript to choose the return to the streamer. Its decisions now reach FFmpeg before subtitle burn-in. An earlier visual setup can replace a repetitive silent lead. The quality gate preserves the 30-second minimum and keeps speech out of the removed spans. The actual ranges above show where a splice was made.</p>
<p><b>Audio and assets.</b> Both historical jobs selected the original game/stream mix. The old release sidecars contain no stock accept/reject ledger. The launcher and finisher have no stock search on this path. The new EDLs contain no B-roll, music bed or SFX. There was no provider search, download, transition-library selection, synthetic impact or music duck. The game supplies both the subject and the payoff in these two recordings. The rendered audio was decoded and compared with the source. Audibility, comic timing and audience retention still require a listener.</p>
<h2>Which models actually contributed?</h2>
<p>The earlier <a href="../../../provider-audit.json">separate provider smoke test</a> recorded the following. Authentication or model availability during that test cannot establish a request by a particular export. The prior release used cached results for both videos. No per-request receipt survives for their original recognition requests. The new runs did not request fresh hosted inference, use a remote vision model or download assets.</p>
<table><tr><th>Provider</th><th>Key, authentication and model evidence</th><th>Role in these cuts</th></tr>
<tr><td>Groq</td><td>Key present and authenticated in earlier audit. <code>openai/gpt-oss-120b</code> completed a separate chat probe. The source ASR workflow used <code>whisper-large-v3-turbo</code>.</td><td>Cached audio words selected for both clips. No fresh chat or transcription request for these encodes.</td></tr>
<tr><td>Local Whisper</td><td>Offline large-v3 source decodes exist for both windows. No API key applies.</td><td>Independent word coverage and timing reference. The smaller faster-whisper fallback was unnecessary.</td></tr>
<tr><td>NVIDIA</td><td>Key present, authentication and <code>nvidia/nemotron-3-ultra-550b-a55b</code> test inference succeeded. Several alternative slugs were absent.</td><td>No verified shot, sound or title decision from NVIDIA.</td></tr>
<tr><td>OpenRouter</td><td>Key present, authenticated; <code>nvidia/nemotron-3-ultra-550b-a55b:free</code> passed the earlier probe.</td><td>No verified call or result consumed.</td></tr>
<tr><td>Cloudflare</td><td>Account and key passed the earlier audit. Its chat probe targeted <code>@cf/openai/gpt-oss-120b</code>.</td><td>No verified call or result consumed.</td></tr>
<tr><td>Ollama</td><td>Earlier service inventory found four models including <code>qwen3:8b</code>. No authentication or generation test was recorded.</td><td>No inference evidence.</td></tr>
<tr><td>DeepSeek / Hugging Face</td><td>Earlier key checks passed. DeepSeek inference was skipped for cost. Hugging Face has no inference receipt.</td><td>No verified use.</td></tr>
<tr><td>Pixabay / Coverr / Openverse</td><td>Credentials and availability untested in these jobs.</td><td>No searches, downloads or accepted assets. The stock picker was bypassed.</td></tr></table>
<h2>Feature flags and the actual render path</h2>
<p>All ten overrides were unset inside both new encoder processes. The original release launcher explicitly set HOOK_FINISH, STYLED_HOOK_TITLE and REACTION_PUNCH on. It did not forward the ten flags below. A snapshot of the retired encoder environments was unavailable, so those historical effective values cannot be independently certified.</p>
<ul><li><b>EDL_CONTENT_CUTS</b> default off. Even an earlier proposed sequence would have been replaced by the gaming branch.</li>
<li><b>SCREEN_SUBJECT</b> default off. The delegated facecam composition bypassed ordinary screen-subject shot planning.</li>
<li><b>SELECTION_EVENTS</b> default off. These exports re-rendered fixed windows and never ran candidate discovery.</li>
<li><b>FIT_PAD</b> default blur. The gaming stack uses cropped panels, so fit padding had no effect.</li>
<li><b>REFRAME_HIRES_PROBE</b> default off. This changes analysis resolution for large masters. It does not invent shot changes.</li>
<li><b>CAPTION_LOOP_FILTER / CAPTION_PHRASE_SPLIT</b> default off. The gaming seam renderer uses its own grouping and preserves the timed word track.</li>
<li><b>ASSET_VARIANTS / BROLL_OPENVERSE</b> default on. Both need an upstream asset request. There was none.</li>
<li><b>TRANSITION_LIBRARY_API</b> default on. This serves previews to the UI. It does not pick a transition for the renderer.</li></ul>''')
(out/"review.html").write_text(''.join(page),encoding="utf8")
