"""Extended, CPU-only transition library.

The product already exposes 58 built-in FFmpeg ``xfade`` names. Those
names are useful but several families look alike, and a few effects an
editor expects -- iris shapes, blinds, glitches, light leaks -- do not
exist there at all.

This module is a registry, not a renderer. Every entry is a
:class:`TransitionSpec` whose ``builder`` returns one FFmpeg filter-graph
fragment: it consumes two equal-size labelled clips ``[a]`` and ``[b]``
and produces ``[out]``. The caller decides how the clips are decoded and
encoded. The module itself is standard-library only; it does not import
the application or touch the network.

Two implementation styles are used:

* ``xfade=transition=custom`` with an expression. FFmpeg evaluates the
  expression per pixel. Its progress variable ``P`` runs from 1.0 at the
  start of the transition to 0.0 at the end (that is FFmpeg's convention;
  ``A`` is the outgoing clip, ``B`` the incoming one). Most geometric
  reveals are built this way.
* A small graph of ordinary filters (``overlay``, ``zoompan``,
  ``rotate``, ``rgbashift``, ``noise``, ``gblur``, ``blend``) for motion,
  glitch and light effects that an xfade expression cannot express.

All builders take the same five values: ``duration`` (transition
seconds), ``offset`` (when the transition starts in the first clip),
``width``, ``height`` and ``fps``. The caller supplies two clips of
``offset + duration`` seconds; the expected output duration is
``2 * offset + duration``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict

Builder = Callable[..., str]


@dataclass(frozen=True)
class TransitionSpec:
    """One registry entry."""

    name: str
    family: str
    description: str
    builder: Builder


TRANSITIONS: Dict[str, TransitionSpec] = {}


def _min(*parts: str) -> str:
    """FFmpeg's eval only has binary ``min``; fold a list into it."""
    value = parts[0]
    for part in parts[1:]:
        value = f"min({value},{part})"
    return value


def _register(
    name: str, family: str, description: str, builder: Builder
) -> None:
    if name in TRANSITIONS:
        raise ValueError(f"duplicate transition name: {name}")
    TRANSITIONS[name] = TransitionSpec(name, family, description, builder)


def _num(value: float) -> str:
    """Short, locale-independent number formatting for filter graphs."""
    return f"{value:.6g}"


def _soft(value: str, width: float = 0.08) -> str:
    """A 0..1 soft edge (smoothstep) around ``value``."""
    u = f"clip(({value})/{_num(width)},0,1)"
    return f"(({u})*({u})*(3-2*({u})))"


def _mix(mask: str) -> str:
    """Outgoing clip weighted by ``mask`` (1 = A, 0 = B)."""
    return f"A*({mask})+B*(1-({mask}))"


def _xfade_builder(expr: str) -> Builder:
    def builder(
        duration: float,
        offset: float,
        width: int,
        height: int,
        fps: float,
    ) -> str:
        return (
            f"[a][b]xfade=transition=custom:expr='{expr}'"
            f":duration={_num(duration)}:offset={_num(offset)}[out]"
        )

    return builder


def _add_custom(
    name: str, family: str, description: str, expr: str
) -> None:
    _register(name, family, description, _xfade_builder(expr))


def _add_iris() -> None:
    variants = (
        ("iris_soft_center", 0.5, 0.5, 0.5, 0.5, 0.06),
        ("iris_soft_tl", 0.28, 0.28, 0.72, 0.72, 0.055),
        ("iris_soft_tr", 0.72, 0.28, 0.72, 0.72, 0.055),
        ("iris_soft_bl", 0.28, 0.72, 0.72, 0.72, 0.055),
        ("iris_soft_br", 0.72, 0.72, 0.72, 0.72, 0.055),
        ("iris_soft_wide", 0.5, 0.5, 1.15, 0.85, 0.08),
        ("iris_soft_tall", 0.5, 0.5, 0.85, 1.15, 0.08),
        ("iris_soft_low", 0.5, 0.68, 0.85, 0.9, 0.07),
    )
    for name, cx, cy, rx, ry, soft in variants:
        dist = f"hypot((X-{_num(cx)}*W)/{_num(rx)},(Y-{_num(cy)}*H)/{_num(ry)})"
        _add_custom(
            name,
            "iris",
            f"soft-edged elliptical iris from ({cx:.2f}, {cy:.2f})",
            _mix(f"1-{_soft(f'((1-P)*1.6-({dist}))', soft)}"),
        )


def _add_diamond() -> None:
    variants = (
        ("diamond_center", 0.5, 0.5, 0.06),
        ("diamond_tl", 0.3, 0.3, 0.06),
        ("diamond_tr", 0.7, 0.3, 0.06),
        ("diamond_bl", 0.3, 0.7, 0.06),
        ("diamond_br", 0.7, 0.7, 0.06),
        ("diamond_wide", 0.5, 0.5, 0.14),
    )
    for name, cx, cy, soft in variants:
        dist = (
            f"abs(X-{_num(cx)}*W)/(W/2)"
            f"+abs(Y-{_num(cy)}*H)/(H/2)"
        )
        # Worst corner is at most 2; the off-centre variants need a
        # little more travel so every edge is B at the end.
        extra = 0.35 + abs(cx - 0.5) * 2 + abs(cy - 0.5) * 2
        _add_custom(
            name,
            "diamond",
            f"soft diamond reveal centred at ({cx:.2f}, {cy:.2f})",
            _mix(f"1-{_soft(f'((1-P)*{_num(2 + extra)}-({dist}))', soft)}"),
        )


