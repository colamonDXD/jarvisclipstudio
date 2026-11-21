export interface Segment {
  id: string;
  start: number;
  end: number;
}
export interface CaptionWord {
  id: string;
  start: number;
  end: number;
  text: string;
  highlight: boolean;
}
export interface CaptionStyle {
  enabled: boolean;
  font:
    | "TikTokSans-Regular"
    | "TikTokSans-Bold"
    | "Poppins-ExtraBold"
    | "THEBOLDFONT"
    | "Montserrat-Black"
    | "Montserrat-ExtraBold";
  size: number;
  color: string;
  accent: string;
  y: number;
  background: boolean;
  wordsPerLine: number;
  /** Absent on drafts saved before the editor matched generated captions. */
  karaoke?: boolean;
  uppercase?: boolean;
  /** Caption template the clip was generated with; supplies its animation and outline. */
  template?: string;
}
/** Template styling the editor's own caption controls don't cover. */
export interface CaptionLook {
  animation: "none" | "karaoke" | "pop" | "fade" | "bounce";
  wordPop: boolean;
  wordBox: boolean;
  wordBoxColor: string | null;
  highlightColor: string | null;
  emphasisColor: string | null;
  strokeColor: string | null;
  strokeWidth: number;
  shadow: boolean;
  glow: boolean;
  backgroundColor: string | null;
}
export interface Framing {
  aspect: "vertical" | "square" | "original";
  fit: "contain" | "cover" | "auto";
  zoom: number;
  x: number;
  y: number;
}
export interface Effects {
  brightness: number;
  contrast: number;
  saturation: number;
  blur: number;
  hue: number;
}
/** A stock video rendition; the server only downloads from that provider's CDN. */
export interface BRollFootage {
  provider: "pexels" | "pixabay" | "coverr";
  /** Pexels and Pixabay ids are numbers; Coverr ids arrive as strings. */
  id: number | string;
  width: number;
  height: number;
  duration: number;
  fileUrl: string;
  pageUrl: string;
  thumbnail: string;
  credit: string;
}
/**
 * Stock footage over the clip from `start` to `end` on the source (clean.mp4)
 * timeline, so it follows cuts like captions do. `offset` is where in the
 * footage it begins.
 */
export interface BRollClip {
  id: string;
  start: number;
  end: number;
  offset: number;
  query: string;
  footage: BRollFootage;
  /**
   * Smart B-Roll v2 review surface: the planner's concept sentence for
   * this cue ("what the brain asked for"), the sense-resolved search
   * string, and the re-resolution inputs "Next candidate" needs. All
   * absent on manually added clips, whose panel falls back to `query`.
   */
  concept?: string;
  searchQuery?: string;
  searchQueries?: string[];
  avoid?: string[];
  media?: string;
  /** Combined pieces dissolve only at the original cutaway's outer edges. */
  fadeIn?: boolean;
  fadeOut?: boolean;
  fadeDuration?: number | null;
}
export interface EditDocument {
  version: 1;
  segments: Segment[];
  words: CaptionWord[];
  captions: CaptionStyle;
  framing: Framing;
  effects: Effects;
  volume: number;
  muted: boolean;
  /** Headline shown at the top for the first seconds; absent on very old drafts. */
  hookTitle?: string;
  /** Hook visibility in edited/output seconds. Missing preserves the generated default. */
  hookTiming?: { start: number; end: number } | null;
  /** Normalized offsets from the original text anchors; independent font scales. */
  textTransforms?: Partial<Record<TextLayer, TextTransform>>;
  /** Placement overrides for individual caption occurrences. */
  captionTransforms?: Record<string, CaptionTransform>;
  /** Absent on drafts cached before the editor had B-roll. */
  broll?: BRollClip[];
  /** User-placed images composited above B-roll. Absent on older drafts. */
  overlays?: OverlayItem[];
  /** Per-lane hide/lock controls. Absent on drafts saved before lane controls. */
  lanes?: Partial<Record<LaneId, LaneState>>;
}
export type LaneId = "video" | "broll" | "overlays" | "captions" | "hook" | "audio";
export interface LaneState { hidden: boolean; locked: boolean }
export type TextLayer = "hook" | "captions";
export interface TextTransform { x: number; y: number; scale: number }
export interface CaptionTransform extends TextTransform { size?: number | null }
export const textTransform = (doc: EditDocument, layer: TextLayer): TextTransform =>
  doc.textTransforms?.[layer] ?? { x: 0, y: 0, scale: 1 };
