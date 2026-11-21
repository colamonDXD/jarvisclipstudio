"""Generate a stand-in gameplay fixture that follows the Lane A spec.

1280x720, 30 fps, 150 s, h264 + aac, HUD (crosshair, health bar, minimap,
ammo counter, kill feed), no speech. Ground truth lives in GROUND_TRUTH.

THIS IS NOT THE OFFICIAL SHARED FIXTURE. It is built from the written spec so
detector work can proceed without it; scored claims must be re-run on the
official ``gameplay_fixture.mp4`` when it arrives.

Usage: python make_fixture.py <out.mp4>
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

GROUND_TRUTH = {
    "kill": (20.0, 26.0),
    "death": (47.0, 52.0),
    "funny": (75.0, 82.0),
    "decoy": (95.0, 97.5),
    "clutch": (110.0, 120.0),
}
FONT = "C\\:/Windows/Fonts/arial.ttf"


def _txt(text, x, y, size, color, t0, t1, box=False):
    b = ":box=1:boxcolor=black@0.55:boxborderw=8" if box else ""
    return (
        f"drawtext=fontfile='{FONT}':text='{text}':x={x}:y={y}:fontsize={size}"
        f":fontcolor={color}{b}:enable='between(t,{t0},{t1})'"
    )


def video_filters() -> str:
    f = []
    # Crosshair
    f.append("drawbox=x=636:y=356:w=8:h=8:color=white@0.9:t=fill")
    f.append("drawbox=x=620:y=359:w=40:h=2:color=white@0.7:t=fill")
    # Health bar: full green, then dark overlay steps as health drains
    f.append("drawbox=x=38:y=668:w=304:h=24:color=white@0.5:t=2")
    f.append("drawbox=x=40:y=670:w=300:h=20:color=0x30c050@0.9:t=fill")
    drains = [
        # (t0, t1, covered px of the right side)
        (47.0, 47.3, 100), (47.3, 47.6, 200), (47.6, 48.0, 300),  # death
        (53.0, 140.0, -1),  # respawn: handled by the restore below
        (110.0, 112.0, 120), (112.0, 115.0, 200), (115.0, 120.0, 255),  # clutch
    ]
    for t0, t1, px in drains:
        if px < 0:
            continue
        end = t1 if t1 < 53 else t1
        f.append(
            f"drawbox=x={340 - px}:y=670:w={px}:h=20:color=0x181818@1:t=fill:"
            f"enable='between(t,{t0},{end if t0 < 100 else 120})'"
        )
    # After death the bar stays empty until respawn at 53 (covered above to 48);
    # keep it empty through 53.
    f.append("drawbox=x=40:y=670:w=300:h=20:color=0x181818@1:t=fill:enable='between(t,48,53)'")
    # Minimap
    f.append("drawbox=x=1050:y=20:w=210:h=210:color=black@0.5:t=fill")
    f.append("drawbox=x=1050:y=20:w=210:h=210:color=white@0.6:t=2")
    f.append("drawbox=x=1150:y=120:w=8:h=8:color=cyan@0.9:t=fill")
    # Ammo counter
    f.append(_txt("30 / 120", "1090", "672", 28, "white", 0, 150))
    # Kill feed (top-left)
    for i, t in enumerate((20.6, 22.6, 24.6)):
        f.append(_txt(f"YOU ELIMINATED P{i + 1}", 30, 30 + 34 * i, 24, "white", t, t + 3.5, True))
    for i, t in enumerate((111.5, 114.5, 117.0)):
        f.append(_txt(f"YOU ELIMINATED E{i + 1}", 30, 30 + 34 * i, 24, "white", t, t + 3.5, True))
    # Banners
    f.append(_txt("DOUBLE ELIMINATION", "(w-text_w)/2", 150, 64, "yellow", 22.4, 24.4, True))
    f.append(_txt("ELIMINATED", "(w-text_w)/2", 150, 80, "red", 48.2, 50.6, True))
    f.append(_txt("VICTORY", "(w-text_w)/2", 150, 90, "yellow", 118.0, 120.6, True))
    # Red flash on death, muzzle flashes on kills
    f.append("drawbox=x=0:y=0:w=1280:h=720:color=red@0.35:t=fill:enable='between(t,47.0,48.2)'")
    for t in (20.5, 22.5, 24.5, 111.4, 114.4, 116.9):
        f.append(f"drawbox=x=560:y=300:w=160:h=120:color=white@0.8:t=fill:enable='between(t,{t},{t + 0.12})'")
    # Decoy: distant explosion at the frame edge (small, off-centre)
    f.append("drawbox=x=1200:y=300:w=60:h=60:color=orange@0.95:t=fill:enable='between(t,95,96.2)'")
    f.append("drawbox=x=1215:y=315:w=30:h=30:color=yellow@0.95:t=fill:enable='between(t,95,95.6)'")
    return ",".join(f)


def audio_expr() -> str:
    terms = ["0.025*(random(0)-0.5)", "0.02*sin(2*PI*110*t)"]

    def burst(t0, amp, decay, freq=None):
        if freq:
            return f"if(gte(t,{t0}),{amp}*sin(2*PI*{freq}*t)*exp(-{decay}*(t-{t0})),0)"
        return f"if(gte(t,{t0}),{amp}*(random(0)-0.5)*2*exp(-{decay}*(t-{t0})),0)"

    for t in (20.5, 22.5, 24.5, 111.4, 114.4, 116.9):
        terms.append(burst(t, 0.45, 25))
    terms.append(burst(48.0, 0.4, 6, 90))  # death thud
    for i in range(8):  # springy blips during the tumble
        terms.append(burst(75.5 + i * 0.8, 0.3, 14, 500 + 80 * i))
    terms.append(burst(95.0, 0.98, 2.2))  # DECOY: loudest burst in the file
    terms.append(burst(118.0, 0.35, 5, 220))  # victory sting
    return "+".join(terms)


def build(out: Path) -> None:
    vf = video_filters()
    player = (
        "[1:v]format=rgba[pl];[0:v]" + vf + "[bg];"
        "[bg][pl]overlay=x='if(between(t,75,82),610+220*sin(7*t),610)':"
        "y='if(between(t,75,82),380-130*abs(sin(4.5*t)),380)',"
        "crop=1240:680:x='20+if(between(t,110,120),14*sin(55*t),0)':"
        "y='20+if(between(t,110,120),10*cos(47*t),0)',scale=1280:720,format=yuv420p[v]"
    )
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "graph.txt"
        script.write_text(player, encoding="utf-8")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "gradients=s=1280x720:r=30:d=150:speed=0.004:c0=0x24384c:c1=0x4a5a34:nb_colors=2",
            "-f", "lavfi", "-i", "color=c=0xd04040:s=60x60:r=30:d=150",
            "-f", "lavfi", "-i", f"aevalsrc='{audio_expr()}':s=44100:d=150",
            "-/filter_complex", str(script),
            "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-t", "150", "-movflags", "+faststart",
            str(out),
        ]
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    build(Path(sys.argv[1]))
    print("wrote", sys.argv[1])