def _add_checker() -> None:
    variants = (
        ("checker_32", 32, 0.10, False),
        ("checker_48", 48, 0.10, False),
        ("checker_64", 64, 0.10, False),
        ("checker_96", 96, 0.12, False),
        ("checker_128", 128, 0.12, False),
        ("checker_64_inverse", 64, 0.10, True),
    )
    for name, cell, soft, inverse in variants:
        phase = f"mod(floor(X/{cell})+floor(Y/{cell}),2)"
        if inverse:
            phase = f"(1-{phase})"
        _add_custom(
            name,
            "checker",
            f"checkerboard dissolve with {cell}px cells"
            + (" (inverted order)" if inverse else ""),
            _mix(
                f"1-{_soft(f'((1-P)-({0.28}+0.56*({phase})))', 0.04)}"
            ),
        )


def _add_blinds() -> None:
    for axis, label in (("X", "vertical"), ("Y", "horizontal")):
        for count in (6, 10, 14, 20, 28):
            period = f"W/{count}" if axis == "X" else f"H/{count}"
            phase = f"mod(floor({axis}/({period})),2)"
            expr = (
                f"1-{_soft(f'((1-P)-({0.28}+0.56*({phase})))', 0.04)}"
            )
            _add_custom(
                f"blinds_{label}_{count}",
                "blinds",
                f"{label} venetian blinds, {count} slats",
                _mix(expr),
            )


def _add_zigzag() -> None:
    for axis, label in (("X", "h"), ("Y", "v")):
        for cycles in (3, 6, 9):
            if axis == "X":
                tri = f"abs(mod(Y/H*{cycles},1)*2-1)"
                edge = f"(W+2*W*0.08)*P-W*0.08+W*0.08*({tri})"
                dist = f"(X-({edge}))/W"
            else:
                tri = f"abs(mod(X/W*{cycles},1)*2-1)"
                edge = f"(H+2*H*0.08)*P-H*0.08+H*0.08*({tri})"
                dist = f"(Y-({edge}))/H"
            _add_custom(
                f"zigzag_{label}_{cycles}",
                "zigzag",
                f"{cycles}-tooth zig-zag wipe ({label})",
                _mix(f"1-{_soft(dist, 0.05)}"),
            )


def _add_wave() -> None:
    for axis, label in (("X", "h"), ("Y", "v")):
        for cycles in (2, 4, 6):
            for phase in (0.0, 1.570796):
                if axis == "X":
                    edge = (
                        f"(W+2*W*0.10)*P-W*0.10"
                        f"+W*0.10*sin(2*PI*Y/H*{cycles}+{_num(phase)})"
                    )
                    dist = f"(X-({edge}))/W"
                else:
                    edge = (
                        f"(H+2*H*0.10)*P-H*0.10"
                        f"+H*0.10*sin(2*PI*X/W*{cycles}+{_num(phase)})"
                    )
                    dist = f"(Y-({edge}))/H"
                _add_custom(
                    f"wave_{label}_{cycles}_{'a' if phase == 0 else 'b'}",
                    "wave",
                    f"{cycles}-cycle sine wipe ({label}, phase "
                    f"{'0' if phase == 0 else 'pi/2'})",
                    _mix(f"1-{_soft(dist, 0.06)}"),
                )


def _spiral_mask(turns: int, direction: str) -> str:
    """Archimedean spiral front that is fully open at ``P = 1``.

    The angle is normalised to one turn and the radius to the half
    diagonal, so the reveal front is ``turns`` twists long and the
    travel has to cover both the radius and the twist.
    """
    theta = "atan2(Y-H/2,X-W/2)+PI"
    if direction == "ccw":
        theta = f"(2*PI-({theta}))"
    theta_n = f"(({theta})/(2*PI))"
    rad = "hypot(X-W/2,Y-H/2)/(hypot(W,H)/2)"
    travel = 1.0 + turns + 0.3
    value = f"(P*{_num(travel)}-(({rad})+{turns}*({theta_n})))"
    return f"1-{_soft(value, 0.05)}"


def _add_spiral() -> None:
    for turns in (1, 2, 3):
        for direction in ("cw", "ccw"):
            _add_custom(
                f"spiral_{direction}_{turns}",
                "spiral",
                f"{direction.upper()} spiral with {turns} turn(s)",
                _mix(_spiral_mask(turns, direction)),
            )


def _add_clock() -> None:
    centres = (
        ("center", 0.5, 0.5),
        ("left", 0.3, 0.5),
        ("right", 0.7, 0.5),
        ("top", 0.5, 0.3),
        ("bottom", 0.5, 0.7),
    )
    for label, cx, cy in centres:
        for direction in ("cw", "ccw"):
            ang = f"atan2(Y-{_num(cy)}*H,X-{_num(cx)}*W)"
            if direction == "cw":
                ang = f"mod(({ang})+PI,2*PI)"
            else:
                ang = f"mod(2*PI-(({ang})+PI),2*PI)"
            _add_custom(
                f"clock_{label}_{direction}",
                "clock",
                f"clock wipe from the {label} ({direction})",
                _mix(f"1-{_soft(f'((1-P)*(2*PI+0.35)-({ang}))', 0.35)}"),
            )


def _add_blocks() -> None:
    for cell in (32, 64, 96):
        for seed in (11.0, 37.0):
            bx = f"floor(X/{cell})"
            by = f"floor(Y/{cell})"
            rnd = (
                f"(0.05+0.80*mod(abs(sin({bx}*12.9898+{by}*78.233+"
                f"{_num(seed)})*43758.5453),1))"
            )
            _add_custom(
                f"blocks_{cell}_{int(seed)}",
                "blocks",
                f"random {cell}px block dissolve (seed {int(seed)})",
                _mix(f"1-{_soft(f'((1-P)-({rnd}))', 0.03)}"),
            )


