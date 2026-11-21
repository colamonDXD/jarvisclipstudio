"""Versioned, non-destructive clip drafts and deterministic render instructions.

Editor files live beside generated media on the shared temp volume. Originals are
never modified. A file lock and revision check protect concurrent API workers.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

BROLL_MIN_SECONDS = 0.2
BROLL_CONTINUATION_MIN_SECONDS = 0.04
BROLL_FADE_SECONDS = 0.25
OVERLAY_MIN_SECONDS = 0.2
OVERLAY_CONTINUATION_MIN_SECONDS = 0.04
TIME_EPSILON = 1e-9


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Segment(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end - self.start < 0.04 - TIME_EPSILON:
            raise ValueError("Each segment must be at least one frame long")
        return self


class Word(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=120)
    highlight: bool = False

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Caption end must follow its start")
        return self


class CaptionStyle(Model):
    enabled: bool = True
    font: Literal[
        "TikTokSans-Regular",
        "TikTokSans-Bold",
        "Poppins-ExtraBold",
        "THEBOLDFONT",
        "Montserrat-Black",
        "Montserrat-ExtraBold",
    ] = "Poppins-ExtraBold"
    # 1080-wide units; 132 matches the generation renderer's largest caption.
    size: int = Field(default=54, ge=20, le=132)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str = Field(default="#FACC15", pattern=r"^#[0-9a-fA-F]{6}$")
    y: float = Field(default=0.78, ge=0.1, le=0.9)
    background: bool = True
    wordsPerLine: int = Field(default=4, ge=1, le=8)
    # Colour the word being spoken with the accent, as the generated clip does.
    karaoke: bool = False
    uppercase: bool = False
    # Caption template the clip was generated with; supplies the animation, outline,
    # word pill and emphasis styling that the fields above don't expose.
    template: str = Field(default="default", min_length=1, max_length=50)


class Framing(Model):
    aspect: Literal["vertical", "square", "original"] = "vertical"
    fit: Literal["contain", "cover", "auto"] = "cover"
    zoom: float = Field(default=1, ge=1, le=3)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)


class Effects(Model):
    brightness: float = Field(default=100, ge=40, le=180)
    contrast: float = Field(default=100, ge=40, le=180)
    saturation: float = Field(default=100, ge=0, le=220)
    blur: float = Field(default=0, ge=0, le=8)
    hue: float = Field(default=0, ge=-180, le=180)


# One download host per stock provider: the editor only ever fetches a
# rendition from its own provider's CDN, never an arbitrary URL. Pexels
# renditions download from that host; Pixabay/Coverr renditions arrive
# through the shared stock library (their file is already on disk when
# the editor opens the clip), so their ``fileUrl`` is provenance — where
# the file came from — and is never fetched by the Pexels download path.
# Coverr's real file host is storage.coverr.co (its API answers
# mp4_download/poster URLs there); cdn.coverr.co is not a Coverr host.
STOCK_CDN_HOSTS = {
    "pexels": "videos.pexels.com",
    "pixabay": "cdn.pixabay.com",
    "coverr": "storage.coverr.co",
}


class BRollFootage(Model):
    """A stock video rendition.

    ``provider`` is the library the footage came from. Pexels and
    Pixabay ids are ints; Coverr ids arrive as strings.
    """

    provider: Literal["pexels", "pixabay", "coverr"] = "pexels"
    id: int | str
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    duration: float = Field(gt=0, le=3600)
    fileUrl: str = Field(max_length=500)
    pageUrl: str = Field(default="", max_length=500)
    thumbnail: str = Field(default="", max_length=500)
    credit: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _check_id_and_url(self):
        if isinstance(self.id, int):
            if self.id <= 0:
                raise ValueError("Stock footage needs a positive id")
        elif isinstance(self.id, str):
            if not self.id or len(self.id) > 100 or not re.fullmatch(r"[A-Za-z0-9_-]+", self.id):
                raise ValueError("Stock footage needs a short plain-text id")
        else:
            raise ValueError("Stock footage needs an id")
        host = STOCK_CDN_HOSTS[self.provider]
        if not re.fullmatch(rf"https://{re.escape(host)}/[^\s]{{1,500}}", self.fileUrl or ""):
            raise ValueError(f"Stock footage must come from {host}")
        return self

    @property
    def key(self) -> str:
        # Provider-prefixed: a Pixabay id can equal a Pexels id, and the
        # key is the render's lookup into the resolved footage files —
        # two different assets must never share one.
        return f"{self.provider}:{self.id}/{self.width}x{self.height}"


class BRollClip(Model):
    """Stock footage shown over the clip from ``start`` to ``end`` (clean.mp4 time).

    Like caption words, it lives on the source timeline, so cuts, reorders and
    duplicated segments carry it along. ``offset`` is where in the footage it begins.
    """

    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    offset: float = Field(default=0, ge=0, le=3600)
    query: str = Field(default="", max_length=120)
    footage: BRollFootage
    # Smart B-Roll v2 review surface (Slice 6): the planner's concept
    # sentence for this cue ("what the brain asked for"), and the planned
    # search/sense text it falls back to when the cue has no concept
    # (a keyword cue's sense-resolved search string). Both default to ""
    # — a manually added clip carries neither, and the panel then falls
    # back to ``query``.
    concept: str = Field(default="", max_length=500)
    searchQuery: str = Field(default="", max_length=200)
    # "Next candidate" inputs (Slice 6): the concept's full search-phrase
    # list, its wrong-sense list and its media preference — everything
    # the resolver needs to re-resolve this cue faithfully. All default
    # empty: a manually added clip carries none, and the re-resolve then
    # falls back to ``searchQuery`` / ``query`` exactly as the resolver
    # does without them.
    searchQueries: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=3)
    avoid: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=20)
    media: str = Field(default="", max_length=20)
    # Combined cuts remain separate footage ranges, without adding dissolves at the join.
    fadeIn: bool = True
    fadeOut: bool = True
    fadeDuration: float | None = Field(default=None, gt=0, le=BROLL_FADE_SECONDS)

    @model_validator(mode="after")
    def ordered(self):
        minimum = BROLL_MIN_SECONDS if self.fadeIn and self.fadeOut else BROLL_CONTINUATION_MIN_SECONDS
        if self.end - self.start < minimum - TIME_EPSILON:
            raise ValueError(f"B-roll must be at least {minimum} seconds long")
        return self


class OverlayItem(Model):
    """A user-placed image shown over the clip from ``start`` to ``end``.

    Like B-roll it lives on the source (clean.mp4) timeline, so cuts, reorders
    and duplicated segments carry it along. ``asset`` names the uploaded image
    inside the editor directory; ``x``/``y`` are the image centre as fractions
    of the frame and ``scale`` its width as a fraction of the frame width.
    """

    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    asset: str = Field(pattern=r"^overlay-[a-f0-9]{32}\.(png|jpg|webp)$")
    name: str = Field(default="", max_length=120)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)
    scale: float = Field(default=0.4, ge=0.05, le=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.end - self.start < OVERLAY_MIN_SECONDS - TIME_EPSILON:
            raise ValueError(f"An overlay must be at least {OVERLAY_MIN_SECONDS} seconds long")
        return self


LaneName = Literal["video", "broll", "overlays", "captions", "hook", "audio"]


class LaneState(Model):
    """Per-lane editor controls. A hidden lane is left out of exports and
    dimmed in the editor; a locked lane refuses edits in the editor UI.
    Neither ever alters the lane's items."""

    hidden: bool = False
    locked: bool = False


