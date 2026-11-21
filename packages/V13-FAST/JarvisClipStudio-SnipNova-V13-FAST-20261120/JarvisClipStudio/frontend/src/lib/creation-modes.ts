/**
 * The four creation modes (tracker 1.9), mirroring the backend
 * contract in backend/src/auto_director/creation_modes.py.
 *
 * These are workflows, not speed presets: each one changes who picks
 * the moments, whether the AI edits them, and whether generation
 * pauses for a review pick. The blurbs say exactly that — a mode whose
 * description promises a difference the pipeline does not make would
 * be a fake control.
 */
export const CREATION_MODE_OPTIONS = [
  {
    value: "full_auto",
    label: "Full AutoClip",
    blurb:
      "AI finds the best moments and edits them end to end. Nothing to review — clips render straight away.",
  },
  {
    value: "moments_manual",
    label: "Find Moments + Manual",
    blurb:
      "AI finds candidate moments, then stops. You pick which ones render, as plain cuts with your caption settings — no AI zooms, music or effects — and finish the edit yourself. Pauses and waits for you to pick the clips — if you don't pick within 15 minutes, it continues with the top picks automatically.",
  },
  {
    value: "moments_ai",
    label: "Find Moments + AI Editing",
    blurb:
      "AI finds candidate moments, then stops. You pick which ones render, and the AI applies the full edit (zooms, music, captions, effects) to just your picks. Pauses and waits for you to pick the clips — if you don't pick within 15 minutes, it continues with the top picks automatically.",
  },
  {
    value: "manual_cutter_ai",
    label: "Manual Cutter + AI Editing",
    blurb:
      "You give the exact time ranges to cut — the AI never hunts for moments. The AI applies the full edit to each of your ranges.",
  },
] as const;

export type CreationModeValue =
  (typeof CREATION_MODE_OPTIONS)[number]["value"];

export const MANUAL_CUTTER_MODE: CreationModeValue = "manual_cutter_ai";

export interface ManualRange {
  start: number;
  end: number;
}

export interface ParsedManualRanges {
  ranges: ManualRange[] | null;
  error: string | null;
}

function parseTimestamp(token: string): number | null {
  const trimmed = token.trim();
  if (!trimmed) return null;
  if (/^\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  const parts = trimmed.split(":");
  if (parts.length < 2 || parts.length > 3) return null;
  if (!parts.every((part) => /^\d+(\.\d+)?$/.test(part))) return null;
  return parts.reduce((total, part) => total * 60 + Number(part), 0);
}

/**
 * Parse the Manual Cutter textarea: one range per line, written as
 * "1:23-1:52", "01:23 - 01:52" or plain seconds "83-112". Client-side
 * validation mirrors the backend contract (which stays authoritative):
 * 1-20 ranges, each at least 1 second, no overlaps.
 */
export function parseManualRanges(text: string): ParsedManualRanges {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) {
    return { ranges: null, error: "Add at least one time range, one per line." };
  }
  if (lines.length > 20) {
    return { ranges: null, error: "At most 20 ranges per video." };
  }
  const ranges: ManualRange[] = [];
  for (const line of lines) {
    const match = line.match(/^(.+?)\s*[-–]\s*(.+)$/);
    const start = match ? parseTimestamp(match[1]) : null;
    const end = match ? parseTimestamp(match[2]) : null;
    if (start === null || end === null) {
      return {
        ranges: null,
        error: `Could not read "${line}". Use start-end per line, like 1:23-1:52.`,
      };
    }
    if (end - start < 1) {
      return {
        ranges: null,
        error: `The range "${line}" must be at least 1 second long.`,
      };
    }
    ranges.push({ start, end });
  }
  const sorted = [...ranges].sort((a, b) => a.start - b.start);
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i].start < sorted[i - 1].end) {
      return { ranges: null, error: "Ranges must not overlap." };
    }
  }
  return { ranges: sorted, error: null };
}