/** Keep the inspector's size in output units, including extreme legacy base sizes. */
export function setCaptionVisualSize(doc: EditDocument, size: number, captionId?: string): EditDocument {
  size = clamp(size, 5, 528);
  if (captionId) return { ...doc, captionTransforms: { ...doc.captionTransforms, [captionId]: { ...(doc.captionTransforms?.[captionId] ?? textTransform(doc, "captions")), size, scale: 1 } } };
  const base = clamp(doc.captions.size, Math.max(20, Math.ceil(size / 4)), Math.min(132, Math.floor(size / 0.25)));
  return { ...doc, captions: { ...doc.captions, size: base }, textTransforms: { ...doc.textTransforms, captions: { ...textTransform(doc, "captions"), scale: size / base } } };
}
export interface ExportJob {
  id: string;
  status: "queued" | "rendering" | "completed" | "failed" | "cancelled";
  progress: number;
  error?: string;
}
export interface AIEditUsage {
  plan: string;
  limit: number | null;
  used: number;
  remaining: number | null;
  resetsAt: string | null;
  allowed: boolean;
}

export interface EditorState {
  status: "unprepared" | "preparing" | "ready" | "failed";
  basis: string;
  error?: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  hasAudio: boolean;
  waveform: number[];
  draft: { revision: number; document: EditDocument };
  original: EditDocument;
  jobs: ExportJob[];
  captionLooks?: Record<string, CaptionLook>;
  /** Whether the server can make AI edits (an Anthropic key is configured). */
  aiEditing?: boolean;
  aiEditUsage?: AIEditUsage;
  hasLayout?: boolean;
  landscapeRecommended?: boolean;
}
export const DEFAULT_EFFECTS: Effects = {
  brightness: 100,
  contrast: 100,
  saturation: 100,
  blur: 0,
  hue: 0,
};
export const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));
export const editDuration = (doc: EditDocument) =>
  doc.segments.reduce((total, s) => total + s.end - s.start, 0);
/** Decode source-continuous splits together without changing the editable draft. */
export function renderSegments(segments: Segment[]): Segment[] {
  const result: Segment[] = [];
  for (const segment of segments) {
    const previous = result.at(-1);
    if (previous && Math.abs(previous.end - segment.start) < 1e-9) previous.end = segment.end;
    else result.push({ ...segment });
  }
  return result;
}
/** Hook titles are timed on the edited output, including their fade margins. */
export function hookRange(doc: EditDocument) {
  const duration = editDuration(doc);
  if (doc.hookTiming) {
    const start = clamp(doc.hookTiming.start, 0, Math.max(0, duration - .04));
    return { start, end: clamp(doc.hookTiming.end, start + Math.min(.04, duration), duration) };
  }
  return duration <= 1.5
    ? { start: 0, end: Math.max(0.5, duration) }
    : { start: 0.12, end: Math.min(4, Math.max(1.5, duration - 0.25)) };
}
/** Move one title edge, preserving a visible interval and the edited clip's bounds. */
export function trimHook(doc: EditDocument, edge: "start" | "end", seconds: number): EditDocument {
  const range = hookRange(doc), minimum = Math.min(.1, editDuration(doc));
  const value = edge === "start" ? clamp(seconds, 0, range.end - minimum) : clamp(seconds, range.start + minimum, editDuration(doc));
  return { ...doc, hookTiming: { ...range, [edge]: value } };
}
export const timecode = (time: number) =>
  `${Math.floor(Math.max(0, time) / 60)}:${(Math.max(0, time) % 60).toFixed(2).padStart(5, "0")}`;