class HookTiming(Model):
    start: float = Field(ge=0, le=86400)
    end: float = Field(gt=0, le=86400)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Title end must follow its start")
        return self


class TextTransform(Model):
    x: float = Field(default=0, ge=-1, le=1)
    y: float = Field(default=0, ge=-1, le=1)
    scale: float = Field(default=1, ge=0.25, le=4)


class CaptionTransform(TextTransform):
    size: float | None = Field(default=None, ge=5, le=528)


class EditDocument(Model):
    version: Literal[1] = 1
    segments: list[Segment] = Field(min_length=1, max_length=100)
    words: list[Word] = Field(default_factory=list, max_length=5000)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    framing: Framing = Field(default_factory=Framing)
    effects: Effects = Field(default_factory=Effects)
    volume: float = Field(default=1, ge=0, le=2)
    muted: bool = False
    # AI headline burned into the top safe area for the first seconds, as generated.
    hookTitle: str = Field(default="", max_length=200)
    hookTiming: HookTiming | None = None
    textTransforms: dict[Literal["hook", "captions"], TextTransform] = Field(default_factory=dict)
    captionTransforms: dict[Annotated[str, Field(max_length=300)], CaptionTransform] = Field(default_factory=dict, max_length=5000)
    broll: list[BRollClip] = Field(default_factory=list, max_length=60)
    overlays: list[OverlayItem] = Field(default_factory=list, max_length=60)
    # Per-lane hide/lock controls; lanes a draft doesn't mention are visible
    # and unlocked, so drafts saved before lane controls behave as before.
    lanes: dict[LaneName, LaneState] = Field(default_factory=dict)

    def validate_duration(self, duration: float):
        if any(s.end > duration + 0.05 for s in self.segments):
            raise ValueError("An edit extends past the original clip")
        if any(w.end > duration + 0.05 for w in self.words):
            raise ValueError("A caption extends past the original clip")
        if any(b.end > duration + 0.05 for b in self.broll):
            raise ValueError("B-roll extends past the original clip")
        if any(o.end > duration + 0.05 for o in self.overlays):
            raise ValueError("An overlay extends past the original clip")
        if len({s.id for s in self.segments}) != len(self.segments) or len(
            {w.id for w in self.words}
        ) != len(self.words) or len({b.id for b in self.broll}) != len(self.broll) or len(
            {o.id for o in self.overlays}
        ) != len(self.overlays):
            raise ValueError("Editor item IDs must be unique")
        ordered = sorted(self.broll, key=lambda b: b.start)
        if any(later.start < earlier.end - 0.01 for earlier, later in zip(ordered, ordered[1:])):
            raise ValueError("B-roll clips can't overlap")
        ordered = sorted(self.overlays, key=lambda o: o.start)
        if any(later.start < earlier.end - 0.01 for earlier, later in zip(ordered, ordered[1:])):
            raise ValueError("Overlays can't overlap")
        return self


