"""Build a local, media-linked review and inspect all exported audio/video frames."""
import html
import json
import re
import subprocess
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

proof = Path(__file__).resolve().parent / "verified-kai/caption-proof"
release = proof / "release"
h = html.escape


def media_check(path):
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-vf",
            "scale=160:284,blackdetect=d=0.12:pix_th=0.10,freezedetect=n=-55dB:d=1",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    p = subprocess.run(args, capture_output=True, text=True, check=True)
    stats = json.loads(re.findall(r'\{\s*"input_i".*?\}', p.stderr, re.S)[-1])
    return dict(lufs=stats["input_i"], true_peak=stats["input_tp"], black=re.findall(r"black_start:([\d.]+)", p.stderr),
                freezes=re.findall(r"freeze_start: ([\d.]+)", p.stderr))


def text_by_time(words, start, stop):
    return " ".join(w["text"].strip() for w in words if start <= w["start"] < stop)


def raw_words(path):
    return [{"start": w["start"], "text": w["word"]} for s in json.loads(path.read_text())["segments"] for w in s["words"]]


def screen(path, output):
    subprocess.run(["ffmpeg","-v","error","-y","-i",str(path),"-vf","fps=1/4,scale=140:248,tile=7x3",
                    "-frames:v","1",str(output)], check=True)


def sound_match(source, final, begin, length):
    def pcm(p, t, duration):
        cmd = ["ffmpeg","-v","error","-ss",str(t),"-i",str(p),"-t",str(duration),"-vn","-ac","1","-ar","8000","-f","s16le","-"]
        return np.frombuffer(subprocess.check_output(cmd),"<i2").astype(float)
    samples = []
    for at in (length*.18,length*.50,length*.82):
        ref, mixed = pcm(source,begin+at-.4,2.8), pcm(final,at,2.)
        if len(ref) < len(mixed) or not mixed.any():
            continue
        fft_n = 1 << (len(ref)+len(mixed)-1).bit_length()
        corr = np.fft.irfft(np.fft.rfft(ref,fft_n)*np.fft.rfft(mixed[::-1],fft_n),fft_n)[len(mixed)-1:len(ref)]
        offset = int(np.argmax(corr))
        matched = ref[offset:offset+len(mixed)]
        similarity = float(np.dot(matched,mixed)/(np.linalg.norm(matched)*np.linalg.norm(mixed)+1))
        samples.append({"at_s":round(at,2),"offset_ms":round((offset/8000-.4)*1000),"correlation":round(similarity,3)})
    return samples