export const uid = () => crypto.randomUUID();
export function outputSize(doc: EditDocument, width: number, height: number) {
  if (doc.framing.aspect === "vertical") return { width: 1080, height: 1920 };
  if (doc.framing.aspect === "square") return { width: 1080, height: 1080 };
  const scale = Math.min(1, 1920 / Math.max(width, height));
  return {
    width: Math.max(2, Math.floor((width * scale) / 2) * 2),
    height: Math.max(2, Math.floor((height * scale) / 2) * 2),
  };
}
export function frameRect(
  frame: Framing,
  sw: number,
  sh: number,
  width: number,
  height: number,
) {
  const scale =
    (frame.fit === "cover" ? Math.max : Math.min)(width / sw, height / sh) *
    frame.zoom;
  const w = Math.round((sw * scale) / 2) * 2,
    h = Math.round((sh * scale) / 2) * 2;
  return {
    x: (width - w) * frame.x,
    y: (height - h) * frame.y,
    width: w,
    height: h,
  };
}
export function mappedWords(doc: EditDocument): CaptionWord[] {
  const result: CaptionWord[] = [];
  let cursor = 0;
  let previousEnd: number | null = null;
  let previousWords = new Map<string, CaptionWord>();
  const orderedWords = [...doc.words].sort((a, b) => a.start - b.start);
  for (const segment of doc.segments) {
    const continuous = previousEnd !== null && Math.abs(segment.start - previousEnd) < 1e-6;
    const currentWords = new Map<string, CaptionWord>();
    for (const word of orderedWords) {
      const start = Math.max(segment.start, word.start),
        end = Math.min(segment.end, word.end);
      if (end > start && word.text.trim()) {
        const piece = {
          ...word,
          start: cursor + start - segment.start,
          end: cursor + end - segment.start,
        };
        const previous = continuous ? previousWords.get(word.id) : undefined;
        if (previous && Math.abs(previous.end - piece.start) < 1e-6) {
          previous.end = piece.end;
          currentWords.set(word.id, previous);
        } else {
          result.push(piece);
          currentWords.set(word.id, piece);
        }
      }
    }
    cursor += segment.end - segment.start;
    previousEnd = segment.end;
    previousWords = currentWords;
  }
  return result;
}
/** Fixed-size word groups, chunked exactly as the generation renderer does. */
export function captionGroups(words: CaptionWord[], count: number) {
  const groups: CaptionWord[][] = [];
  for (let i = 0; i < words.length; i += count)
    groups.push(words.slice(i, i + count));
  return groups;
}
/** A source-continuous run survives a split; repeated footage has its own ID. */
export function captionKey(doc: EditDocument, group: CaptionWord[]): string {
  let cursor = 0;
  for (const segment of renderSegments(doc.segments)) {
    cursor += segment.end - segment.start;
    if (group[0].start < cursor - 1e-9) return JSON.stringify([segment.id, group[0].id]);
  }
  return JSON.stringify([doc.segments.at(-1)?.id, group[0].id]);
}
export function captionTransform(doc: EditDocument, group: CaptionWord[]): CaptionTransform {
  return doc.captionTransforms?.[captionKey(doc, group)] ?? textTransform(doc, "captions");
}
export function captionAt(doc: EditDocument, time: number) {
  return captionGroups(mappedWords(doc), doc.captions.wordsPerLine)
    .find(words => time >= words[0].start && time < words[words.length - 1].end);
}