def editor_dir(temp_dir: str, task_id: str, clip_id: str, filename: str) -> Path:
    # No request-controlled string is ever used as a path component.
    key = hashlib.sha256(f"{task_id}:{clip_id}:{filename}".encode()).hexdigest()
    return Path(temp_dir) / "editor" / key


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        temporary.write_text(json.dumps(value, allow_nan=False))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def editor_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class RevisionConflict(ValueError):
    pass


def save_document(
    directory: Path, document: EditDocument, revision: int, duration: float
):
    document.validate_duration(duration)
    with editor_lock(directory):
        path = directory / "draft.json"
        current = json.loads(path.read_text()) if path.exists() else {"revision": 0}
        if revision != current["revision"]:
            raise RevisionConflict(
                "This clip was edited in another tab. Reload the saved draft before continuing."
            )
        payload = {"revision": revision + 1, "document": document.model_dump()}
        atomic_json(path, payload)
        return payload


def output_size(document: EditDocument, width: int, height: int):
    if document.framing.aspect == "square":
        return 1080, 1080
    if document.framing.aspect == "vertical":
        return 1080, 1920
    scale = min(1, 1920 / max(width, height))
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def mapped_words(document: EditDocument):
    result = []
    cursor = 0.0
    previous_end = None
    previous_words = {}
    ordered_words = sorted(document.words, key=lambda w: w.start)
    for segment in document.segments:
        continuous = previous_end is not None and abs(segment.start - previous_end) < 1e-6
        current_words = {}
        for word in ordered_words:
            start, end = max(segment.start, word.start), min(segment.end, word.end)
            if end > start and word.text.strip():
                piece = {
                    **word.model_dump(),
                    "start": cursor + start - segment.start,
                    "end": cursor + end - segment.start,
                }
                previous = previous_words.get(word.id) if continuous else None
                if previous is not None and abs(previous["end"] - piece["start"]) < 1e-6:
                    previous["end"] = piece["end"]
                    current_words[word.id] = previous
                else:
                    result.append(piece)
                    current_words[word.id] = piece
        cursor += segment.end - segment.start
        previous_end = segment.end
        previous_words = current_words
    return result