def _add_barn() -> None:
    for axis, label in (("X", "horizontal"), ("Y", "vertical")):
        for margin, soft in ((0.22, 0.08), (0.42, 0.16)):
            if axis == "X":
                dist = "abs(X-W/2)/(W/2)"
            else:
                dist = "abs(Y-H/2)/(H/2)"
            expr = (
                f"1-{_soft(f'((1-P)*{_num(1 + margin)}-(1-({dist})))', soft)}"
            )
            suffix = "soft" if margin == 0.22 else "wide"
            _add_custom(
                f"barn_{label}_{suffix}",
                "barn",
                f"soft barn doors, {label} ({suffix})",
                _mix(expr),
            )


def _add_gradient() -> None:
    for angle in (0, 15, 30, 45, 60, 75, 90, 105, 120, 135):
        radians = angle * math.pi / 180.0
        cos_a = _num(math.cos(radians))
        sin_a = _num(math.sin(radians))
        proj = (
            f"(X*({cos_a})+Y*({sin_a}))"
            f"/(W*abs({cos_a})+H*abs({sin_a}))"
        )
        soft = 0.06
        edge = f"(1+2*{_num(soft)})*(1-P)-2*{_num(soft)}"
        _add_custom(
            f"gradient_{angle:03d}",
            "gradient",
            f"soft linear wipe at {angle} degrees",
            _mix(f"1-{_soft(f'(({proj})-({edge}))', soft)}"),
        )


def _add_grid() -> None:
    for rows, cols in ((3, 3), (4, 4), (5, 5), (6, 4), (8, 6), (10, 8)):
        idx = (
            f"(floor(Y/(H/{rows}))*{cols}+floor(X/(W/{cols})))"
        )
        total = rows * cols
        rank = f"(({idx})+0.5)/{total}"
        _add_custom(
            f"grid_{rows}x{cols}",
            "grid",
            f"row-major grid reveal, {rows}x{cols} cells",
            _mix(f"1-{_soft(f'((1-P)-({rank}))', 0.04)}"),
        )


def _add_mosaic() -> None:
    for rows, cols, stride in (
        (4, 4, 5),
        (5, 5, 7),
        (6, 4, 11),
        (4, 6, 13),
        (8, 6, 17),
        (6, 8, 19),
    ):
        idx = f"(floor(Y/(H/{rows}))*{cols}+floor(X/(W/{cols})))"
        total = rows * cols
        rank = f"(mod(({idx})*{stride}+{stride}, {total})+0.5)/{total}"
        _add_custom(
            f"mosaic_{rows}x{cols}_{stride}",
            "mosaic",
            f"shuffled mosaic reveal, {rows}x{cols} cells (stride {stride})",
            _mix(f"1-{_soft(f'((1-P)-({rank}))', 0.04)}"),
        )


def _add_rings() -> None:
    for freq, soft in ((3, 0.05), (5, 0.05), (7, 0.07), (9, 0.07)):
        rad = "hypot(X-W/2,Y-H/2)/(hypot(W,H)/2)"
        ripple = f"({rad}+0.035*sin(({rad})*{freq}*2*PI))"
        _add_custom(
            f"ripple_{freq}",
            "ripple",
            f"concentric ripple reveal, {freq} wave(s)",
            _mix(f"1-{_soft(f'((1-P)*1.35-({ripple}))', soft)}"),
        )


def _add_star() -> None:
    for points in (5, 6, 8, 10):
        theta = "atan2(Y-H/2,X-W/2)+PI"
        rad = "hypot(X-W/2,Y-H/2)/(hypot(W,H)/2)"
        shape = f"({rad})/(0.55+0.45*abs(cos(({theta})*{points}/2)))"
        _add_custom(
            f"star_{points}",
            "star",
            f"{points}-point star iris",
            _mix(f"1-{_soft(f'(P*2.0-({shape}))', 0.07)}"),
        )


def _add_multi_iris() -> None:
    grids = (
        ("2x2", 2, 2, 0.0),
        ("3x3", 3, 3, 0.0),
        ("2x3", 2, 3, 0.0),
        ("3x2", 3, 2, 0.0),
        ("2x2_offset", 2, 2, 0.12),
        ("3x3_offset", 3, 3, 0.10),
    )
    for label, cols, rows, shift in grids:
        parts = []
        for row in range(rows):
            for col in range(cols):
                cx = (col + 0.5 + shift) / cols
                cy = (row + 0.5 + shift) / rows
                parts.append(f"hypot(X-{_num(cx)}*W,Y-{_num(cy)}*H)")
        dist = _min(*parts)
        _add_custom(
            f"multi_iris_{label}",
            "multi-iris",
            f"multi-point iris, {rows}x{cols} centres"
            + (" (offset)" if shift else ""),
            _mix(f"1-{_soft(f'((1-P)*hypot(W,H)*0.7-({dist}))', 0.06)}"),
        )


def _add_shutter() -> None:
    for axis, label in (("X", "vertical"), ("Y", "horizontal")):
        for count in (4, 8):
            period = f"W/{count}" if axis == "X" else f"H/{count}"
            frac = f"(mod({axis},{period})/({period}))"
            phase = f"mod(floor({axis}/({period})),2)"
            front = f"if(eq({phase},0),{frac},1-({frac}))"
            _add_custom(
                f"shutter_{label}_{count}",
                "shutter",
                f"{label} shutter, {count} bars sliding in from both sides",
                _mix(f"1-{_soft(f'(P*1.25-({front}))', 0.04)}"),
            )


def _corner_mask(dist: str) -> str:
    """Radial front from a corner, normalised by the frame diagonal."""
    dist_n = f"(({dist})/hypot(W,H))"
    return f"1-{_soft(f'(P*1.08-({dist_n}))', 0.03)}"


