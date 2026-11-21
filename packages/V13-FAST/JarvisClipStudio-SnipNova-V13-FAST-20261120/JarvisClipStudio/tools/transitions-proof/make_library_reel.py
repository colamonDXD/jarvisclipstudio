#!/usr/bin/env python3
"""Proof reel for the extended transition library.

Renders every entry of ``backend/src/media/transition_library.py`` the same
way Round 1 rendered the 58 built-in ``xfade`` names: two labelled
1080x1920 cards, a 0.5 s transition, a real ``libx264`` encode. The
built-in names are re-rendered alongside so both sets are measured with
one rule.

For every clip the tool records:

* render wall time,
* probe duration / geometry,
* a 32x32 grey midpoint signature and a three-frame (25/50/75 %) one,
* the mean absolute signature distance to every other clip.

A variant counts as distinct only when its three-frame signature differs
from every other clip by more than ``SEQUENCE_THRESHOLD`` (1.0 on the
0..255 grey scale). Near-identical groups are reported, not hidden.

Usage (from the repository root, with the backend venv's python):

    python tools/transitions-proof/make_library_reel.py
    python tools/transitions-proof/make_library_reel.py --only iris_
    python tools/transitions-proof/make_library_reel.py --skip-builtins
    python tools/transitions-proof/make_library_reel.py --reuse old/library.json

``--reuse`` copies signatures, frames and clips for entries the earlier
run completed, so an incremental registry change only renders the new
entries.

Exit code is 1 when any transition fails to render.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]
LIBRARY = REPO_ROOT / "backend" / "src" / "media" / "transition_library.py"

sys.path.insert(0, str(TOOLS_DIR))
import make_reel as reel  # noqa: E402

SEQUENCE_THRESHOLD = 1.0


def load_registry() -> dict:
    """Import the stdlib-only registry without the application package."""
    spec = importlib.util.spec_from_file_location(
        "transition_library_proof", LIBRARY
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {LIBRARY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.TRANSITIONS


def render_one(
    name: str,
    builder,
    output: Path,
    font: Path | None,
) -> dict:
    """Render one join with the Round 1 cards and probe it."""
    cards = (
        reel._card_chain(0, name, "A -> B", font)
        + "[a];"
        + reel._card_chain(1, name, "B side", font)
        + "[b];"
    )
    if builder is None:
        graph = (
            cards
            + f"[a][b]xfade=transition={name}"
            f":duration={reel.TRANSITION_SECONDS}"
            f":offset={reel.OFFSET_SECONDS}[v]"
        )
        label = "[v]"
    else:
        graph = cards + builder(
            reel.TRANSITION_SECONDS,
            reel.OFFSET_SECONDS,
            reel.WIDTH,
            reel.HEIGHT,
            reel.FPS,
        )
        label = "[out]"

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"color=c=0x1E3A8A:s={reel.WIDTH}x{reel.HEIGHT}"
        f":r={reel.FPS}:d={reel.CARD_SECONDS}",
        "-f", "lavfi", "-i",
        f"color=c=0xD97706:s={reel.WIDTH}x{reel.HEIGHT}"
        f":r={reel.FPS}:d={reel.CARD_SECONDS}",
        "-filter_complex", graph,
        "-map", label,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output),
    ]
    started = time.perf_counter()
    rendered = reel.subprocess.run(
        command, capture_output=True, text=True
    )
    seconds = time.perf_counter() - started
    if rendered.returncode != 0:
        return {
            "rendered": False,
            "seconds": round(seconds, 3),
            "error": (rendered.stderr or "").strip().splitlines()[-1:]
            or [f"ffmpeg exited {rendered.returncode}"],
        }
    probe = reel.subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,nb_frames",
            "-show_entries", "format=duration",
            "-of", "json", str(output),
        ],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return {
            "rendered": False,
            "seconds": round(seconds, 3),
            "error": (probe.stderr or "").strip().splitlines()[-1:],
        }
    payload = json.loads(probe.stdout or "{}")
    video = next(
        (
            stream
            for stream in payload.get("streams", [])
            if stream.get("codec_type") == "video"
        ),
        None,
    )
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    geometry_ok = (
        video is not None
        and int(video.get("width") or 0) == reel.WIDTH
        and int(video.get("height") or 0) == reel.HEIGHT
    )
    duration_ok = abs(duration - reel.EXPECTED_DURATION) <= 0.1
    return {
        "rendered": geometry_ok and duration_ok,
        "seconds": round(seconds, 3),
        "duration": duration,
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "frames": (video or {}).get("nb_frames"),
        "error": None
        if geometry_ok and duration_ok
        else f"bad probe output: {json.dumps(payload)[:400]}",
    }


def _probe_duration(clip: Path) -> float:
    probe = reel.subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(clip),
        ],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return 0.0
    payload = json.loads(probe.stdout or "{}")
    return float((payload.get("format") or {}).get("duration") or 0.0)


def _distance_matrix(names: list[str], signatures: dict[str, bytes]):
    """Pairwise mean absolute distance, numpy when available."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - fallback for minimal envs
        size = len(names)
        matrix = [[0.0] * size for _ in range(size)]
        for i, first in enumerate(names):
            for j in range(i + 1, size):
                value = reel.mean_abs_distance(
                    signatures[first], signatures[names[j]]
                )
                matrix[i][j] = matrix[j][i] = value
        return matrix

    stacked = np.frombuffer(
        b"".join(signatures[name] for name in names), dtype=np.uint8
    ).reshape(len(names), -1)
    matrix = np.zeros((len(names), len(names)), dtype=np.float64)
    for index in range(len(names)):
        matrix[index] = np.abs(
            stacked.astype(np.int16) - stacked[index].astype(np.int16)
        ).mean(axis=1)
    return matrix