def continuous_segments(segments: list[Segment]) -> list[Segment]:
    result = []
    for segment in segments:
        if result and abs(result[-1].end - segment.start) < TIME_EPSILON:
            result[-1] = result[-1].model_copy(update={"end": segment.end})
        else:
            result.append(segment)
    return result


def caption_key(document: EditDocument, words: list[dict]) -> str:
    cursor = 0.0
    segment = document.segments[-1]
    for segment in continuous_segments(document.segments):
        cursor += segment.end - segment.start
        if words[0]["start"] < cursor - TIME_EPSILON:
            break
    return json.dumps([segment.id, words[0]["id"]], separators=(",", ":"), ensure_ascii=False)


def lane_state(document: EditDocument, lane: str) -> LaneState:
    """A lane's controls; lanes a draft doesn't mention are visible + unlocked."""
    return document.lanes.get(lane) or LaneState()


def lane_hidden(document: EditDocument, lane: str) -> bool:
    return lane_state(document, lane).hidden


def lane_locked(document: EditDocument, lane: str) -> bool:
    return lane_state(document, lane).locked


def mapped_broll(document: EditDocument) -> list[dict]:
    """B-roll pieces on the output timeline, following segments like captions do.

    A clip cut by a segment boundary becomes several pieces; only a piece that
    starts (or ends) where the clip does fades in (or out), so a cutaway keeps
    playing across a cut instead of dissolving at it.
    """
    pieces = []
    cursor = 0.0
    ordered = sorted(document.broll, key=lambda b: b.start)
    for segment in document.segments:
        for clip in ordered:
            start, end = max(segment.start, clip.start), min(segment.end, clip.end)
            if end - start >= BROLL_CONTINUATION_MIN_SECONDS - TIME_EPSILON:
                pieces.append(
                    {
                        "id": clip.id,
                        "footage": clip.footage,
                        "query": clip.query,
                        # Review-surface provenance (Slice 6): the
                        # concept + re-resolution inputs ride with the
                        # piece, so a re-render or a combine keeps the
                        # cue's meaning instead of only its footage.
                        "concept": clip.concept,
                        "searchQuery": clip.searchQuery,
                        "searchQueries": list(clip.searchQueries),
                        "avoid": list(clip.avoid),
                        "media": clip.media,
                        "start": cursor + start - segment.start,
                        "duration": end - start,
                        "offset": clip.offset + start - clip.start,
                        "fade_in": clip.fadeIn and start - clip.start < 0.02,
                        "fade_out": clip.fadeOut and clip.end - end < 0.02,
                        "fade_duration": clip.fadeDuration or min(BROLL_FADE_SECONDS, (clip.end - clip.start) / 3),
                    }
                )
        cursor += segment.end - segment.start
    return pieces


def mapped_overlays(document: EditDocument) -> list[dict]:
    """Overlay pieces on the output timeline, following segments like B-roll.

    An item cut by a segment boundary becomes several pieces; the image is a
    still, so unlike B-roll there is no offset to track and nothing fades.
    """
    pieces = []
    cursor = 0.0
    ordered = sorted(document.overlays, key=lambda o: o.start)
    for segment in document.segments:
        for item in ordered:
            start, end = max(segment.start, item.start), min(segment.end, item.end)
            if end - start >= OVERLAY_CONTINUATION_MIN_SECONDS - TIME_EPSILON:
                pieces.append(
                    {
                        "id": item.id,
                        "asset": item.asset,
                        "start": cursor + start - segment.start,
                        "duration": end - start,
                        "x": item.x,
                        "y": item.y,
                        "scale": item.scale,
                    }
                )
        cursor += segment.end - segment.start
    return pieces


