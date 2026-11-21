import { pollClipRerender } from "./clip-rerender";

/**
 * Clip editor (segment operations + Caption Studio overrides).
 *
 * Mirrors the backend shapes from src/auto_director/clip_edits.py: segment
 * times are seconds on the clip timeline and always tile [0, total]; the
 * backend is the authority and rejects impossible edits with 422.
 */

export type EditorSegment = {
  index: number;
  start: number;
  end: number;
  duration: number;
  transition_in: string;
  transition_duration: number;
};

export type TrimOperation = {
  type: "trim";
  index: number;
  start?: number;
  end?: number;
};

export type SplitOperation = { type: "split"; index: number; at: number };

export type SegmentOperation =
  | TrimOperation
  | { type: "delete"; index: number }
  | SplitOperation
  | { type: "reorder"; order: number[] };

export type CaptionOverrides = {
  template?: string;
  font_family?: string;
  font_size?: number;
  font_color?: string;
  position?: string;
  position_y?: number;
  words_per_line?: number;
};

export type GradeValues = {
  exposure: number;
  contrast: number;
  saturation: number;
  temperature: number;
  tint: number;
  highlights: number;
  shadows: number;
};

export type VfxValues = {
  grain: number;
  vignette: number;
  glow: number;
  sharpen: number;
  letterbox: number;
  shake: number;
};

/**
 * The grade's stored form: the slider fields the user set, plus the
 * optional `lut` — the filename of an imported .cube LUT acting as the
 * grade's base, or null as the explicit "remove my LUT" marker.
 */
export type GradeOverrides = Partial<GradeValues> & {
  lut?: string | null;
};

/** The studio panels' stored form: only the fields the user set. */
export type LookOverrides = {
  transition?: string;
  grade?: GradeOverrides;
  vfx?: Partial<VfxValues>;
};

/** The effective look the next render would use (profile look + user). */
export type LookState = {
  transition: string;
  grade: GradeValues;
  vfx: VfxValues;
  profile_lut: string;
  /** The imported .cube LUT (stored filename) grading this clip, if any. */
  lut?: string | null;
  stored: boolean;
};

/** One imported .cube LUT in the user's library. */
export type LutOption = {
  filename: string;
  display_name: string;
  size: number;
  title: string | null;
};

export type TransitionOption = {
  name: string;
  effect: string | null;
  group: string;
};

export type AudioValues = {
  music_track: string;
  music_volume: number;
  ducking_db: number;
  noise_reduction: number;
  voice_clarity: number;
  loudness: string;
  fade_in: number;
  fade_out: number;
};

/** The Audio Studio's stored form: only the fields the user set. */
export type AudioOverrides = Partial<AudioValues>;

export type MusicTrackOption = {
  filename: string;
  display_name: string;
  source: string;
};

export type VoiceoverState = {
  script: string;
  voice: string;
  mode: string;
} | null;

export type VoiceOption = {
  name: string;
  label: string;
};

/** The effective audio state the next render would use. */
export type AudioState = AudioValues & {
  stored: boolean;
  tracks: MusicTrackOption[];
  voiceover_enabled: boolean;
  voiceover: VoiceoverState;
  voices: VoiceOption[];
};

export type VoiceoverRequest =
  | { script: string; voice: string; mode: string }
  | { clear: true };

/** One user-placed sound effect on the clip's timeline. */
export type SfxPlacement = {
  name: string;
  at_sec: number;
  gain_db: number;
};

/** One placeable file in the SFX library (bundled assets or user upload). */
export type SfxLibraryEntry = {
  filename: string;
  display_name: string;
  duration: number | null;
  source?: "bundled" | "user";
};

/** The SFX panel's state: stored placements + the placeable library. */
export type SfxState = {
  placements: SfxPlacement[];
  library: SfxLibraryEntry[];
};

export type GradePreset = {
  name: string;
  grade: GradeValues;
};

export type EditPlan = {
  version: number;
  niche: string | null;
  total_duration: number;
  segments: EditorSegment[];
  captions: CaptionOverrides | null;
  look?: LookOverrides | null;
  audio?: AudioOverrides | null;
  voiceover?: VoiceoverState;
};

export type EditPlanResponse = {
  clip_id: string;
  task_id: string;
  persisted: boolean;
  plan: EditPlan;
  niche?: string | null;
  look?: LookState;
  audio?: AudioState;
  transitions?: TransitionOption[];
  grade_presets?: GradePreset[];
  luts?: LutOption[];
  sfx?: SfxState;
};

export type ClipEditRequest = {
  operations?: SegmentOperation[];
  captions?: CaptionOverrides;
  niche_override?: string;
  music_override?: string;
  look?: LookOverrides;
  audio?: AudioOverrides;
  voiceover?: VoiceoverRequest;
  /** Full replacement placement list; an empty list clears them all. */
  sfx?: SfxPlacement[];
};

/**
 * Diff an Audio Studio draft against the clip's current effective
 * audio. Only changed fields are sent: the backend stores them as the
 * user's partial override over the profile's own audio, so untouched
 * fields keep following the profile. Returns null when nothing
 * changed.
 */
export function buildAudioOverrides(
  initial: AudioValues,
  draft: AudioValues,
): AudioOverrides | null {
  const audio: AudioOverrides = {};
  (Object.keys(draft) as (keyof AudioValues)[]).forEach((key) => {
    if (draft[key] !== initial[key]) {
      (audio as Record<string, unknown>)[key] = draft[key];
    }
  });
  return Object.keys(audio).length > 0 ? audio : null;
}

/**
 * Diff a studio draft against the clip's current effective look. Only
 * changed fields are sent: the backend stores them as the user's partial
 * override over the profile's own grade, so untouched fields keep
 * following the profile. Returns null when nothing changed.
 */
