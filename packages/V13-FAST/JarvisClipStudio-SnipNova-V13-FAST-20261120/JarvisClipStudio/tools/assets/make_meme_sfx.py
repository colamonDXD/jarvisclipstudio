"""Synthesise SnipNova's original meme SFX pack into assets/sfx/library/meme.

  python tools/assets/make_meme_sfx.py [--dest assets/sfx/library]

Meme-board sounds (the famous "boom", sitcom rimshots, sad trombones) are
clips of other people's recordings with no licence, so none can ship. These
are new performances of the same generic archetypes, built from oscillators
and filtered noise with ffmpeg: no samples, nothing to license (released
with the project, manifest licence "original").

Layout follows the renderer's pack lookup (media.asset_library): the EDL's
six cue names under ``meme/<cue>/`` so a profile with ``sfx_pack: "meme"``
draws its whooshes, pops and impacts from here, plus extra archetypes under
``<name>/`` for the editor's library.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _boom(f0: float, f1: float, decay: float, d: float, grit: str) -> tuple[str, str]:
    # Sub "boom": a sine dropping f0 -> f1 Hz with exponential decay,
    # saturated, with a short room tail.
    k = 7
    src = (f"aevalsrc='0.95*sin(2*PI*({f1}*t+{f0 - f1}*(1-exp(-{k}*t))/{k}))*exp(-{decay}*t)"
           f"+0.3*sin(2*PI*({2 * f1}*t+{2 * (f0 - f1)}*(1-exp(-{k}*t))/{k}))*exp(-{decay * 1.6}*t)'"
           f":s=48000:d={d}")
    return src, f"asoftclip=type={grit},aecho=0.8:0.7:60|140:0.3|0.18,lowpass=f=1500"


SOUNDS: dict[str, tuple[str, str]] = {
    # --- the six EDL cues, meme flavour -------------------------------------
    "meme/impact/boom_deep": _boom(110, 32, 1.6, 2.4, "tanh"),
    "meme/impact/boom_punchy": _boom(150, 45, 2.8, 1.6, "atan"),
    "meme/impact/boom_distorted": (
        _boom(130, 38, 2.0, 2.0, "hard")[0],
        "volume=2.5,asoftclip=type=hard,aecho=0.8:0.7:50|120:0.35|0.2,lowpass=f=2200",
    ),
    "meme/pop/boing": (
        "aevalsrc='sin(2*PI*(220*t+60*sin(2*PI*14*t)/(2*PI*14)*exp(-3*t)*14))*exp(-4.5*t)':s=48000:d=0.8",
        "highpass=f=90",
    ),
    "meme/pop/bubble": (
        "aevalsrc='sin(2*PI*(400*t+900*t*t))*exp(-30*t)':s=48000:d=0.2", "highpass=f=150",
    ),
    "meme/whoosh/zip": (
        "aevalsrc='(0.6*sin(2*PI*(300*t+2200*t*t))+0.4*(random(0)-0.5))*sin(PI*t/0.35)':s=48000:d=0.35",
        "highpass=f=300,lowpass=f=8000",
    ),
    "meme/riser/slide_up": (
        "aevalsrc='0.7*sin(2*PI*(500*t+700*t*t))*(0.6+0.4*sin(2*PI*6*t))*min(1,t*6)':s=48000:d=0.9",
        "lowpass=f=4000,aecho=0.7:0.4:40:0.2",
    ),
    "meme/ding/correct": (
        "aevalsrc='0.5*sin(2*PI*1046.5*t)*exp(-6*t)*lt(t,0.14)+0.6*sin(2*PI*1568*(t-0.12))*exp(-4*(t-0.12))*gte(t,0.12)':s=48000:d=0.9",
        "aecho=0.6:0.4:25:0.2",
    ),
    "meme/sting/rimshot": (
        # Two toms then a snare + cymbal: "ba-dum-tss".
        "aevalsrc='0.8*sin(2*PI*180*t)*exp(-25*t)"
        "+0.8*sin(2*PI*130*(t-0.16))*exp(-25*(t-0.16))*gte(t,0.16)"
        "+0.5*(random(0)-0.5)*exp(-14*(t-0.4))*gte(t,0.4)"
        "+0.35*sin(2*PI*210*(t-0.4))*exp(-30*(t-0.4))*gte(t,0.4)':s=48000:d=1.4",
        "highpass=f=60",
    ),
    "meme/sting/sad_trombone": (
        # Four descending notes, the last one long with a wide vibrato.
        "aevalsrc='0.35*(sin(2*PI*F*t)+0.5*sin(4*PI*F*t)+0.3*sin(6*PI*F*t))*min(1,t*20)*exp(-0.4*t)'"
        ":s=48000:d=2.4".replace(
            "F",
            "(if(lt(t,0.45),233.1,if(lt(t,0.9),220,if(lt(t,1.35),207.7,196*(1+0.03*sin(2*PI*6*t))))))",
        ),
        "lowpass=f=2500,aecho=0.6:0.4:30:0.15",
    ),
    "meme/sting/slide_down": (
        "aevalsrc='0.7*sin(2*PI*(1300*t-500*t*t))*min(1,t*8)*exp(-0.6*t)':s=48000:d=1.1",
        "lowpass=f=4000",
    ),
    # --- extra archetypes for the editor library ----------------------------
    "scratch/record_scratch": (
        "aevalsrc='(random(0)-0.5)*abs(sin(2*PI*5*t))*exp(-2*t)':s=48000:d=0.6",
        "bandpass=f=1800:width_type=o:w=2,volume=4",
    ),
    "horn/party_horn": (
        "aevalsrc='0.3*(sin(2*PI*466*t)+sin(2*PI*470*t)+sin(2*PI*587*t)+sin(2*PI*699*t))*min(1,t*30)*lt(t,0.9)':s=48000:d=1.0",
        "asoftclip=type=atan,lowpass=f=3500",
    ),
    "crickets/night_crickets": (
        "aevalsrc='0.4*sin(2*PI*4400*t)*gt(sin(2*PI*30*t),0)*gt(sin(2*PI*1.6*t),0.3)':s=48000:d=3.0",
        "highpass=f=3000",
    ),
    "glitch/digital_glitch": (
        "aevalsrc='0.6*sin(2*PI*(200+1800*gt(sin(2*PI*23*t),0))*t)*gt(sin(2*PI*9*t),-0.4)':s=48000:d=0.5",
        "acrusher=bits=6:samples=8",
    ),
    "heartbeat/heartbeat": (
        "aevalsrc='0.9*sin(2*PI*55*t)*(exp(-30*mod(t,0.9))+0.7*exp(-30*abs(mod(t,0.9)-0.22))*gte(mod(t,0.9),0.22))':s=48000:d=2.7",
        "lowpass=f=300",
    ),
}


def render(rel: str, source: str, chain: str, dest: Path) -> Path:
    out = dest / f"{rel}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    graph = f"{chain},aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-af", graph,
         "-ar", "48000", "-c:a", "pcm_s16le", str(out)],
        check=True, capture_output=True,
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(ROOT / "assets" / "sfx" / "library"))
    a = ap.parse_args()
    dest = Path(a.dest)
    failed = 0
    for rel, (source, chain) in SOUNDS.items():
        try:
            print(f"ok  {render(rel, source, chain, dest).relative_to(dest)}")
        except subprocess.CalledProcessError as exc:
            failed += 1
            print(f"FAILED {rel}: {exc.stderr.decode(errors='replace')[-300:]}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