def editor_caption_template(style: CaptionStyle) -> dict:
    """The generation template with the editor's own style choices applied on top."""
    from .caption_templates import get_template

    template = dict(get_template(style.template))
    # Templates that emphasise words in their highlight colour follow the editor's choice.
    if (template.get("emphasis_color") or template.get("highlight_color")) == template.get("highlight_color"):
        template["emphasis_color"] = style.accent
    template.update(
        font_color=style.color,
        highlight_color=style.accent,
        uppercase=style.uppercase,
        max_words_per_line=style.wordsPerLine,
    )
    if style.background:
        template["background"] = True
        template["background_color"] = template.get("background_color") or "#00000080"
    else:
        template["background"] = False
        template["background_color"] = None
    if style.karaoke:
        template["animation"] = "karaoke"
    elif template["animation"] == "karaoke":
        template["animation"] = "none"
    return template


def write_ass(path: Path, document: EditDocument, width: int, height: int):
    """Write captions with the generation renderer so edits keep the clip's animation."""
    from .media.captions import build_assemblyai_ass_subtitles

    style = document.captions
    # A hidden captions lane leaves the export, exactly like captions
    # switched off; the words stay in the draft untouched.
    captions_on = style.enabled and not lane_hidden(document, "captions")
    words = mapped_words(document) if captions_on else []
    caption_transform = document.textTransforms.get("captions", TextTransform())
    hook_transform = document.textTransforms.get("hook", TextTransform())
    original_font_px = max(1, round(style.size * width / 1080))
    font_px = max(1, round(style.size * width * caption_transform.scale / 1080))
    placements = []
    for index in range(0, len(words), style.wordsPerLine):
        group = words[index:index + style.wordsPerLine]
        placement = document.captionTransforms.get(caption_key(document, group))
        placements.append(None if placement is None else {
            "font_px": max(1, round((placement.size if placement.size is not None else style.size) * width * placement.scale / 1080)),
            "x": round((0.5 + placement.x) * width),
            "y": round((style.y + placement.y) * height),
        })
    written = build_assemblyai_ass_subtitles(
        path,
        0,
        sum(s.end - s.start for s in document.segments),
        width,
        height,
        path,
        font_family=style.font,
        caption_words=words,
        include_captions=captions_on,
        template_override=editor_caption_template(style),
        font_px_override=font_px,
        # The editor's position is the caption centre, already kept in range.
        center_y_override=round((style.y + caption_transform.y) * height),
        center_x_override=round((0.5 + caption_transform.x) * width),
        hook_editor_style={
            **hook_transform.model_dump(),
            "base_px": max(34, min(66, int(original_font_px * 0.82))),
            **(document.hookTiming.model_dump() if document.hookTiming else {}),
        },
        # Highlights are per occurrence and already include the generated emphasis.
        emphasis_indices=[i for i, w in enumerate(words) if w["highlight"]],
        wrap_lines=True,
        # A hidden hook lane leaves the title out of the export; the title
        # itself stays in the draft.
        hook_title=None if lane_hidden(document, "hook") else document.hookTitle.strip() or None,
        caption_placements=placements,
    )
    if not written:
        # Nothing to show (no captions or hook title): keep ffmpeg's ass filter valid.
        path.write_text(
            f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )


def run_render(command: list[str], directory: Path, job_id: str, duration: float):
    """Poll a cancellable child without buffering unbounded ffmpeg logs."""
    status_path = directory / f"job-{job_id}.json"
    progress_path = directory / f"progress-{job_id}.txt"
    log_path = directory / f"log-{job_id}.txt"
    with log_path.open("w+") as log:
        # -progress/-nostats are ffmpeg flags. A non-ffmpeg command (tests use
        # sleep) must be started unchanged or GNU sleep rejects the extra args
        # and the cancel path never runs.
        launched = list(command)
        if command and Path(command[0]).name == "ffmpeg":
            launched = command[:1] + ["-progress", str(progress_path), "-nostats"] + command[1:]
        process = subprocess.Popen(
            launched,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        try:
            while process.poll() is None:
                if (directory / f"cancel-{job_id}").exists():
                    raise InterruptedError("Export cancelled")
                percent = 0
                if progress_path.exists():
                    for line in progress_path.read_text().splitlines():
                        if line.startswith("out_time_us="):
                            try:
                                percent = min(
                                    99,
                                    int(
                                        float(line.split("=", 1)[1])
                                        / 1_000_000
                                        / duration
                                        * 100
                                    ),
                                )
                            except ValueError:
                                pass
                atomic_json(status_path, {"status": "rendering", "progress": percent})
                time.sleep(0.25)
            if process.returncode:
                log.seek(0)
                raise RuntimeError("Video rendering failed: " + log.read()[-1500:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            progress_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)


def framing_geometry(document: EditDocument, sw: int, sh: int) -> tuple[str, int, int]:
    """The scale/crop/pad chain for a document's framing, with the output
    size it produces. Shared by render_document and the combine worker so
    a combined segment is framed exactly as its own clip's export frames
    it."""
    width, height = output_size(document, sw, sh)
    framing = document.framing
    scale = (max if framing.fit == "cover" else min)(
        width / sw, height / sh
    ) * framing.zoom
    rw, rh = max(2, round(sw * scale / 2) * 2), max(2, round(sh * scale / 2) * 2)
    # Crop overflow and position letterboxing using the same normalized anchor.
    cw, ch = min(width, rw), min(height, rh)
    geometry = f"scale={rw}:{rh},crop={cw}:{ch}:{(rw - cw) * framing.x}:{(rh - ch) * framing.y},pad={width}:{height}:{(width - cw) * framing.x}:{(height - ch) * framing.y}:black,setsar=1"
    return geometry, width, height


def effects_fragment(fx: Effects, width: int) -> str:
    """The colour-effects filter chain for a document's effects at an
    output width. Shared by render_document and the combine worker."""
    # CSS brightness/contrast use RGB multiplication followed by a midpoint shift.
    gain = fx.brightness / 100 * fx.contrast / 100
    offset = 128 * (1 - fx.contrast / 100)
    effects = f"lutrgb=r='clip(val*{gain}+{offset},0,255)':g='clip(val*{gain}+{offset},0,255)':b='clip(val*{gain}+{offset},0,255)',hue=h={fx.hue}:s={fx.saturation / 100}"
    if fx.blur:
        effects += f",gblur=sigma={fx.blur * width / 1080}"
    return effects


def render_document(
    directory: Path,
    job_id: str,
    document: EditDocument,
    preset: str,
    broll_files: dict[str, Path] | None = None,
):
    """Render a draft. ``broll_files`` maps footage keys to local files; B-roll
    whose footage is missing is left out rather than failing the export."""
    from .clip_editor import _double_bitrate, _ffprobe_size, _escape_filter_path, preset_rates
    from .media.broll_overlay import broll_input_args, broll_overlay_graph, normalize_broll_overlays
    from .media.image_overlay import image_input_args, image_overlay_graph
    from .video_utils import ass_fonts_dir

    # A split without a source-time jump is one continuous picture. Render it
    # as such so a split inside a dissolve cannot reset its opacity or discard
    # a sub-frame B-roll fragment. Keep the editable draft and its IDs intact.
    document = document.model_copy(update={"segments": continuous_segments(document.segments)})

    source = directory / ("layout.mp4" if document.framing.fit == "auto" and document.framing.aspect == "vertical" and (directory / "layout.mp4").exists() else "clean.mp4")
    sw, sh = _ffprobe_size(source)
    geometry, width, height = framing_geometry(document, sw, sh)
    effects = effects_fragment(document.effects, width)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    audio = bool(probe.stdout.strip())
    filters, joins = [], []
    for index, segment in enumerate(document.segments):
        filters.append(
            f"[0:v]trim=start={segment.start}:end={segment.end},setpts=PTS-STARTPTS[v{index}]"
        )
        joins.append(f"[v{index}]")
        if audio:
            filters.append(
                f"[0:a]atrim=start={segment.start}:end={segment.end},asetpts=PTS-STARTPTS[a{index}]"
            )
            joins.append(f"[a{index}]")
    filters.append(
        "".join(joins)
        + f"concat=n={len(document.segments)}:v=1:a={int(audio)}[video]"
        + ("[audio]" if audio else "")
    )
    ass = directory / f"captions-{job_id}.ass"
    write_ass(ass, document, width, height)
    fonts = ass_fonts_dir(document.captions.font)
    subtitles = f"ass={_escape_filter_path(ass)}:fontsdir={_escape_filter_path(fonts or Path('fonts'))}"
    total = sum(s.end - s.start for s in document.segments)
    # A hidden lane is left out of the export entirely, the way disabled
    # captions are; its items stay in the draft untouched. With nothing
    # hidden these are the same pieces the export has always used.
    broll_pieces = [] if lane_hidden(document, "broll") else mapped_broll(document)
    overlays = normalize_broll_overlays(
        [
            {**piece, "path": broll_files[piece["footage"].key]}
            for piece in broll_pieces
            if broll_files and piece["footage"].key in broll_files
        ],
        total,
        min_duration=BROLL_MIN_SECONDS,
    )
    # User-placed images composite above B-roll and under the captions.
    # Uploads that are missing are left out rather than failing the export,
    # as with B-roll footage. With an empty lane the graph below is exactly
    # the one existing drafts have always rendered with.
    overlay_pieces = [] if lane_hidden(document, "overlays") else mapped_overlays(document)
    image_pieces = [
        {**piece, "path": directory / piece["asset"]}
        for piece in overlay_pieces
        if (directory / piece["asset"]).is_file()
    ]
    if image_pieces:
        filters.append(f"[video]{geometry},{effects}[framed]")
        label = "[framed]"
        if overlays:
            graph, label = broll_overlay_graph("[framed]", overlays, width, height)
            filters.append(graph)
        graph, label = image_overlay_graph(label, image_pieces, width, height, 1 + len(overlays))
        filters.append(graph)
        filters.append(f"{label}{subtitles}[outv]")
    elif overlays:
        # Footage fills the output frame over the framed, graded picture and
        # under the captions, as in the preview.
        filters.append(f"[video]{geometry},{effects}[framed]")
        graph, label = broll_overlay_graph("[framed]", overlays, width, height)
        filters.append(graph)
        filters.append(f"{label}{subtitles}[outv]")
    else:
        filters.append(f"[video]{geometry},{effects},{subtitles}[outv]")
    # Mixed source/stock frame rates must not make ffmpeg duplicate tail frames.
    filters.append("[outv]fps=30[finalv]")
    if audio:
        # A hidden audio lane silences the export, like muting; the draft
        # keeps its volume. (The video lane's hidden flag is the one lane
        # state with no export meaning — the export IS the picture, so it
        # stays an editor-only state; removing segments is how picture is
        # taken out of an export.)
        filters.append(
            f"[audio]volume={0 if document.muted or lane_hidden(document, 'audio') else document.volume}[outa]"
        )
    output = directory / f"export-{job_id}.mp4"
    temporary = directory / f".export-{job_id}.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        *broll_input_args(overlays),
        *image_input_args(image_pieces),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[finalv]",
    ]
    # One preset table for every export surface (clip_editor.preset_rates).
    # This render always outputs 30 fps (the fps stage above), so the
    # standard-frame-rate row applies where a preset is frame-rate aware.
    video_bitrate, audio_bitrate = preset_rates(preset, 30)
    if audio:
        command += ["-map", "[outa]", "-c:a", "aac", "-b:a", audio_bitrate]
    command += [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-maxrate",
        video_bitrate,
        "-bufsize",
        _double_bitrate(video_bitrate),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-t",
        f"{total:.6f}",
        str(temporary),
    ]
    try:
        run_render(
            command, directory, job_id, sum(s.end - s.start for s in document.segments)
        )
        if (directory / f"cancel-{job_id}").exists():
            raise InterruptedError("Export cancelled")
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        ass.unlink(missing_ok=True)
