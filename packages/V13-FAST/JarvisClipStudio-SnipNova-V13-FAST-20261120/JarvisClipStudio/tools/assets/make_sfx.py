"""Synthesise SnipNova's bundled sound effects (original, CC0) into assets/sfx.

  python tools/assets/make_sfx.py [--dest assets/sfx]

The EDL places six named cues (auto_director.edl.SFX_EVENT_NAMES): whoosh,
pop, riser, ding, impact, sting. The renderer resolves each against
``assets/sfx/<name>.*`` and then the user's own SFX library. These files are
generated here from oscillators and filtered noise with ffmpeg: no samples,
no third-party audio, so there is nothing to license. They are released
CC0 with the project. Meme sounds users download themselves (a "vine boom")
belong in their own SFX library, not in the bundle.

48 kHz stereo WAV, peak-normalised, short. Deterministic: the same ffmpeg
build regenerates the same audio.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# name -> (lavfi source, extra filter chain). t is time in seconds.
SOUNDS = {
    # Deep, gritty sub "boom": a sine that drops 120 -> 40 Hz with a fast
    # exponential decay, soft-clipped, with a short room tail.
    "impact": (
        "aevalsrc='0.95*sin(2*PI*(40*t+80*(1-exp(-6*t))/6))*exp(-2.6*t)"
        "+0.25*sin(2*PI*(80*t+160*(1-exp(-6*t))/6))*exp(-4*t)':s=48000:d=1.6",
        "asoftclip=type=tanh,aecho=0.8:0.6:45|90:0.25|0.12,lowpass=f=1800",
    ),
    # Air whoosh: high-passed pink noise under a bell-shaped swell.
    "whoosh": (
        "anoisesrc=c=pink:r=48000:d=0.7:a=0.9",
        "highpass=f=500,lowpass=f=6500,volume='exp(-pow((t-0.32)/0.11,2))':eval=frame,"
        "aphaser=type=t:speed=2",
    ),
    # Short bubbly pop: a 700 -> 280 Hz blip, 80 ms.
    "pop": (
        "aevalsrc='sin(2*PI*(280*t+420*(1-exp(-40*t))/40))*exp(-38*t)':s=48000:d=0.18",
        "highpass=f=120",
    ),
    # Bell ding: two inharmonic partials with long decay.
    "ding": (
        "aevalsrc='0.6*sin(2*PI*1320*t)*exp(-3.2*t)+0.3*sin(2*PI*2650*t)*exp(-5*t)"
        "+0.12*sin(2*PI*3960*t)*exp(-7*t)':s=48000:d=1.4",
        "aecho=0.7:0.5:30:0.2",
    ),
    # Tension riser: a sine glide 200 -> 1600 Hz plus noise, crescendo, 1.3 s.
    "riser": (
        "aevalsrc='(0.5*sin(2*PI*(200*t+1400*t*t/(2*1.3)))+0.25*(random(0)-0.5))*pow(t/1.3,2)':s=48000:d=1.3",
        "highpass=f=150,lowpass=f=7000",
    ),
    # Comic sting: a minor-triad stab with saw-like harmonics.
    "sting": (
        "aevalsrc='(sin(2*PI*220*t)+0.5*sin(2*PI*440*t)+0.33*sin(2*PI*660*t)"
        "+0.8*sin(2*PI*261.6*t)+0.4*sin(2*PI*523.2*t)+0.7*sin(2*PI*329.6*t)"
        "+0.35*sin(2*PI*659.2*t))*0.22*exp(-3*t)':s=48000:d=0.9",
        "asoftclip=type=atan,lowpass=f=5000",
    ),
}


def render(name: str, source: str, chain: str, dest: Path) -> Path:
    out = dest / f"{name}.wav"
    graph = f"{chain},aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-af", graph,
         "-ar", "48000", "-c:a", "pcm_s16le", str(out)],
        check=True, capture_output=True,
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(ROOT / "assets" / "sfx"))
    a = ap.parse_args()
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name, (source, chain) in SOUNDS.items():
        path = render(name, source, chain, dest)
        print(f"ok  {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