def _groups_from_matrix(
    names: list[str], matrix, threshold: float
) -> list[list[str]]:
    parent = {name: name for name in names}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for i, first in enumerate(names):
        for j in range(i + 1, len(names)):
            if matrix[i][j] <= threshold:
                root_a, root_b = find(first), find(names[j])
                if root_a != root_b:
                    parent[root_b] = root_a
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(find(name), []).append(name)
    return list(groups.values())


def _near_map(
    names: list[str], matrix, threshold: float
) -> dict[str, list[tuple[str, float]]]:
    near: dict[str, list[tuple[str, float]]] = {name: [] for name in names}
    for i, first in enumerate(names):
        for j, second in enumerate(names):
            if i >= j:
                continue
            value = float(matrix[i][j])
            if value <= threshold:
                near[first].append((second, value))
                near[second].append((first, value))
    for pairs in near.values():
        pairs.sort(key=lambda item: item[1])
    return near


def write_table(
    output: Path,
    entries: list[dict],
    results: dict[str, dict],
    near: dict[str, list[tuple[str, float]]],
    groups: list[list[str]],
) -> None:
    lines = [
        "| Transition | Source | Family | Rendered OK | Seconds | Duration (s) | Near-identical |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        name = entry["name"]
        result = results[name]
        if not result.get("rendered"):
            lines.append(
                f"| {name} | {entry['source']} | {entry['family']} | "
                f"FAILED | {result.get('seconds', '-')} | - | "
                f"{result.get('error')} |"
            )
            continue
        pairs = near.get(name, [])
        detail = (
            ", ".join(f"{other} ({value:.2f})" for other, value in pairs)
            if pairs
            else "-"
        )
        lines.append(
            f"| {name} | {entry['source']} | {entry['family']} | OK | "
            f"{result['seconds']:.3f} | {result['duration']:.3f} | {detail} |"
        )
    lines.append("")
    lines.append(
        "Near-identical uses the three-frame (25/50/75 %) 32x32 grey "
        f"signature and flags pairs within {SEQUENCE_THRESHOLD:.2f} "
        "(mean absolute difference on 0..255)."
    )
    lines.append("")
    lines.append("### Near-identical groups")
    lines.append("")
    meaningful = sorted(
        tuple(sorted(group)) for group in groups if len(group) > 1
    )
    if not meaningful:
        lines.append("None.")
    else:
        for group in meaningful:
            lines.append(f"- {', '.join(group)}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=TOOLS_DIR / "library-out",
        help="output directory (default: tools/transitions-proof/library-out)",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="substring filter applied to both sets before rendering",
    )
    parser.add_argument(
        "--skip-builtins",
        action="store_true",
        help="render only the extended registry",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=SEQUENCE_THRESHOLD,
        help="three-frame near-identical threshold (default 1.0)",
    )
    parser.add_argument(
        "--reuse",
        type=Path,
        default=None,
        help="reuse signatures, frames and clips from an earlier library.json",
    )
    args = parser.parse_args(argv)

    registry = load_registry()
    entries: list[dict] = []
    if not args.skip_builtins:
        for name in reel.load_transitions(reel.EDL_RENDER):
            entries.append(
                {
                    "name": name,
                    "source": "builtin",
                    "family": reel.family_of(name),
                    "builder": None,
                }
            )
    for name, spec in registry.items():
        entries.append(
            {
                "name": name,
                "source": "library",
                "family": spec.family,
                "builder": spec.builder,
                "description": spec.description,
            }
        )
    if args.only:
        entries = [entry for entry in entries if args.only in entry["name"]]

    font = reel.find_font()
    out_dir = args.out.resolve()
    clips_dir = out_dir / "clips"
    frames_dir = out_dir / "frames"
    clips_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    midpoint_signatures: dict[str, bytes] = {}
    sequence_signatures: dict[str, bytes] = {}
    frames: dict[str, Path] = {}
    reused: dict[str, dict] = {}
    reuse_payload: dict = {}
    if args.reuse:
        reuse_payload = json.loads(args.reuse.read_text(encoding="utf-8"))
        reuse_root = args.reuse.parent
        for name in reuse_payload.get("rendered_ok", []):
            signatures = (reuse_payload.get("signatures") or {}).get(name)
            frame = reuse_root / "frames" / f"{name}.png"
            clip = reuse_root / "clips" / f"{name}.mp4"
            if signatures and frame.is_file() and clip.is_file():
                reused[name] = {
                    "midpoint": base64.b64decode(signatures["midpoint"]),
                    "sequence": base64.b64decode(signatures["sequence"]),
                    "frame": frame,
                    "clip": clip,
                }

    print(
        f"Rendering {len(entries)} transitions at "
        f"{reel.WIDTH}x{reel.HEIGHT}, {reel.TRANSITION_SECONDS}s "
        f"({len(reused)} reused from {args.reuse}) ..."
        if args.reuse
        else f"Rendering {len(entries)} transitions at "
        f"{reel.WIDTH}x{reel.HEIGHT}, {reel.TRANSITION_SECONDS}s ..."
    )
    for index, entry in enumerate(entries, start=1):
        name = entry["name"]
        clip = clips_dir / f"{name}.mp4"
        if name in reused:
            reuse = reused[name]
            shutil.copy2(reuse["frame"], frames_dir / f"{name}.png")
            midpoint_signatures[name] = reuse["midpoint"]
            sequence_signatures[name] = reuse["sequence"]
            frames[name] = frames_dir / f"{name}.png"
            result = {
                "rendered": True,
                "seconds": reuse_payload.get("render_seconds", {}).get(name),
                "duration": _probe_duration(reuse["clip"]),
                "reused": True,
            }
            results[name] = result
            print(
                f"  [{index:03d}/{len(entries)}] {name:26s} REUSE  "
                f"{result['duration']:6.3f}s"
            )
            continue
        result = render_one(name, entry["builder"], clip, font)
        if result.get("rendered"):
            try:
                reel.extract_midpoint_png(clip, frames_dir / f"{name}.png")
                midpoint_signatures[name] = reel._raw_frame_signature(
                    clip, reel.MIDPOINT_FRAME, reel.SIGNATURE_SIZE
                )
                sequence_signatures[name] = reel._sequence_signature(clip)
                frames[name] = frames_dir / f"{name}.png"
            except RuntimeError as exc:
                result["rendered"] = False
                result["error"] = str(exc)
        results[name] = result
        status = "OK" if result.get("rendered") else "FAILED"
        duration = (
            f"{result['duration']:.3f}s"
            if result.get("duration") is not None
            else "-"
        )
        print(
            f"  [{index:03d}/{len(entries)}] {name:26s} {status:6s} "
            f"{duration:>8s} {result.get('seconds', 0):6.2f}s"
        )

    working = [entry["name"] for entry in entries if results[entry["name"]].get("rendered")]
    if working:
        matrix = _distance_matrix(working, sequence_signatures)
        groups = _groups_from_matrix(working, matrix, args.threshold)
        near = _near_map(working, matrix, args.threshold)
    else:
        matrix = []
        groups = []
        near = {}

    distinct = [group[0] for group in groups if len(group) == 1]
    duplicates = sum(len(group) - 1 for group in groups if len(group) > 1)

    by_source: dict[str, dict[str, int]] = {}
    for entry in entries:
        if entry["name"] not in working:
            continue
        bucket = by_source.setdefault(
            entry["source"], {"rendered": 0, "distinct": 0}
        )
        bucket["rendered"] += 1
        if any(entry["name"] in group and len(group) == 1 for group in groups):
            bucket["distinct"] += 1

    sheets = []
    family_names: dict[str, list[str]] = {}
    for entry in entries:
        family_names.setdefault(entry["family"], []).append(entry["name"])
    for family, names in family_names.items():
        sheet = reel.contact_sheet(
            family,
            names,
            frames,
            out_dir / f"contact-{family}.png",
        )
        if sheet is not None:
            sheets.append(sheet)

    payload = {
        "width": reel.WIDTH,
        "height": reel.HEIGHT,
        "fps": reel.FPS,
        "card_seconds": reel.CARD_SECONDS,
        "transition_seconds": reel.TRANSITION_SECONDS,
        "offset_seconds": reel.OFFSET_SECONDS,
        "expected_duration": reel.EXPECTED_DURATION,
        "midpoint_frame": reel.MIDPOINT_FRAME,
        "sequence_frames": list(reel.SEQUENCE_FRAMES),
        "signature_size": reel.SEQUENCE_SIGNATURE_SIZE,
        "threshold": args.threshold,
        "rendered_ok": working,
        "failed": {
            entry["name"]: results[entry["name"]].get("error")
            for entry in entries
            if not results[entry["name"]].get("rendered")
        },
        "render_seconds": {
            entry["name"]: results[entry["name"]].get("seconds")
            for entry in entries
        },
        "reused_from": str(args.reuse) if args.reuse else None,
        "reused": sorted(name for name in working if name in reused),
        "total_rendered": len(working),
        "total_distinct": len(distinct),
        "near_identical_groups": [
            group for group in groups if len(group) > 1
        ],
        "by_source": by_source,
        "entries": [
            {
                "name": entry["name"],
                "source": entry["source"],
                "family": entry["family"],
                "description": entry.get("description"),
                "near_identical": near.get(entry["name"], []),
            }
            for entry in entries
        ],
        "signatures": {
            name: {
                "midpoint": base64.b64encode(
                    midpoint_signatures[name]
                ).decode("ascii"),
                "sequence": base64.b64encode(
                    sequence_signatures[name]
                ).decode("ascii"),
            }
            for name in working
        },
    }
    (out_dir / "library.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    write_table(
        out_dir / "table.md", entries, results, near, groups
    )

    print()
    print(f"Rendered OK:   {len(working)}/{len(entries)}")
    print(f"Distinct:      {len(distinct)} of {len(working)} "
          f"(delta > {args.threshold:.2f} vs every other three-frame signature)")
    print(f"Duplicates:    {duplicates} clip(s) in "
          f"{sum(1 for group in groups if len(group) > 1)} near-identical group(s)")
    for source, counts in sorted(by_source.items()):
        print(f"  {source:8s} rendered {counts['rendered']:3d}  "
              f"distinct {counts['distinct']:3d}")
    print(f"Table:         {out_dir / 'table.md'}")
    print(f"Data:          {out_dir / 'library.json'}")
    if sheets:
        print(f"Contact sheets: {len(sheets)} under {out_dir}")
    return 0 if len(working) == len(entries) else 1


if __name__ == "__main__":
    sys.exit(main())