/** Keep local placement with the same spoken phrase when its text/IDs change. */
export function remapCaptionTransforms(before: EditDocument, after: EditDocument): EditDocument {
  if (!before.captionTransforms || !Object.keys(before.captionTransforms).length) return after;
  const previous = captionGroups(mappedWords(before), before.captions.wordsPerLine)
    .map(words => ({ words, key: captionKey(before, words) }));
  const placements: Record<string, CaptionTransform> = {};
  for (const words of captionGroups(mappedWords(after), after.captions.wordsPerLine)) {
    const key = captionKey(after, words);
    if (after.captionTransforms?.[key]) { placements[key] = after.captionTransforms[key]; continue; }
    const run = JSON.parse(key)[0];
    let best = 0;
    for (const old of previous) {
      const placement = before.captionTransforms[old.key];
      if (!placement || JSON.parse(old.key)[0] !== run) continue;
      const overlap = Math.min(words.at(-1)!.end, old.words.at(-1)!.end) - Math.max(words[0].start, old.words[0].start);
      if (overlap > best) { best = overlap; placements[key] = placement; }
    }
  }
  return { ...after, captionTransforms: placements };
}
/** The spoken word stays active until the next word in its group starts. */
export function activeWordIndex(group: CaptionWord[], time: number) {
  for (let i = group.length - 1; i >= 0; i--)
    if (time >= group[i].start) return i;
  return -1;
}
export function outputTime(
  doc: EditDocument,
  segmentIndex: number,
  sourceTime: number,
) {
  return (
    doc.segments
      .slice(0, segmentIndex)
      .reduce((sum, s) => sum + s.end - s.start, 0) +
    sourceTime -
    doc.segments[segmentIndex].start
  );
}
export function splitSegment(
  doc: EditDocument,
  index: number,
  at: number,
): EditDocument {
  const segment = doc.segments[index];
  if (!segment || at - segment.start < 0.04 || segment.end - at < 0.04)
    return doc;
  const segments = [...doc.segments];
  segments.splice(
    index,
    1,
    { ...segment, end: at },
    { id: uid(), start: at, end: segment.end },
  );
  return { ...doc, segments };
}
/** Correcting a phrase retains existing time anchors; inserted words share only the replaced span. */
export function replaceWords(
  words: CaptionWord[],
  text: string,
  duration: number,
): CaptionWord[] {
  const tokens = text.trim().split(/\s+/).filter(Boolean);
  // LCS anchors preserve unchanged words, including those after an insertion/deletion.
  const rows = words.length + 1,
    cols = tokens.length + 1;
  if (rows * cols > 1_000_000)
    throw new Error("Edit this long transcript one word at a time.");
  const table = Array.from({ length: rows }, () => new Uint16Array(cols));
  for (let i = words.length - 1; i >= 0; i--)
    for (let j = tokens.length - 1; j >= 0; j--)
      table[i][j] =
        words[i].text === tokens[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
  const anchors: [number, number][] = [[-1, -1]];
  let i = 0,
    j = 0;
  while (i < words.length && j < tokens.length) {
    if (words[i].text === tokens[j]) {
      anchors.push([i++, j++]);
    } else if (table[i + 1][j] >= table[i][j + 1]) i++;
    else j++;
  }
  anchors.push([words.length, tokens.length]);
  const result: CaptionWord[] = [];
  for (let k = 1; k < anchors.length; k++) {
    const [prevI, prevJ] = anchors[k - 1],
      [nextI, nextJ] = anchors[k];
    const count = nextJ - prevJ - 1;
    const start =
      nextI > prevI + 1 ? words[prevI + 1].start : (words[prevI]?.end ?? 0);
    const end =
      nextI > prevI + 1
        ? words[nextI - 1].end
        : (words[nextI]?.start ?? duration);
    for (let n = 0; n < count; n++) {
      const from = start + (Math.max(0.01, end - start) * n) / count;
      result.push({
        id: uid(),
        text: tokens[prevJ + n + 1].slice(0, 120),
        start: clamp(from, 0, duration - 0.01),
        end: clamp(
          start + (Math.max(0.01, end - start) * (n + 1)) / count,
          from + 0.001,
          duration,
        ),
        highlight: false,
      });
    }
    if (nextI < words.length) result.push({ ...words[nextI] });
  }
  return result;
}

/** Replace one displayed phrase using original source timings, including after cuts. */
export function replaceCaptionGroup(doc: EditDocument, ids: string[], text: string, duration: number): EditDocument {
  const selected = new Set(ids);
  const byId = new Map(doc.words.map(word => [word.id, word]));
  const words = [...selected].flatMap(id => byId.has(id) ? [byId.get(id)!] : []);
  if (!words.length) return doc;
  let replacement: CaptionWord[];
  if (words.every((word, index) => !index || word.start >= words[index - 1].start)) {
    replacement = replaceWords(words, text, duration);
  } else {
    // A phrase may cross reordered segments. Align the typed words in display
    // order, then put each result back inside its original source-word slot.
    const slots = words.map((word, index) => ({ ...word, start: index, end: index + 1 }));
    replacement = replaceWords(slots, text, slots.length).map(word => {
      const index = Math.min(words.length - 1, Math.floor((word.start + word.end) / 2));
      const source = words[index], span = source.end - source.start, minimum = Math.min(0.001, span / 2);
      const start = source.start + clamp(word.start - index, 0, 1 - minimum / span) * span;
      return { ...word, start, end: Math.max(start + minimum, source.start + clamp(word.end - index, 0, 1) * span) };
    });
  }
  replacement.sort((a, b) => a.start - b.start);
  let inserted = false;
  return remapCaptionTransforms(doc, { ...doc, words: doc.words.flatMap(word => {
    if (!selected.has(word.id)) return [word];
    if (inserted) return [];
    inserted = true;
    return replacement;
  }) });
}

export interface OverlayItem {
  id: string;
  /** Source-timeline placement, like a B-roll clip. */
  start: number;
  end: number;
  /** Stored upload name inside the editor directory (overlay-<uuid>.<ext>). */
  asset: string;
  name: string;
  /** Centre of the image as a fraction of the frame (0-1). */
  x: number;
  y: number;
  /** Width as a fraction of the frame width (0.05-1). */
  scale: number;
}

export const BROLL_MIN_SECONDS = 0.2;
export const BROLL_CONTINUATION_MIN_SECONDS = 0.04;
export const BROLL_FADE_SECONDS = 0.25;
export const brollMinimum = (clip: BRollClip) =>
  clip.fadeIn === false || clip.fadeOut === false ? BROLL_CONTINUATION_MIN_SECONDS : BROLL_MIN_SECONDS;
export const brollFadeDuration = (clip: BRollClip) =>
  clip.fadeDuration ?? Math.min(BROLL_FADE_SECONDS, (clip.end - clip.start) / 3);
export const brollOf = (doc: EditDocument) => doc.broll ?? [];
export const footageKey = (footage: BRollFootage) =>
  // Provider-prefixed, like the server's render lookup: a Pixabay id can
  // equal a Pexels id, and two different assets must never share one key.
  `${footage.provider}:${footage.id}/${footage.width}x${footage.height}`;
export const footageUrl = (endpoint: string, footage: BRollFootage) =>
  // Pexels renditions stream from the backend's Pexels endpoint; Pixabay
  // and Coverr renditions stream from the shared stock library on disk,
  // same origin as the clip, so the preview never depends on a stock
  // CDN's CORS headers.
  footage.provider === "pexels"
    ? `${endpoint}/broll/footage/${footage.id}/${footage.width}x${footage.height}.mp4`
    : `${endpoint}/broll/footage/library/${footage.provider}/${footage.id}`;

/** The B-roll clip on screen at a source time, if any. */
export function brollAt(doc: EditDocument, sourceTime: number) {
  return brollOf(doc).find((b) => sourceTime >= b.start && sourceTime < b.end) ?? null;
}

/**
 * How opaque a B-roll clip is at a source time: it dissolves in over its first
 * and out over its last quarter second, as the export does.
 */
export function brollAlpha(clip: BRollClip, sourceTime: number) {
  const fade = brollFadeDuration(clip);
  if (fade <= 0) return 1;
  return clamp(Math.min(
    clip.fadeIn === false ? 1 : (sourceTime - clip.start) / fade,
    clip.fadeOut === false ? 1 : (clip.end - sourceTime) / fade,
  ), 0, 1);
}

/** Room for a B-roll clip around `at`: from the previous clip's end to the next one's start. */
export function brollGap(doc: EditDocument, at: number, duration: number, ignore?: string) {
  let start = 0,
    end = duration;
  for (const b of brollOf(doc)) {
    if (b.id === ignore) continue;
    if (b.end <= at) start = Math.max(start, b.end);
    else if (b.start > at) end = Math.min(end, b.start);
    else return null;
  }
  return { start, end };
}

/**
 * Insert footage at `at` (source seconds) for up to `length` seconds, fitted
 * into the free space there and the footage's own length. Null when no room.
 */
export function insertBroll(
  doc: EditDocument,
  footage: BRollFootage,
  at: number,
  duration: number,
  query = "",
  length = 3,
): { doc: EditDocument; id: string } | null {
  const gap = brollGap(doc, at, duration);
  if (!gap) return null;
  const start = clamp(at, gap.start, gap.end);
  const end = Math.min(gap.end, start + Math.min(length, footage.duration));
  if (end - start < BROLL_MIN_SECONDS - 1e-9) return null;
  const id = uid();
  const clip: BRollClip = { id, start, end, offset: 0, query, footage };
  const broll = [...brollOf(doc), clip].sort((a, b) => a.start - b.start);
  return { doc: { ...doc, broll }, id };
}

/**
 * Move or resize a B-roll clip, kept inside the clip, clear of its neighbours,
 * and no longer than its footage (after its offset).
 */
export function placeBroll(
  doc: EditDocument,
  id: string,
  start: number,
  end: number,
  duration: number,
): EditDocument {
  const clip = brollOf(doc).find((b) => b.id === id);
  if (!clip) return doc;
  const others = brollOf(doc).filter((b) => b.id !== id);
  const before = Math.max(0, ...others.filter((b) => b.end <= clip.start + 1e-6).map((b) => b.end));
  const after = Math.min(duration, ...others.filter((b) => b.start >= clip.end - 1e-6).map((b) => b.start));
  const minimum = brollMinimum(clip);
  const maxLength = Math.max(minimum, clip.footage.duration - clip.offset);
  let nextStart = clamp(start, before, after - minimum);
  let nextEnd = clamp(end, nextStart + minimum, Math.min(after, nextStart + maxLength));
  if (Math.abs(end - start - (clip.end - clip.start)) < 1e-6) {
    // A move keeps the length; clamping one edge shifts the other.
    const length = Math.min(clip.end - clip.start, after - before);
    nextStart = clamp(start, before, after - length);
    nextEnd = nextStart + length;
  }
  return {
    ...doc,
    broll: brollOf(doc).map((b) => (b.id === id ? { ...b, start: nextStart, end: nextEnd } : b)),
  };
}

/** Map a track edge back to source time; a right edge belongs to the preceding segment. */
function sourceAtTrackTime(doc: EditDocument, time: number, edge: "start" | "end") {
  const items = sequence(doc);
  const at = clamp(time, 0, editDuration(doc));
  const index = items.findIndex(item => at >= item.start && (edge === "end" ? at <= item.end : at < item.end));
  const i = index < 0 ? items.length - 1 : index, item = items[i];
  // A split with no source-time jump is still continuous footage. Clamp to
  // that whole run, so dragging a continuation cannot snap to the split edge.
  let first = i, last = i;
  while (first > 0 && Math.abs(items[first - 1].segment.end - items[first].segment.start) < 1e-6) first--;
  while (last + 1 < items.length && Math.abs(items[last].segment.end - items[last + 1].segment.start) < 1e-6) last++;
  return { source: item.segment.start + at - item.start, segment: { ...item.segment, start: items[first].segment.start, end: items[last].segment.end } };
}

/** Timeline gestures use edit seconds; persisted B-roll stays anchored to source footage. */
export function placeBrollOnTimeline(
  doc: EditDocument, id: string, piece: { start: number; end: number; offset: number },
  mode: "move" | "start" | "end", delta: number, duration: number,
): EditDocument {
  const clip = brollOf(doc).find(clip => clip.id === id);
  if (!clip) return doc;
  const edge = mode === "end" ? "end" : "start";
  const origin = clip.start + piece.offset - clip.offset + (edge === "end" ? piece.end - piece.start : 0);
  const target = sourceAtTrackTime(doc, piece[edge] + delta, edge);
  // At a cut, keep the grabbed piece inside the destination's kept source
  // range instead of leaving only a sliver before a removed interval.
  const visible = Math.min(piece.end - piece.start, target.segment.end - target.segment.start);
  const destination = mode === "move" ? clamp(target.source, target.segment.start, target.segment.end - visible) : target.source;
  const shift = destination - origin;
  const placed = placeBroll(doc, id, clip.start + (mode === "end" ? 0 : shift), clip.end + (mode === "start" ? 0 : shift), duration);
  if (mode !== "start") return placed;
  // Head trimming keeps the remaining stock frames in place.
  return { ...placed, broll: brollOf(placed).map(b => b.id === id ? { ...b, offset: Math.max(0, clip.offset + b.start - clip.start) } : b) };
}

/** B-roll pieces on the output timeline, mirroring the server's mapped_broll. */
export function mappedBroll(doc: EditDocument) {
  const pieces: {
    segmentId: string;
    clip: BRollClip;
    start: number;
    end: number;
    offset: number;
    fadeIn: boolean;
    fadeOut: boolean;
    fadeDuration: number;
  }[] = [];
  let cursor = 0;
  const ordered = [...brollOf(doc)].sort((a, b) => a.start - b.start);
  for (const segment of doc.segments) {
    for (const clip of ordered) {
      const start = Math.max(segment.start, clip.start),
        end = Math.min(segment.end, clip.end);
      if (end - start >= BROLL_CONTINUATION_MIN_SECONDS - 1e-9)
        pieces.push({
          segmentId: segment.id,
          clip,
          start: cursor + start - segment.start,
          end: cursor + end - segment.start,
          offset: clip.offset + start - clip.start,
          fadeIn: clip.fadeIn !== false && start - clip.start < 0.02,
          fadeOut: clip.fadeOut !== false && clip.end - end < 0.02,
          fadeDuration: brollFadeDuration(clip),
        });
    }
    cursor += segment.end - segment.start;
  }
  return pieces;
}

/** Segments laid out back to back on the edit (output) timeline. */
export const OVERLAY_MIN_SECONDS = 0.2;
const OVERLAY_CONTINUATION_MIN_SECONDS = 0.04;

export const overlaysOf = (doc: EditDocument) => doc.overlays ?? [];

/** A lane's controls; lanes a draft doesn't mention are visible and unlocked. */
export const laneStateOf = (doc: EditDocument, lane: LaneId): LaneState => ({
  hidden: doc.lanes?.[lane]?.hidden ?? false,
  locked: doc.lanes?.[lane]?.locked ?? false,
});
export const laneHidden = (doc: EditDocument, lane: LaneId) => laneStateOf(doc, lane).hidden;
export const laneLocked = (doc: EditDocument, lane: LaneId) => laneStateOf(doc, lane).locked;

/** Captions reach an export only when enabled and their lane is not
 * hidden — the same rule the server render applies. */
export const captionsInExport = (doc: EditDocument) =>
  doc.captions.enabled && !laneHidden(doc, "captions");

/** The gain an export applies: mute and a hidden audio lane both
 * silence the file, again mirroring the server render. */
export const exportGain = (doc: EditDocument) =>
  doc.muted || laneHidden(doc, "audio") ? 0 : doc.volume;

/**
 * Set one lane control. The stored entry is always the full
 * `{hidden, locked}` pair the backend model dumps, and a lane back at the
 * defaults is dropped again — an all-default draft keeps no `lanes` key,
 * exactly like a draft saved before lane controls existed.
 */
export function withLaneState(doc: EditDocument, lane: LaneId, patch: Partial<LaneState>): EditDocument {
  const state = { ...laneStateOf(doc, lane), ...patch };
  const lanes: Partial<Record<LaneId, LaneState>> = { ...doc.lanes };
  if (!state.hidden && !state.locked) delete lanes[lane];
  else lanes[lane] = state;
  const next = { ...doc };
  if (Object.keys(lanes).length) next.lanes = lanes;
  else delete next.lanes;
  return next;
}

/** The overlay on screen at a source time, if any. */
export function overlayAt(doc: EditDocument, sourceTime: number) {
  return overlaysOf(doc).find((item) => sourceTime >= item.start && sourceTime < item.end) ?? null;
}

/** Room for an overlay around `at`: from the previous item's end to the next one's start. */
export function overlayGap(doc: EditDocument, at: number, duration: number, ignore?: string) {
  let start = 0;
  let end = duration;
  for (const item of overlaysOf(doc)) {
    if (item.id === ignore) continue;
    if (item.end <= at && item.end > start) start = item.end;
    if (item.start >= at && item.start < end) end = item.start;
    if (at >= item.start && at < item.end) return null;
  }
  return { start, end };
}

/** Place an uploaded image on the overlay lane, like B-roll is placed on its lane. */
export function insertOverlay(
  doc: EditDocument,
  asset: string,
  name: string,
  at: number,
  duration: number,
  length = 3,
) {
  const gap = overlayGap(doc, at, duration);
  if (!gap) return null;
  const start = clamp(at, gap.start, gap.end);
  const end = Math.min(gap.end, start + length);
  if (end - start < OVERLAY_MIN_SECONDS - 1e-9) return null;
  const id = uid();
  const item: OverlayItem = { id, start, end, asset, name, x: 0.5, y: 0.5, scale: 0.4 };
  return { doc: { ...doc, overlays: [...overlaysOf(doc), item].sort((a, b) => a.start - b.start) }, id };
}

/** Move or trim an overlay without overlapping its neighbours, as with B-roll. */
export function placeOverlay(
  doc: EditDocument,
  id: string,
  start: number,
  end: number,
  duration: number,
): EditDocument {
  const item = overlaysOf(doc).find((o) => o.id === id);
  if (!item) return doc;
  const others = overlaysOf(doc).filter((o) => o.id !== id);
  const before = Math.max(0, ...others.filter((o) => o.end <= item.start + 1e-6).map((o) => o.end));
  const after = Math.min(duration, ...others.filter((o) => o.start >= item.end - 1e-6).map((o) => o.start));
  let nextStart = clamp(start, before, after - OVERLAY_MIN_SECONDS);
  let nextEnd = clamp(end, nextStart + OVERLAY_MIN_SECONDS, after);
  if (Math.abs(end - start - (item.end - item.start)) < 1e-6) {
    // A move keeps the length exactly, so the dragged edge never drifts.
    const length = Math.min(item.end - item.start, after - before);
    nextStart = clamp(start, before, after - length);
    nextEnd = nextStart + length;
  }
  return {
    ...doc,
    overlays: overlaysOf(doc).map((o) => (o.id === id ? { ...o, start: nextStart, end: nextEnd } : o)),
  };
}

/** Drag an overlay piece like a B-roll piece, converting across segment edits. */
export function placeOverlayOnTimeline(
  doc: EditDocument,
  id: string,
  piece: { start: number; end: number; sourceStart: number },
  mode: "move" | "start" | "end",
  delta: number,
  duration: number,
) {
  const item = overlaysOf(doc).find((o) => o.id === id);
  if (!item) return doc;
  const edge = mode === "end" ? "end" : "start";
  const origin = piece.sourceStart + (edge === "end" ? piece.end - piece.start : 0);
  const target = sourceAtTrackTime(doc, piece[edge] + delta, edge);
  const visible = Math.min(piece.end - piece.start, target.segment.end - target.segment.start);
  const destination =
    mode === "move"
      ? clamp(target.source, target.segment.start, target.segment.end - visible)
      : target.source;
  const shift = destination - origin;
  return placeOverlay(
    doc,
    id,
    item.start + (mode === "end" ? 0 : shift),
    item.end + (mode === "start" ? 0 : shift),
    duration,
  );
}

/** Overlay pieces on the output timeline, mirroring the server's mapped_overlays. */
export function mappedOverlays(doc: EditDocument) {
  const pieces: Array<{
    segmentId: string;
    item: OverlayItem;
    start: number;
    end: number;
    sourceStart: number;
  }> = [];
  let out = 0;
  for (const segment of doc.segments) {
    const segmentOut = out;
    out += Math.max(0, segment.end - segment.start);
    for (const item of overlaysOf(doc)) {
      const start = Math.max(item.start, segment.start);
      const end = Math.min(item.end, segment.end);
      if (end - start < OVERLAY_CONTINUATION_MIN_SECONDS - 1e-9) continue;
      pieces.push({
        segmentId: segment.id,
        item,
        start: segmentOut + start - segment.start,
        end: segmentOut + end - segment.start,
        sourceStart: start,
      });
    }
  }
  return pieces.sort((a, b) => a.start - b.start);
}

/** The mapped overlay piece on screen at an output (edit-timeline)
 * time, if any — what an export draws at that moment. */
export function overlayPieceAt(
  pieces: ReturnType<typeof mappedOverlays>,
  at: number,
) {
  return pieces.find((piece) => at >= piece.start && at < piece.end) ?? null;
}

export function sequence(doc: EditDocument) {
  let cursor = 0;
  return doc.segments.map((segment, index) => {
    const start = cursor;
    cursor += segment.end - segment.start;
    return { segment, index, start, end: cursor };
  });
}

/** The segment and source time playing at an edit (output) time. */
export function locate(doc: EditDocument, time: number) {
  const items = sequence(doc);
  const total = items.length ? items[items.length - 1].end : 0;
  const at = clamp(time, 0, Math.max(0, total - 0.001));
  const item = items.find((i) => at >= i.start && at < i.end) ?? items[items.length - 1];
  return { index: item.index, source: item.segment.start + at - item.start };
}