def _add_corner() -> None:
    corners = (
        ("tl", "hypot(X,Y)"),
        ("tr", "hypot(W-X,Y)"),
        ("bl", "hypot(X,H-Y)"),
        ("br", "hypot(W-X,H-Y)"),
    )
    for label, dist in corners:
        _add_custom(
            f"corner_{label}",
            "corner",
            f"radial reveal from the {label} corner",
            _mix(_corner_mask(dist)),
        )
    _add_custom(
        "corner_all",
        "corner",
        "radial reveal from all four corners at once",
        _mix(
            _corner_mask(
                _min(
                    "hypot(X,Y)",
                    "hypot(W-X,Y)",
                    "hypot(X,H-Y)",
                    "hypot(W-X,H-Y)",
                )
            )
        ),
    )
    _add_custom(
        "corner_diagonal",
        "corner",
        "two-corner diagonal reveal (TL and BR)",
        _mix(_corner_mask(_min("hypot(X,Y)", "hypot(W-X,H-Y)"))),
    )


def _pinwheel_mask(spokes: int) -> str:
    """Rotating N-spoke wedge iris."""
    theta = "atan2(Y-H/2,X-W/2)+PI"
    rad = "hypot(X-W/2,Y-H/2)/(hypot(W,H)/2)"
    wedge = f"abs(cos(({theta})*{spokes}/2+(1-P)*PI/2))"
    return f"1-{_soft(f'(P*1.85-({rad})-0.55*({wedge}))', 0.05)}"


def _add_pinwheel() -> None:
    for spokes in (2, 3, 4, 5):
        _add_custom(
            f"pinwheel_{spokes}",
            "pinwheel",
            f"{spokes}-spoke pinwheel iris, rotating as it opens",
            _mix(_pinwheel_mask(spokes)),
        )


def _add_polygon() -> None:
    shapes = (
        ("square", "max(abs(X-W/2)/(W/2),abs(Y-H/2)/(H/2))"),
        (
            "hexagon",
            "max(abs(X-W/2)/(W/2),max(0.5*abs(X-W/2)/(W/2)+"
            "0.866*abs(Y-H/2)/(H/2),0.5*abs(X-W/2)/(W/2)-"
            "0.866*abs(Y-H/2)/(H/2)))",
        ),
        (
            "triangle",
            "max(abs(Y-H/2)/(H/2),abs(X-W/2)/(W/2)*0.9+"
            "(Y-H/2)/(H/2)*0.45)",
        ),
    )
    for label, dist in shapes:
        for variant, extra in (("soft", 0.06), ("wide", 0.14)):
            _add_custom(
                f"polygon_{label}_{variant}",
                "polygon",
                f"{label} iris ({variant} edge)",
                _mix(f"1-{_soft(f'(P*1.55-({dist}))', extra)}"),
            )


def _add_diagonal_blinds() -> None:
    for count in (6, 10, 14, 18, 24, 30):
        phase = f"mod(floor((X+Y)/((W+H)/{count})),2)"
        _add_custom(
            f"diag_blinds_{count}",
            "diagonal-blinds",
            f"diagonal venetian blinds, {count} slats",
            _mix(f"1-{_soft(f'((1-P)-({0.28}+0.56*({phase})))', 0.04)}"),
        )


def _add_frame() -> None:
    d = "max(abs(X-W/2)/(W/2),abs(Y-H/2)/(H/2))"
    _add_custom(
        "window_open",
        "frame",
        "soft window opens from the centre",
        _mix(f"1-{_soft(f'((1-P)*1.35-({d}))', 0.08)}"),
    )
    _add_custom(
        "frame_close",
        "frame",
        "soft frame closes in from the borders",
        _mix(f"1-{_soft(f'((1-P)*1.35-(1-({d})))', 0.08)}"),
    )
    _add_custom(
        "window_wide",
        "frame",
        "wide soft window from the centre",
        _mix(f"1-{_soft(f'((1-P)*1.35-({d}))', 0.16)}"),
    )
    _add_custom(
        "frame_wide",
        "frame",
        "wide soft frame from the borders",
        _mix(f"1-{_soft(f'((1-P)*1.35-(1-({d})))', 0.16)}"),
    )


_add_iris()
_add_diamond()
_add_checker()
_add_blinds()
_add_zigzag()
_add_wave()
_add_spiral()
_add_clock()
_add_blocks()
_add_barn()
_add_gradient()
_add_grid()
_add_mosaic()
_add_rings()
_add_star()
_add_multi_iris()
_add_shutter()
_add_corner()
_add_pinwheel()
_add_polygon()
_add_diagonal_blinds()
_add_frame()


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Ordinary-filter graphs: motion, glitch and light
# ---------------------------------------------------------------------------


def _timeline(
    *,
    duration: float,
    offset: float,
    a_filters: str = "",
    b_filters: str = "",
    alpha: float | None = None,
    x: str = "0",
    y: str = "0",
    post: str = "",
) -> str:
    """Two clips on one timeline, with B composited over A.

    A is extended by ``offset`` with its last frame; B is preceded by
    ``offset`` seconds of its first frame. B therefore starts its own
    content exactly at the transition, and the result lasts
    ``2 * offset + duration`` seconds.
    """
    a_chain = f"tpad=stop_mode=clone:stop_duration={_num(offset)}"
    if a_filters:
        a_chain += "," + a_filters

    b_chain = f"tpad=start_mode=clone:start_duration={_num(offset)}"
    if b_filters:
        b_chain += "," + b_filters
    if alpha is not None:
        b_chain += (
            f",format=rgba,fade=t=in:st={_num(offset)}"
            f":d={_num(alpha)}:alpha=1"
        )

    graph = (
        f"[a]{a_chain}[aext];[b]{b_chain}[bpad];"
        f"[aext][bpad]overlay=x='{x}':y='{y}':eof_action=pass"
    )
    if post:
        graph += "," + post
    return graph + "[out]"


def _window(offset: float, duration: float) -> str:
    return f"between(t,{_num(offset)},{_num(offset + duration)})"