page = ['''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Luffy’s Kai clip review</title><style>body{max-width:1180px;margin:auto;padding:24px;font:16px/1.5 system-ui;background:#faf9f6;color:#182121}h1,h2{line-height:1.15}h2{margin-top:44px}.players{display:flex;gap:12px;flex-wrap:wrap}.players figure{flex:1 1 230px;margin:0}video{width:100%;height:420px;background:#151515}th,td{vertical-align:top;text-align:left;padding:6px 8px;border-bottom:1px solid #aaa}table{border-collapse:collapse}img{max-width:100%}a{color:#07574f}details{margin:16px 0}</style><main>
<h1>Same three Kai moments, before and after</h1><p>Play the development export first. Compare it with the original or the public reference, using one audio track at a time. Raw transcripts include uncensored speech. Each row shows a five-second source interval. Different recognizers sometimes assign a word to adjacent rows.</p>''']
names = ["Kai worries he might not be the father","Kai spots a change in the water","Kai hears a warning in the field"]
refs = ["Ec18vXsDHYU","_DhJBnQl0hI","nDxYSoFZxAM"]
views = ["23.73 million","24.10 million","10.71 million"]
findings = [
    "0–3 s: An NPC telling people to move opens the clip. The old headline implies a reveal, although Kai voices a fear. 12–23 s: the sidewalk dialogue and Kai’s paternity speculation overlap. Around 24–40 s the gameplay becomes blurred and colored while his face remains clear. At 44–53 s the conversation turns to a car. The revised headline tracks the uncertainty. Assess whether the closing exchange still belongs.",
    "0–12 s: the avatar sits by the water. The source includes two controller prompts and a small facecam. The action is a change in the game, which is hard to infer from the face alone. Around 19–26 s the phrase about another dimension supplies context. Around 31 s the original masks “damn.” At 38–40 s Kai calls it a W. The later bag dialogue starts a different thought. Compare the reported cut range and the two independent readings of the opening word.",
    "0–8 s: field footage leads into repeated calls. Around 16–25 s the avatar carries a body. At 30–38 s the gameplay changes to an item encounter. Around 44–48 s the lower panel becomes hard to read. Around 50–58 s another character appears and the warning supplies the stakes. The old title primes the viewer for a flare gun, while both audio models disagree with that spelling. The revised title points to the encounter. Check the repeated “yo” by ear."
]
all_checks = []
for n, src in enumerate(sorted(proof.glob("window*.mp4")),1):
    stem = f"clip_{n:03}"
    result = json.loads((release/(stem+".json")).read_text())
    assert result["ok"] and result["burned_words"]
    begin, end = result["source_range"]
    final = release/(stem+".mp4")
    old = proof.parent/(stem+".mp4")
    with ThreadPoolExecutor(3) as pool:
        v = pool.submit(media_check, final)
        o = pool.submit(media_check, old)
        shot = pool.submit(screen,final,release/(stem+".jpg"))
        new, previous = v.result(), o.result()
        shot.result()
    sound = sound_match(src,final,begin,result["duration"])
    all_checks.append({"clip":n,"old":previous,"new":new,"range":[begin,end],"duration":result["duration"],"sound_match":sound})
    burned = [{**w,"start":w["start"]+begin} for w in result["burned_words"]]
    groq = raw_words(src.with_suffix(".groq.json"))
    offline = raw_words(src.with_suffix(".offline.json"))
    full_burn = json.loads((proof/"full"/(stem+".json")).read_text())["burned_words"]
    blocks = []
    for t in range(0,int(max(end,max(w["start"] for w in groq+offline)))+1,5):
        cells = [f"{t:02}–{t+5:02} s",*(text_by_time(words,t,t+5) for words in (burned,full_burn,groq,offline))]
        blocks.append(cells)
    plain = f"{names[n-1]}\nSource {begin:.2f}–{end:.2f} s. Burned source {result['transcript_provider']}.\n\n"
    for row in blocks:
        plain += f"{row[0]}\nEditorial burn: {row[1]}\nFull-window burn: {row[2]}\nGroq: {row[3]}\nOffline Whisper: {row[4]}\n\n"
    (release/(stem+".txt")).write_text(plain,encoding="utf8")
    page.append(f'<h2>{n}. {names[n-1]}</h2><div class="players">')
    for label,link in [("Editorial export",stem+".mp4"),("Original supplied export","../../"+stem+".mp4"),("Public reference · "+views[n-1]+" recorded views","../../../../hook-study/videos/"+refs[n-1]+".mp4")]:
        assert (release/link).is_file(),link
        page.append(f'<figure><figcaption>{label}</figcaption><video controls playsinline preload="metadata" src="{link}"></video></figure>')
    page.append(f'</div><button onclick="pair(this,0)">Replay both openings · hear edit</button> '
                f'<button onclick="pair(this,1)">Replay both openings · hear original</button><p>{findings[n-1]}</p><p>Source interval {begin:.2f}–{end:.2f} s. Export {result["duration"]:.2f} s. '
                f'{len(burned)} rendered caption words. Full-window Groq {len(groq)}, offline large-v3 {len(offline)}. '
                f'<a href="{stem}.txt">Timestamped transcript</a> · <a href="../full/{stem}.mp4">Full-window caption proof</a> · '
                f'<a href="https://www.youtube.com/shorts/{refs[n-1]}">Public reference</a>.</p>')
    page.append(f'<p>Integrated audio {previous["lufs"]} → {new["lufs"]} LUFS. True peak {previous["true_peak"]} → {new["true_peak"]} dBTP. '
                f'New full-frame black intervals ≥120 ms: {len(new["black"])}. New whole-frame freezes ≥1 s: {len(new["freezes"])}. '
                'These detectors cannot judge a blur inside gameplay or identify a voice under music.</p>')
    page.append('<p>Source-audio correlation at three points (1.0 is identical waveform shape): '
                + ', '.join(f'{v["at_s"]} s: {v["correlation"]}, lag {v["offset_ms"]} ms' for v in sound)+'.</p>')
    page.append('<details><summary>Compare sampled frames every four seconds</summary>')
    page.append(f'<p>Original</p><img src="../before-{n:03}.jpg"><p>Editorial export</p><img src="{stem}.jpg"></details>')
    page.append('<table><tr><th>Source time</th><th>Editorial burn</th><th>Full-window burn</th><th>Groq full window</th><th>Offline Whisper full window</th></tr>')
    page.extend('<tr>'+''.join(f'<td>{h(x)}</td>' for x in row)+'</tr>' for row in blocks)
    page.append('</table>')
page.append('''<p><a href="audit.md">Editorial audit, references and picker decision</a></p></main><script>
function pair(button,audible){let group=button;while(!group.classList.contains("players"))group=group.previousElementSibling;
document.querySelectorAll("video").forEach(v=>v.pause());group.querySelectorAll("video").forEach((v,i)=>{if(i<2){v.currentTime=0;v.muted=i!==audible;v.play().catch(()=>{});}})}
</script></html>''')
(release/"review.html").write_text("\n".join(page),encoding="utf8")
(release/"media-checks.json").write_text(json.dumps(all_checks,indent=2))