export function buildLookOverrides(
  initial: LookState,
  draft: {
    transition: string;
    grade: GradeValues;
    vfx: VfxValues;
    lut?: string | null;
  },
): LookOverrides | null {
  const look: LookOverrides = {};
  if (draft.transition !== initial.transition) {
    look.transition = draft.transition;
  }
  const grade: GradeOverrides = {};
  (Object.keys(draft.grade) as (keyof GradeValues)[]).forEach((key) => {
    if (draft.grade[key] !== initial.grade[key]) grade[key] = draft.grade[key];
  });
  // The LUT rides with the grade: send it when it changed (a pick or an
  // explicit clear), and also alongside any slider change while one is
  // active — a stored grade REPLACES the previous one, so omitting an
  // unchanged LUT from a slider-only apply would silently drop it.
  const initialLut = initial.lut ?? null;
  const draftLut = draft.lut === undefined ? initialLut : draft.lut;
  if (
    draftLut !== initialLut ||
    (draftLut !== null && Object.keys(grade).length > 0)
  ) {
    grade.lut = draftLut;
  }
  if (Object.keys(grade).length > 0) look.grade = grade;
  const vfx: Partial<VfxValues> = {};
  (Object.keys(draft.vfx) as (keyof VfxValues)[]).forEach((key) => {
    if (draft.vfx[key] !== initial.vfx[key]) vfx[key] = draft.vfx[key];
  });
  if (Object.keys(vfx).length > 0) look.vfx = vfx;
  return Object.keys(look).length > 0 ? look : null;
}

const round3 = (value: number) => Math.round(value * 1000) / 1000;

/** Shift a segment earlier in the timeline; returns null when it cannot move. */
export function moveSegmentUp(
  segments: EditorSegment[],
  index: number,
): SegmentOperation | null {
  return buildMoveOperation(segments, index, -1);
}

/** Shift a segment later in the timeline; returns null when it cannot move. */
export function moveSegmentDown(
  segments: EditorSegment[],
  index: number,
): SegmentOperation | null {
  return buildMoveOperation(segments, index, 1);
}

function buildMoveOperation(
  segments: EditorSegment[],
  index: number,
  delta: number,
): SegmentOperation | null {
  const target = index + delta;
  if (index < 0 || index >= segments.length || target < 0 || target >= segments.length) {
    return null;
  }
  const order = segments.map((segment) => segment.index);
  const [moved] = order.splice(index, 1);
  order.splice(target, 0, moved);
  return { type: "reorder", order };
}

/** Split a segment at its midpoint (the panel's one-click split). */
export function splitAtMidpoint(segment: EditorSegment): SplitOperation {
  return {
    type: "split",
    index: segment.index,
    at: round3((segment.start + segment.end) / 2),
  };
}

/** Trim a segment to the given bounds, clamped to its current window. */
export function trimSegment(
  segment: EditorSegment,
  start: number,
  end: number,
): TrimOperation | null {
  const clampedStart = Math.min(Math.max(start, segment.start), segment.end);
  const clampedEnd = Math.max(Math.min(end, segment.end), segment.start);
  if (clampedEnd - clampedStart < 0.5) return null;
  if (clampedStart === segment.start && clampedEnd === segment.end) return null;
  return {
    type: "trim",
    index: segment.index,
    start: round3(clampedStart),
    end: round3(clampedEnd),
  };
}

/**
 * Build an edit request from the panel's draft state. Segment rows keep
 * their identity (original index); only changed rows become operations,
 * applied by the backend in the order returned here: trims by ascending
 * index, deletes by descending index (so indexes stay valid), then the
 * final reorder.
 */
export function buildEditRequest(
  original: EditorSegment[],
  rows: EditorSegment[],
  captions: CaptionOverrides | null,
): ClipEditRequest | null {
  const operations: SegmentOperation[] = [];
  const originalByIndex = new Map(original.map((s) => [s.index, s]));
  const surviving = new Set(rows.map((s) => s.index));

  for (const row of [...rows].sort((a, b) => a.index - b.index)) {
    const before = originalByIndex.get(row.index);
    if (!before) continue;
    if (row.start !== before.start || row.end !== before.end) {
      operations.push({
        type: "trim",
        index: row.index,
        start: row.start,
        end: row.end,
      });
    }
  }
  for (const segment of [...original].sort((a, b) => b.index - a.index)) {
    if (!surviving.has(segment.index)) {
      operations.push({ type: "delete", index: segment.index });
    }
  }
  const order = rows.map((s) => s.index);
  const sortedOrder = [...order].sort((a, b) => a - b);
  if (order.some((value, i) => value !== sortedOrder[i])) {
    operations.push({ type: "reorder", order });
  }

  const request: ClipEditRequest = {};
  if (operations.length > 0) request.operations = operations;
  if (captions && Object.keys(captions).length > 0) request.captions = captions;
  return Object.keys(request).length > 0 ? request : null;
}

/** Compare two caption override sets field-by-field. */
export function captionsChanged(
  current: CaptionOverrides | null,
  next: CaptionOverrides,
): boolean {
  const a = current ?? {};
  const keys = new Set([...Object.keys(a), ...Object.keys(next)]);
  for (const key of keys) {
    const aValue = (a as Record<string, unknown>)[key];
    const bValue = (next as Record<string, unknown>)[key];
    if (aValue !== bValue) return true;
  }
  return false;
}

/**
 * Queue a clip edit and poll until the worker finishes it. Reuses the
 * re-render poller: edit jobs share its outcome key space server-side.
 */
export const pollClipEdit = pollClipRerender;