def _whip_builder(axis: str, start: float, blur: str) -> Builder:
    def builder(duration, offset, width, height, fps) -> str:
        span = width if axis == "x" else height
        travel = start * span
        pos = (
            f"if(lt(t,{_num(offset)}),{_num(travel)},"
            f"{_num(travel)}*(1-clip((t-{_num(offset)})"
            f"/{_num(duration)},0,1)))"
        )
        active = f"{blur}:enable='{_window(offset, duration)}'"
        if axis == "x":
            return _timeline(
                duration=duration,
                offset=offset,
                a_filters=active,
                b_filters=active,
                x=pos,
                y="0",
            )
        return _timeline(
            duration=duration,
            offset=offset,
            a_filters=active,
            b_filters=active,
            x="0",
            y=pos,
        )

    return builder


def _add_motion() -> None:
    for name, desc, axis, start in (
        ("whip_left", "whip pan left, B enters from the right", "x", 1.0),
        ("whip_right", "whip pan right, B enters from the left", "x", -1.0),
        ("whip_up", "whip pan up, B enters from the bottom", "y", 1.0),
        ("whip_down", "whip pan down, B enters from the top", "y", -1.0),
    ):
        blur = (
            "avgblur=sizeX=12:sizeY=1"
            if axis == "x"
            else "avgblur=sizeX=1:sizeY=12"
        )
        _register(name, "motion", desc, _whip_builder(axis, start, blur))

    for name, desc, z0, z1 in (
        ("zoom_punch_in", "zoom punch into the incoming clip", 1.0, 1.9),
        ("zoom_punch_out", "zoom punch out of the incoming clip", 1.9, 1.0),
    ):
        def builder(
            duration, offset, width, height, fps, z0=z0, z1=z1
        ) -> str:
            z = (
                f"{_num(z0)}+{_num(z1 - z0)}*"
                f"clip((time-{_num(offset)})/{_num(duration)},0,1)"
            )
            filt = (
                f"zoompan=z='{z}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
                f":d=1:s={width}x{height}:fps={_num(fps)}"
            )
            return _timeline(
                duration=duration,
                offset=offset,
                b_filters=filt,
                alpha=duration,
            )

        _register(name, "motion", desc, builder)

    for name, desc, sign in (
        ("spin_zoom_cw", "clockwise spin and zoom", 1),
        ("spin_zoom_ccw", "counter-clockwise spin and zoom", -1),
    ):
        def builder(duration, offset, width, height, fps, sign=sign) -> str:
            zoom_progress = (
                f"clip((time-{_num(offset)})/{_num(duration)},0,1)"
            )
            spin_progress = (
                f"clip((t-{_num(offset)})/{_num(duration)},0,1)"
            )
            z = f"1+0.5*{zoom_progress}"
            filt = (
                f"zoompan=z='{z}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
                f":d=1:s={width}x{height}:fps={_num(fps)},"
                "format=rgba,"
                f"rotate=angle='{sign}*2*PI*{spin_progress}'"
                f":ow={width}:oh={height}:c=black@0"
            )
            return _timeline(
                duration=duration,
                offset=offset,
                b_filters=filt,
                alpha=duration,
            )

        _register(name, "motion", desc, builder)

    for name, desc, amp, freq, decay, alpha in (
        ("shake_cut_hard", "hard cut with a sharp camera shake", 34, 70, 9, 0.04),
        ("shake_cut_soft", "cut with a soft decaying camera shake", 18, 38, 5, 0.08),
    ):
        def builder(
            duration,
            offset,
            width,
            height,
            fps,
            amp=amp,
            freq=freq,
            decay=decay,
            alpha=alpha,
        ) -> str:
            t0 = f"(t-{_num(offset)})"
            gate = f"if(lt(t,{_num(offset)}),0,"
            x = (
                f"{gate}{_num(amp)}*sin({_num(freq)}*{t0})"
                f"*exp(-{_num(decay)}*{t0}))"
            )
            y = (
                f"{gate}{_num(amp)}*cos({_num(freq)}*{t0})"
                f"*exp(-{_num(decay)}*{t0}))"
            )
            return _timeline(
                duration=duration,
                offset=offset,
                x=x,
                y=y,
                alpha=alpha,
            )

        _register(name, "motion", desc, builder)


