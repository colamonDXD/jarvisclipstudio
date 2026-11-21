"""SYNTHETIC streamer-VOD generator for the reaction test matrix.

NOT real footage. It composes a game view (HUD, flashes, banners, shake), a
facecam (a cartoon head whose mouth and lean follow the voice) and audio (game
bed with chord pad + explosions, and synthetic voices: calm speech, excited
speech, laughter pulses, a scream). It exists to check logic and false
positives; it can not tell us how real streamers sound. Real-VOD validation is
a separate, still-open step.

Usage: python make_streamer_vod.py <out_dir> [seed] [variant]
  variant 0 = tuning set, 1 = held-out set (different pitches/levels/timings)
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import cv2
import numpy as np

W, H, FPS, SR = 640, 360, 24, 16000
FACE = (470, 226, 160, 120)  # x, y, w, h of the facecam box

# (name, length_sec, role)  role: pos_corr | pos_comm | pos_masked | neg | weak
PLAN = [
    ("ordinary_a", 40, "neg"),
    ("action_no_reaction", 28, "neg"),
    ("laugh_at_event", 28, "pos_corr"),
    ("ordinary_b", 25, "neg"),
    ("facecam_silent", 28, "weak"),
    ("scream_shock", 28, "pos_corr"),
    ("funny_commentary", 28, "pos_comm"),
    ("ordinary_c", 25, "neg"),
    ("masked_laugh", 28, "pos_masked"),
    ("unrelated_laugh", 28, "pos_comm"),
    ("ordinary_d", 40, "neg"),
]


def _harm(f0, t, n=14, tilt=1.0, formants=((700, 400), (1800, 600))):
    """Harmonic stack with simple formant emphasis. f0 may be an array."""
    phase = 2 * np.pi * np.cumsum(f0) / SR
    out = np.zeros_like(t)
    for k in range(1, n + 1):
        fk = f0 * k
        g = 1.0 / (k ** tilt)
        for fc, bw in formants:
            g = g * (1 + 2.0 * np.exp(-((fk - fc) / bw) ** 2)) if False else g
        env = sum(np.exp(-((fk - fc) / bw) ** 2) for fc, bw in formants) + 0.15
        out += g * env * np.sin(k * phase)
    return out / max(1e-6, np.abs(out).max())


def voice(kind, dur, rng, var):
    n = int(dur * SR)
    t = np.arange(n) / SR
    shift = 1.0 if var == 0 else 1.18  # held-out: a different speaker
    if kind == "calm":
        f0 = (122 * shift) + 8 * np.sin(2 * np.pi * 0.6 * t)
        syll = np.clip(np.sin(2 * np.pi * (3.8 + 0.4 * var) * t) + 0.25, 0, 1) ** 0.8
        words = (np.sin(2 * np.pi * 0.35 * t + rng.uniform(0, 6)) > -0.4).astype(float)
        return 0.11 * _harm(f0, t) * syll * np.convolve(words, np.ones(400) / 400, "same")
    if kind == "excited":
        f0 = (185 * shift) + 38 * np.sin(2 * np.pi * 0.9 * t) + 25 * t / dur
        syll = np.clip(np.sin(2 * np.pi * 4.6 * t) + 0.35, 0, 1) ** 0.7
        return (0.26 + 0.1 * t / dur) * _harm(f0, t) * syll
    if kind == "laugh":
        rate = 5.2 if var == 0 else 4.6
        f0 = (250 * shift) - 40 * (t / dur) + 12 * np.sin(2 * np.pi * 7 * t)
        phase = (t * rate) % 1.0
        pulse = np.exp(-phase * 9.0) * (phase < 0.6)
        ramp = np.minimum(1, t / 0.4) * (1 - 0.35 * t / dur)
        breath = rng.normal(0, 0.08, n) * pulse
        return 0.34 * (_harm(f0, t, 10) * pulse * ramp + breath)
    if kind == "scream":
        f0 = (640 * shift) + 120 * t / dur + 18 * np.sin(2 * np.pi * 6 * t)
        env = np.minimum(1, t / 0.2) * np.minimum(1, (dur - t) / 0.5)
        return 0.48 * _harm(f0, t, 9, 0.7) * env
    raise ValueError(kind)


def game_audio(dur, rng, var):
    n = int(dur * SR)
    t = np.arange(n) / SR
    pad = 0.025 * (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 277 * t) + np.sin(2 * np.pi * 330 * t))
    hum = 0.025 * np.sin(2 * np.pi * 95 * t) * (1 + 0.2 * np.sin(2 * np.pi * 0.3 * t))
    noise = np.convolve(rng.normal(0, 1, n), np.ones(8) / 8, "same") * 0.012
    return 0.45 * (pad + hum + noise)


def boom(dur, amp, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = rng.normal(0, 1, n)
    x = np.convolve(x, np.ones(12) / 12, "same")  # low-passed noise: not voiced
    return amp * x * np.exp(-t * 3.0) * 2.2


def draw_scene(frame, t_local, ev, shake):
    """frame: BGR canvas already holding the base scene."""
    out = frame.copy()
    # HUD
    cv2.rectangle(out, (10, 330), (150, 345), (60, 190, 80), -1)
    cv2.rectangle(out, (520, 8), (632, 100), (40, 40, 40), 1)
    cv2.putText(out, "30/120", (360, 350), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    for kind, a, b in ev:
        if not (a <= t_local <= b):
            continue
        if kind == "flash":
            cv2.rectangle(out, (0, 0), (W, H), (60, 60, 255), -1) if False else None
            out[:] = (0.6 * out + 0.4 * np.array([70, 70, 255])).astype(np.uint8)
        elif kind == "banner":
            cv2.putText(out, "WASTED", (150, 110), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (40, 40, 255), 5)
        elif kind == "feed":
            cv2.putText(out, "VEHICLE DESTROYED", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        elif kind == "boomflash":
            cv2.circle(out, (300, 190), 70, (80, 160, 255), -1)
        elif kind == "tumble":
            x = int(300 + 120 * np.sin(8 * t_local)); y = int(200 - 70 * abs(np.sin(5 * t_local)))
            cv2.rectangle(out, (x, y), (x + 40, y + 40), (60, 60, 210), -1)
    if shake:
        M = np.float32([[1, 0, 8 * np.sin(55 * t_local)], [0, 1, 6 * np.cos(47 * t_local)]])
        out = cv2.warpAffine(out, M, (W, H))
    return out


def draw_face(frame, openness, lean, bob, covered):
    x0, y0, w, h = FACE
    cv2.rectangle(frame, (x0, y0), (x0 + w, y0 + h), (90, 80, 70), -1)
    cx = int(x0 + w / 2 + bob * 6)
    cy = int(y0 + h / 2 + 4 - lean * 10)
    rx, ry = int(30 + lean * 10), int(38 + lean * 12)
    cv2.ellipse(frame, (cx, cy), (rx, ry), 0, 0, 360, (140, 180, 225), -1)
    cv2.circle(frame, (cx - 12, cy - 10), 4, (30, 30, 30), -1)
    cv2.circle(frame, (cx + 12, cy - 10), 4, (30, 30, 30), -1)
    mh = int(2 + openness * 22)
    cv2.ellipse(frame, (cx, cy + 16), (11 + int(openness * 6), mh), 0, 0, 360, (30, 30, 120), -1)
    if covered:
        cv2.rectangle(frame, (cx - 30, cy - 20), (cx + 30, cy + 25), (120, 150, 200), -1)


def build(out_dir: Path, seed: int, var: int):
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = sum(p[1] for p in PLAN)
    audio = np.zeros(int(total * SR) + SR)
    truth = []
    # per-scenario plan: events (game), face timeline callbacks, voice placements
    frames_meta = []  # list of (t_abs, ev, shake, faceparams fn)
    t0 = 0.0
    sc = []
    for name, length, role in PLAN:
        a0 = int(t0 * SR)
        n = int(length * SR)
        audio[a0:a0 + n] += game_audio(length, rng, var)
        ev, shake, face = [], [], []   # face: (a, b, kind)
        voices = []  # (rel_start, kind, dur, gain)
        gt = None  # (rel_start, rel_end) of the thing that should be clipped
        if name.startswith("ordinary"):
            for s in range(2, int(length) - 8, 11):
                voices.append((s + rng.uniform(0, 2), "calm", 7.0, 1.0))
        elif name == "action_no_reaction":
            ev = [("feed", 10, 14), ("flash", 10, 10.3), ("banner", 11, 13.5), ("boomflash", 17, 18.4), ("flash", 17, 17.2)]
            shake = [(10, 14)]
            audio[a0 + int(10 * SR): a0 + int(10 * SR) + int(1.6 * SR)] += boom(1.6, 0.9, rng)[: int(1.6 * SR)]
            audio[a0 + int(17 * SR): a0 + int(17 * SR) + int(1.6 * SR)] += boom(1.6, 1.0, rng)[: int(1.6 * SR)]
            voices.append((3, "calm", 20, 1.0))
            gt = (9, 18.5)
        elif name == "laugh_at_event":
            ev = [("feed", 9, 13), ("flash", 9, 9.3), ("banner", 9.5, 12), ("tumble", 9.5, 13)]
            audio[a0 + int(9 * SR): a0 + int(9 * SR) + int(1.6 * SR)] += boom(1.6, 0.8, rng)[: int(1.6 * SR)]
            voices += [(2, "calm", 6.5, 1.0), (10.6, "laugh", 6.0, 1.0)]
            face = [(10.6, 16.6, "laugh")]
            gt = (10.6, 16.6)
        elif name == "scream_shock":
            ev = [("flash", 10, 10.5), ("boomflash", 10, 11.5), ("shake", 10, 12)]
            shake = [(10, 12.5)]
            audio[a0 + int(10 * SR): a0 + int(10 * SR) + int(1.8 * SR)] += boom(1.8, 0.95, rng)[: int(1.8 * SR)]
            voices += [(2, "calm", 6.0, 1.0), (10.4, "scream", 2.6, 1.0), (13.2, "excited", 4.0, 1.0)]
            face = [(10.4, 13.0, "scream")]
            gt = (10.4, 13.0)
        elif name == "funny_commentary":
            voices += [(2, "calm", 7.0, 1.0), (10, "excited", 7.0, 1.0), (13.5, "laugh", 2.5, 0.7)]
            face = [(10, 16, "laugh")]
            gt = (10, 16)
        elif name == "unrelated_laugh":
            voices += [(2, "calm", 7.0, 1.0), (12, "laugh", 4.5, 0.9)]
            face = [(12, 16.5, "laugh")]
            gt = (12, 16.5)
        elif name == "facecam_silent":
            ev = [("feed", 10, 14), ("flash", 10, 10.3), ("banner", 11, 13)]
            face = [(10.4, 13.5, "lunge")]
            voices.append((2, "calm", 6.0, 1.0))
            gt = (10.4, 13.5)
        elif name == "masked_laugh":
            ev = [("boomflash", 8, 22), ("flash", 8, 8.2)]
            shake = [(8, 14)]
            for s in np.arange(8, 21, 1.1):
                i = a0 + int(s * SR)
                audio[i:i + int(1.5 * SR)] += boom(1.5, 0.55, rng)[: len(audio[i:i + int(1.5 * SR)])]
            voices += [(2, "calm", 5.0, 1.0), (11, "laugh", 6.0, 0.6)]
            face = [(11, 17, "laugh")]
            gt = (11, 17)
        for rel, kind, dur, gain in voices:
            seg = voice(kind, dur, rng, var) * gain * 1.6
            i = a0 + int(rel * SR)
            audio[i:i + len(seg)] += seg[: len(audio[i:i + len(seg)])]
        sc.append(dict(name=name, role=role, t0=t0, length=length, ev=ev, shake=shake, face=face, voices=voices))
        if gt:
            truth.append(dict(name=name, role=role, start=t0 + gt[0], end=t0 + gt[1]))
        else:
            truth.append(dict(name=name, role=role, start=t0, end=t0 + length))
        t0 += length

    audio = np.clip(audio * 0.9, -1, 1)
    wav = out_dir / "vod.wav"
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(SR)
        f.writeframes((audio[: int(total * SR)] * 32767).astype(np.int16).tobytes())

    # video
    base = np.zeros((H, W, 3), np.uint8)
    yy, xx = np.mgrid[0:H, 0:W]
    base[..., 0] = (60 + 40 * xx / W).astype(np.uint8)
    base[..., 1] = (80 + 40 * yy / H).astype(np.uint8)
    base[..., 2] = 60
    mp4 = out_dir / "vod.mp4"
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest", str(mp4)],
        stdin=subprocess.PIPE)
    for s in sc:
        nfr = int(s["length"] * FPS)
        for k in range(nfr):
            t = k / FPS
            drift = np.roll(base, int(t * 3) % W, axis=1)
            fr = draw_scene(drift, t, s["ev"], any(a <= t <= b for a, b in s["shake"]))
            openness, lean, bob, covered = 0.1, 0.0, 0.0, False
            # mouth follows whichever voice is active
            for rel, kind, dur, gain in s["voices"]:
                if rel <= t < rel + dur:
                    tt = t - rel
                    if kind == "calm":
                        openness = 0.15 + 0.25 * max(0, np.sin(2 * np.pi * 3.8 * tt))
                    elif kind == "excited":
                        openness = 0.3 + 0.35 * max(0, np.sin(2 * np.pi * 4.6 * tt))
                    elif kind == "laugh":
                        openness = 0.5 + 0.5 * max(0, np.sin(2 * np.pi * 5.2 * tt))
                    elif kind == "scream":
                        openness = 1.0
            for a, b, kind in s["face"]:
                if a <= t <= b:
                    if kind == "laugh":
                        lean = 0.6 * np.sin(2 * np.pi * 2.3 * (t - a)); bob = np.sin(2 * np.pi * 5.0 * (t - a))
                    elif kind == "scream":
                        lean = 1.0
                    elif kind == "lunge":
                        lean = 1.0 if (t - a) < 1.5 else 0.2; covered = (t - a) > 1.8; openness = 0.1
            draw_face(fr, openness, lean, bob, covered)
            proc.stdin.write(fr.tobytes())
    proc.stdin.close(); proc.wait()
    (out_dir / "truth.json").write_text(json.dumps(dict(seed=seed, variant=var, total=total, plan=truth), indent=1))
    print("wrote", mp4, f"{total}s")


if __name__ == "__main__":
    build(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 1,
          int(sys.argv[3]) if len(sys.argv) > 3 else 0)