def _slice_overlay_builder(axis: str, slices: int, shift: int) -> Builder:
    """B enters as offset slices (a glitch cut).

    ``axis="y"`` cuts horizontal bands that slide sideways;
    ``axis="x"`` cuts vertical bands that slide up or down. Neighbouring
    bands slide in opposite directions and settle at zero.
    """

    def builder(duration, offset, width, height, fps) -> str:
        size = height if axis == "y" else width
        step = max(1, size // slices)
        parts = [
            f"[a]tpad=stop_mode=clone:stop_duration={_num(offset)}[aext]",
            f"[b]tpad=start_mode=clone:start_duration={_num(offset)}"
            f",split={slices}" + "".join(f"[b{i}]" for i in range(slices)),
        ]
        current = "[aext]"
        for index in range(slices):
            start = index * step
            extent = step if index < slices - 1 else size - start
            signed = shift if index % 2 == 0 else -shift
            pos = (
                f"if(lt(t,{_num(offset)}),{signed},"
                f"{signed}*(1-clip((t-{_num(offset)})"
                f"/{_num(duration)},0,1)))"
            )
            if axis == "y":
                crop = f"crop={width}:{extent}:0:{start}"
                x, y = pos, str(start)
            else:
                crop = f"crop={extent}:{height}:{start}:0"
                x, y = str(start), pos
            parts.append(
                f"[b{index}]{crop},format=rgba,"
                f"fade=t=in:st={_num(offset)}"
                f":d={_num(min(0.05, duration))}:alpha=1[g{index}]"
            )
            parts.append(
                f"{current}[g{index}]overlay=x='{x}':y='{y}'"
                f":eof_action=pass[o{index}]"
            )
            current = f"[o{index}]"
        return ";".join(parts) + f";{current}null[out]"

    return builder


def _add_glitch() -> None:
    for name, desc, filt in (
        (
            "rgb_split_lr",
            "red/blue channel split during the cut",
            "format=gbrp,rgbashift=rh=28:bh=-28",
        ),
        (
            "rgb_split_tb",
            "red/blue vertical channel split during the cut",
            "format=gbrp,rgbashift=rv=22:bv=-22",
        ),
        (
            "rgb_split_soft",
            "gentle red/blue channel split during the cut",
            "format=gbrp,rgbashift=rh=12:bh=-12",
        ),
    ):
        def builder(duration, offset, width, height, fps, filt=filt) -> str:
            post = (
                f"{filt}:enable='{_window(offset, duration)}',"
                "format=yuv420p"
            )
            return _timeline(
                duration=duration,
                offset=offset,
                alpha=0.04,
                post=post,
            )

        _register(name, "glitch", desc, builder)

    _register(
        "slice_offset_h",
        "glitch",
        "horizontal slices offset in opposite directions",
        _slice_overlay_builder("y", 4, 16),
    )
    _register(
        "slice_offset_v",
        "glitch",
        "vertical slices offset in opposite directions",
        _slice_overlay_builder("x", 4, 16),
    )
    _register(
        "slice_offset_fine",
        "glitch",
        "eight horizontal slices offset in opposite directions",
        _slice_overlay_builder("y", 8, 10),
    )

    for name, desc, strength in (
        ("noise_burst_soft", "short noise burst over the cut", 38),
        ("noise_burst_hard", "heavy noise burst over the cut", 78),
    ):
        def builder(
            duration, offset, width, height, fps, strength=strength
        ) -> str:
            post = (
                f"noise=alls={strength}:allf=t+u"
                f":enable='{_window(offset, duration)}'"
            )
            return _timeline(
                duration=duration,
                offset=offset,
                alpha=0.04,
                post=post,
            )

        _register(name, "glitch", desc, builder)

    def scanline_builder(duration, offset, width, height, fps) -> str:
        active = _window(offset, duration)
        post = (
            f"format=gbrp,rgbashift=rh=8:bh=-8:enable='{active}',"
            f"drawgrid=w=iw:h=4:t=1:c=black@0.35:enable='{active}',"
            "format=yuv420p"
        )
        return _timeline(
            duration=duration, offset=offset, alpha=0.04, post=post
        )

    _register(
        "scanline_glitch",
        "glitch",
        "scanlines and channel offset during the cut",
        scanline_builder,
    )

    def block_builder(duration, offset, width, height, fps) -> str:
        post = (
            f"pixelize=w=18:h=18:enable='{_window(offset, duration)}'"
        )
        return _timeline(
            duration=duration, offset=offset, alpha=0.04, post=post
        )

    _register(
        "block_glitch",
        "glitch",
        "pixel-block burst at the cut",
        block_builder,
    )


def _add_light() -> None:
    for name, desc, colour in (
        ("flash_white", "full-frame white flash at the cut", "white"),
        ("flash_amber", "warm amber flash at the cut", "0xFFB000"),
        ("flash_cyan", "cool cyan flash at the cut", "0x00E5FF"),
        ("flash_gray", "neutral gray flash at the cut", "0x808080"),
        ("flash_red", "red flash at the cut", "0xFF0000"),
        ("flash_green", "green flash at the cut", "0x00FF00"),
    ):
        def builder(
            duration, offset, width, height, fps, colour=colour
        ) -> str:
            total = 2 * offset + duration
            attack = max(0.04, duration * 0.35)
            release = max(0.04, duration - attack)
            flash = (
                f"color=c={colour}:s={width}x{height}:r={_num(fps)}"
                f":d={_num(total)},format=rgba,"
                f"fade=t=in:st={_num(offset)}:d={_num(attack)}:alpha=1,"
                f"fade=t=out:st={_num(offset + attack)}"
                f":d={_num(release)}:alpha=1"
            )
            return (
                f"[a][b]xfade=transition=fade:duration={_num(duration)}"
                f":offset={_num(offset)}[xf];{flash}[fl];"
                "[xf][fl]overlay=0:0:eof_action=pass[out]"
            )

        _register(name, "light", desc, builder)

    for name, desc, forward in (
        ("light_leak_left", "warm light leak sweeping left to right", True),
        ("light_leak_right", "warm light leak sweeping right to left", False),
        (
            "light_leak_soft",
            "soft warm light leak sweeping left to right",
            True,
        ),
    ):
        def builder(
            duration, offset, width, height, fps, forward=forward,
            name=name,
        ) -> str:
            total = 2 * offset + duration
            if forward:
                centre = f"(W*T/{_num(total)})"
            else:
                centre = f"(W*(1-T/{_num(total)}))"
            halo = "0.34" if name == "light_leak_soft" else "0.22"
            fade = max(0.04, duration * 0.18)
            envelope = (
                f"clip((T-{_num(offset)})/{_num(fade)},0,1)*"
                f"clip(({_num(offset + duration)}-T)/{_num(fade)},0,1)"
            )
            band = (
                f"clip(1-abs(X-({centre}))/(W*{halo}),0,1)"
                f"*{envelope}"
            )
            leak = (
                f"color=c=black:s={width}x{height}:r={_num(fps)}"
                f":d={_num(total)},format=rgba,"
                f"geq=r='240*{band}':g='140*{band}':b='40*{band}':a=255"
            )
            return (
                f"[a][b]xfade=transition=fade:duration={_num(duration)}"
                f":offset={_num(offset)},format=rgba[xf];"
                f"{leak}[leak];"
                "[xf][leak]blend=all_mode=screen:shortest=1,"
                "format=yuv420p[out]"
            )

        _register(name, "light", desc, builder)

    for name, desc, sigma in (
        ("blur_dissolve_soft", "cross-fade through a soft blur", 8),
        ("blur_dissolve_strong", "cross-fade through a heavy blur", 22),
    ):
        def builder(
            duration, offset, width, height, fps, sigma=sigma
        ) -> str:
            return (
                f"[a][b]xfade=transition=fade:duration={_num(duration)}"
                f":offset={_num(offset)},"
                f"gblur=sigma={_num(sigma)}"
                f":enable='{_window(offset, duration)}'[out]"
            )

        _register(name, "light", desc, builder)

    for name, desc, gamma in (
        ("luma_fade_soft", "luma-weighted fade with a soft curve", 0.55),
        ("luma_fade_hard", "luma-weighted fade with a hard curve", 2.2),
    ):
        expr = f"A*pow(P,{_num(gamma)})+B*(1-pow(P,{_num(gamma)}))"
        _register(name, "light", desc, _xfade_builder(expr))


def _blend_mode_builder(formula: str) -> Builder:
    """A -> blend(A, B) -> B, with the mode expression in RGB.

    ``formula`` is an FFmpeg expression over ``A`` and ``B``; it is
    evaluated at the midpoint of the join, while the first and last
    quarters cross-fade into and out of the blended frame. That keeps
    the outgoing and incoming frames intact at both ends.
    """

    def builder(duration, offset, width, height, fps) -> str:
        start = _num(offset)
        end = _num(offset + duration)
        progress = _num(duration)
        p = f"clip((T-{start})/{progress},0,1)"
        mode = formula
        blended = (
            f"if(lt({p},0.5),"
            f"A*(1-2*{p})+({mode})*(2*{p}),"
            f"({mode})*(2-2*{p})+B*(2*{p}-1))"
        )
        expr = f"if(lt(T,{start}),A,if(gte(T,{end}),B,{blended}))"
        return (
            f"[a]tpad=stop_mode=clone:stop_duration={start},"
            f"format=gbrp[aext];"
            f"[b]tpad=start_mode=clone:start_duration={start},"
            f"format=gbrp[bext];"
            f"[aext][bext]blend=all_expr='{expr}',"
            "format=yuv420p[out]"
        )

    return builder


def _strobe_builder(frequency: float) -> Builder:
    """Alternate the two clips on a fixed cadence through the join."""

    def builder(duration, offset, width, height, fps) -> str:
        start = _num(offset)
        end = _num(offset + duration)
        expr = (
            f"if(lt(T,{start}),A,if(gte(T,{end}),B,"
            f"if(mod(floor(T*{_num(frequency)}),2),B,A)))"
        )
        return (
            f"[a]tpad=stop_mode=clone:stop_duration={start}[aext];"
            f"[b]tpad=start_mode=clone:start_duration={start}[bext];"
            f"[aext][bext]blend=all_expr='{expr}'[out]"
        )

    return builder


def _fade_post(post: str) -> Builder:
    """Cross-fade, then run ``post`` inside the transition window.

    ``post`` may contain ``{window}``; it is replaced with the real
    ``between(t, offset, offset + duration)`` expression.
    """

    def builder(duration, offset, width, height, fps) -> str:
        window = _window(offset, duration)
        return (
            f"[a][b]xfade=transition=fade:duration={_num(duration)}"
            f":offset={_num(offset)},{post.format(window=window)}[out]"
        )

    return builder


def _mirror_builder(filters: str) -> Builder:
    """Show a full-frame mirrored copy during the join."""

    def builder(duration, offset, width, height, fps) -> str:
        window = _window(offset, duration)
        return (
            f"[a][b]xfade=transition=fade:duration={_num(duration)}"
            f":offset={_num(offset)},split=2[keep][mir];"
            f"[mir]{filters}[flip];"
            f"[keep][flip]overlay=0:0:enable='{window}'[out]"
        )

    return builder


def _add_effects() -> None:
    modes = (
        ("blend_screen", "screen blend through the cut", "255-(255-A)*(255-B)/255"),
        ("blend_multiply", "multiply blend through the cut", "A*B/255"),
        ("blend_difference", "absolute difference blend", "abs(A-B)"),
        ("blend_darken", "keep the darker pixel of both clips", "min(A,B)"),
        ("blend_lighten", "keep the lighter pixel of both clips", "max(A,B)"),
        ("blend_add", "additive blend through the cut", "min(255,A+B)"),
        (
            "blend_exclusion",
            "exclusion blend through the cut",
            "A+B-2*A*B/255",
        ),
        (
            "blend_hardlight",
            "hard-light blend through the cut",
            "if(lt(B,128),2*A*B/255,255-2*(255-A)*(255-B)/255)",
        ),
        (
            "blend_overlay",
            "overlay blend through the cut",
            "if(lt(A,128),2*A*B/255,255-2*(255-A)*(255-B)/255)",
        ),
        (
            "blend_softlight",
            "soft-light blend through the cut",
            "sqrt(A*B)",
        ),
        (
            "blend_linearlight",
            "linear-light blend through the cut",
            "clip(A+2*B-255,0,255)",
        ),
        (
            "blend_vividlight",
            "vivid-light blend through the cut",
            "if(lt(B,128),255-min(255,(255-A)*255/max(1,2*B)),"
            "min(255,A*255/max(1,2*(255-B))))",
        ),
        (
            "blend_subtract",
            "subtract the incoming clip from the outgoing one",
            "max(0,A-B)",
        ),
        (
            "blend_grainmerge",
            "grain-merge blend through the cut",
            "clip(A+B-128,0,255)",
        ),
        (
            "blend_divide",
            "divide blend through the cut",
            "min(255,A*255/max(1,B))",
        ),
        (
            "blend_colorburn",
            "colour-burn blend through the cut",
            "255-min(255,(255-A)*255/max(1,B))",
        ),
        (
            "blend_colordodge",
            "colour-dodge blend through the cut",
            "min(255,A*255/max(1,255-B))",
        ),
        (
            "blend_pinlight",
            "pin-light blend through the cut",
            "if(lt(B,128),min(A,2*B),max(A,2*B-255))",
        ),
        (
            "blend_hardmix",
            "hard-mix blend through the cut",
            "if(gte(A+B,255),255,0)",
        ),
        (
            "blend_negation",
            "negated sum blend through the cut",
            "255-abs(255-A-B)",
        ),
        (
            "blend_linearburn",
            "linear-burn blend through the cut",
            "clip(A+B-255,0,255)",
        ),
        (
            "blend_grainextract",
            "grain-extract blend through the cut",
            "clip(A-B+128,0,255)",
        ),
        (
            "blend_average",
            "hold the average of both clips through the cut",
            "(A+B)/2",
        ),
        (
            "blend_reflect",
            "reflected blend through the cut",
            "min(255,A*A/max(1,255-B))",
        ),
        (
            "blend_phoenix",
            "inverted difference blend through the cut",
            "255-abs(A-B)",
        ),
        (
            "blend_glow",
            "glow blend through the cut",
            "min(255,A+B/2)",
        ),
    )
    for name, desc, formula in modes:
        _register(name, "blend", desc, _blend_mode_builder(formula))

    for frequency in (6, 9, 13, 17, 23, 29, 37, 47):
        _register(
            name := f"strobe_{frequency}",
            "strobe",
            f"{frequency} Hz strobe between both clips",
            _strobe_builder(frequency),
        )

    for size in (6, 10, 14, 24, 36, 48, 72, 108):
        post = f"pixelize=w={size}:h={size}:enable='{{window}}'"
        _register(
            f"pixel_{size}",
            "pixel",
            f"{size}px pixel-block dissolve",
            _fade_post(post),
        )

    for name, desc, filters in (
        ("mirror_h", "horizontal mirror at the midpoint", "hflip"),
        ("mirror_v", "vertical mirror at the midpoint", "vflip"),
        ("mirror_hv", "both-axis mirror at the midpoint", "hflip,vflip"),
    ):
        _register(name, "mirror", desc, _mirror_builder(filters))

    for name, desc, filt in (
        (
            "mirror_cw",
            "quarter-turn clockwise at the midpoint",
            "rotate=PI/2:ow=iw:oh=ih",
        ),
        (
            "mirror_ccw",
            "quarter-turn counter-clockwise at the midpoint",
            "rotate=-PI/2:ow=iw:oh=ih",
        ),
        (
            "mirror_tilt",
            "diagonal tilt at the midpoint",
            "rotate=PI/4:ow=iw:oh=ih",
        ),
    ):
        post = f"{filt}:enable='{{window}}'"
        _register(name, "mirror", desc, _fade_post(post))

    for angle in (45, 90, 135, 180, 270, 330):
        post = f"hue=h={angle}:enable='{{window}}'"
        _register(
            f"hue_{angle}",
            "hue",
            f"{angle} degree hue swing through the cut",
            _fade_post(post),
        )

    for name, desc, mixer in (
        (
            "channel_swap_rb",
            "red and blue channels swap through the cut",
            "rr=0:rb=1:br=1:bb=0",
        ),
        (
            "channel_swap_rg",
            "red and green channels swap through the cut",
            "rr=0:rg=1:gr=1:gg=0",
        ),
        (
            "channel_swap_gb",
            "green and blue channels swap through the cut",
            "gg=0:gb=1:bg=1:bb=0",
        ),
        (
            "channel_warm",
            "warm channel push through the cut",
            "rr=1.4:gg=0.75:bb=0.6",
        ),
    ):
        post = (
            f"colorchannelmixer={mixer}"
            f":enable='{{window}}'"
        )
        _register(name, "channel", desc, _fade_post(post))

    for name, desc, grade in (
        ("grade_flat", "flatten contrast through the cut", "contrast=0.4"),
        ("grade_soft", "soften contrast through the cut", "contrast=0.7"),
        ("grade_punch", "punch up contrast through the cut", "contrast=1.8"),
        ("grade_hard", "hard contrast through the cut", "contrast=2.5"),
        ("grade_mono", "drain colour through the cut", "saturation=0"),
        ("grade_bright", "lift exposure through the cut", "brightness=0.12"),
        ("grade_dark", "drop exposure through the cut", "brightness=-0.12"),
        ("grade_shadow", "crush shadows through the cut", "gamma=0.6"),
        ("grade_lift", "lift midtones through the cut", "gamma=1.6"),
        ("grade_bloom", "strong midtone lift through the cut", "gamma=2.4"),
        ("grade_gentle", "gently raise contrast through the cut", "contrast=1.3"),
    ):
        post = f"eq={grade}:enable='{{window}}'"
        _register(name, "grade", desc, _fade_post(post))

    for name, desc, angle in (
        ("rotate_15", "15 degree twist at the midpoint", "PI/12"),
        ("rotate_30", "30 degree twist at the midpoint", "PI/6"),
        ("rotate_60", "60 degree twist at the midpoint", "PI/3"),
        ("rotate_75", "75 degree twist at the midpoint", "5*PI/12"),
        ("rotate_105", "105 degree twist at the midpoint", "7*PI/12"),
        ("rotate_120", "120 degree twist at the midpoint", "2*PI/3"),
    ):
        post = f"rotate={angle}:ow=iw:oh=ih:enable='{{window}}'"
        _register(name, "rotate", desc, _fade_post(post))


_add_motion()
_add_glitch()
_add_light()
_add_effects()
