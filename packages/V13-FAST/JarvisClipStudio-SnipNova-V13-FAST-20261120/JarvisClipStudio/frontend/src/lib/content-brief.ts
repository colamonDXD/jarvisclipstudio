/**
 * Content brief (Content Requirements & Clip Targeting) — client-side types
 * and helpers mirroring the backend contract in
 * `backend/src/content_brief/`. The backend is the source of truth; these
 * mirrors exist so the create page can show instant feedback and build the
 * exact payload the create-task endpoint validates.
 */

export const REQUIREMENT_TYPES = [
  "must_mention",
  "topic_focus",
  "structure_hook",
  "structure_cta",
  "visual_presence",
  "caption_rule",
  "info_only",
] as const;

export type RequirementType = (typeof REQUIREMENT_TYPES)[number];

export type RequirementKind = "mandatory" | "optional";

/** One requirement as sent to the backend (ids are server-assigned). */
export interface BriefRequirementPayload {
  type: RequirementType;
  text: string;
  kind?: RequirementKind;
  phrase?: string;
  check_in?: string[];
  topics?: string[];
  min_topics?: number;
  requires_major?: boolean;
  within_seconds?: number;
  phrases?: string[];
  window?: string;
  subject?: string;
  hashtags?: string[];
  names?: string[];
}

/** One allowed range as sent to the backend (raw timecode strings/seconds). */
export interface BriefRangePayload {
  start: string | number;
  end: string | number | null;
}

/** Flag categories (backend FLAG_CATEGORIES): review labels stamped on
 *  clips containing the moment — never automatic edits. */
export const FLAG_CATEGORIES = [
  "licence_plate",
  "branding",
  "copyright_music",
  "sensitive_topic",
  "alcohol",
  "review",
  "other",
] as const;

export type FlagCategory = (typeof FLAG_CATEGORIES)[number];

/** One flag point as sent to the backend (a moment to flag for review). */
export interface BriefFlagPointPayload {
  at: string | number;
  category: FlagCategory;
  note?: string;
}

/** One key timestamp as sent to the backend (a moment the brief names). */
export interface BriefKeyTimestampPayload {
  at: string | number;
  description?: string;
}

export interface ContentBriefPayload {
  version: 2;
  requirements: BriefRequirementPayload[];
  allowed_ranges: BriefRangePayload[];
  excluded_ranges?: BriefRangePayload[];
  flag_points?: BriefFlagPointPayload[];
  key_timestamps?: BriefKeyTimestampPayload[];
  example_url?: string;
}

/** A stored brief as returned by the backend (ranges normalised). */
export interface StoredBriefRange {
  start_seconds: number;
  end_seconds: number | null;
  start_label: string;
  end_label: string | null;
}

export interface StoredBriefFlagPoint {
  at_seconds: number;
  at_label: string;
  category: string;
  note: string;
}

export interface StoredBriefKeyTimestamp {
  at_seconds: number;
  at_label: string;
  description: string;
}

export interface StoredBrief {
  version: number;
  requirements: Array<Record<string, unknown>>;
  allowed_ranges: StoredBriefRange[];
  excluded_ranges?: StoredBriefRange[];
  flag_points?: StoredBriefFlagPoint[];
  key_timestamps?: StoredBriefKeyTimestamp[];
  example_url?: string;
}

/* ------------------------------------------------------------------ */
/* Timecode mirror (backend: parse_timecode_input)                     */
/* ------------------------------------------------------------------ */

const PLAIN_NUMBER = /^\d+(?:\.\d+)?$/;
const COMPONENT = /^\d+$/;

/**
 * Parse a user-typed timecode exactly like the backend: plain seconds,
 * "m:ss" (leading minutes unbounded) or "h:mm:ss" (minutes/seconds < 60),
 * with a fractional part allowed only on the last component.
 * Returns seconds, or null when the input is not a valid timecode.
 */
export function parseTimecodeInput(value: string | number): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) && value >= 0 ? value : null;
  }
  const text = value.trim();
  if (!text) return null;
  if (PLAIN_NUMBER.test(text)) return Number(text);
  const parts = text.split(":");
  if (parts.length < 2 || parts.length > 3) return null;
  const last = parts[parts.length - 1];
  const fractionMatch = /^(\d+)(?:\.(\d+))?$/.exec(last);
  if (!fractionMatch) return null;
  const wholeParts = [...parts.slice(0, -1), fractionMatch[1]];
  if (!wholeParts.every((part) => COMPONENT.test(part))) return null;
  const numbers = wholeParts.map((part) => parseInt(part, 10));
  const fraction = fractionMatch[2] ? Number(`0.${fractionMatch[2]}`) : 0;
  if (parts.length === 2) {
    const [minutes, seconds] = numbers;
    if (seconds >= 60) return null;
    return minutes * 60 + seconds + fraction;
  }
  const [hours, minutes, seconds] = numbers;
  if (minutes >= 60 || seconds >= 60) return null;
  return hours * 3600 + minutes * 60 + seconds + fraction;
}

/** "2:56:29" / "03:00" style label (backend format_timecode: MM:SS
 *  zero-padded below an hour, H:MM:SS at/above it). */
export function formatTimecode(totalSeconds: number): string {
  const whole = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const seconds = whole % 60;
  const ss = String(seconds).padStart(2, "0");
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${ss}`;
  return `${String(minutes).padStart(2, "0")}:${ss}`;
}

/* ------------------------------------------------------------------ */
/* Major-focus mirror (backend: derive_requires_major)                 */
/* ------------------------------------------------------------------ */

const MAJOR_FOCUS_PATTERNS = [
  /major\s+focus/,
  /main\s+(focus|topic)/,
  /major\s+topic/,
  /clearly\s+mention/,
  /must\s+mention/,
  /primary\s+focus/,
  /central\s+(focus|topic|theme)/,
];

/** True when the requirement text itself demands major-focus treatment. */
export function requiresMajorFocus(text: string): boolean {
  const lowered = text.toLowerCase();
  return MAJOR_FOCUS_PATTERNS.some((pattern) => pattern.test(lowered));
}

/* ------------------------------------------------------------------ */
/* Panel draft state <-> payload                                       */
/* ------------------------------------------------------------------ */

export interface RequirementDraft {
  key: string;
  text: string;
  kind: RequirementKind;
  type: RequirementType;
  phrase: string;
  topics: string;
  /** null = let the wording decide (server derives it), true/false = explicit. */
  requiresMajor: boolean | null;
  withinSeconds: string;
  phrases: string;
  hashtags: string;
  names: string;
  subject: string;
}

export interface RangeDraft {
  key: string;
  start: string;
  end: string;
}

export interface FlagPointDraft {
  key: string;
  at: string;
  category: FlagCategory;
  note: string;
}

export interface KeyTimestampDraft {
  key: string;
  at: string;
  description: string;
}

export interface BriefPanelState {
  requirements: RequirementDraft[];
  ranges: RangeDraft[];
  excludedRanges: RangeDraft[];
  flagPoints: FlagPointDraft[];
  keyTimestamps: KeyTimestampDraft[];
  exampleUrl: string;
}

let draftCounter = 0;

export function emptyRequirementDraft(
  overrides: Partial<RequirementDraft> = {}
): RequirementDraft {
  draftCounter += 1;
  return {
    key: `req-${draftCounter}`,
    text: "",
    kind: "mandatory",
    type: "topic_focus",
    phrase: "",
    topics: "",
    requiresMajor: null,
    withinSeconds: "2",
    phrases: "",
    hashtags: "",
    names: "",
    subject: "",
    ...overrides,
  };
}

export function emptyRangeDraft(overrides: Partial<RangeDraft> = {}): RangeDraft {
  draftCounter += 1;
  return { key: `range-${draftCounter}`, start: "", end: "", ...overrides };
}

export function emptyFlagPointDraft(
  overrides: Partial<FlagPointDraft> = {}
): FlagPointDraft {
  draftCounter += 1;
  return {
    key: `flag-${draftCounter}`,
    at: "",
    category: "review",
    note: "",
    ...overrides,
  };
}

export function emptyKeyTimestampDraft(
  overrides: Partial<KeyTimestampDraft> = {}
): KeyTimestampDraft {
  draftCounter += 1;
  return { key: `keyts-${draftCounter}`, at: "", description: "", ...overrides };
}

export function emptyPanelState(): BriefPanelState {
  return {
    requirements: [],
    ranges: [],
    excludedRanges: [],
    flagPoints: [],
    keyTimestamps: [],
    exampleUrl: "",
  };
}

function splitList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** Whether a draft row carries anything the backend would enforce/record. */
export function requirementDraftHasContent(draft: RequirementDraft): boolean {
  if (draft.text.trim()) return true;
  switch (draft.type) {
    case "must_mention":
      return Boolean(draft.phrase.trim());
    case "topic_focus":
      return splitList(draft.topics).length > 0;
    case "structure_hook":
      return true; // a hook row is meaningful even without free text
    case "structure_cta":
      return splitList(draft.phrases).length > 0;
    case "visual_presence":
      return Boolean(draft.subject.trim());
    case "caption_rule":
      return (
        splitList(draft.hashtags).length > 0 ||
        splitList(draft.phrases).length > 0 ||
        splitList(draft.names).length > 0
      );
    case "info_only":
      return false;
  }
}

export function requirementDraftToPayload(
  draft: RequirementDraft
): BriefRequirementPayload | null {
  if (!requirementDraftHasContent(draft)) return null;
  const payload: BriefRequirementPayload = {
    type: draft.type,
    text: draft.text.trim(),
  };
  if (draft.type !== "info_only") payload.kind = draft.kind;
  switch (draft.type) {
    case "must_mention":
      if (draft.phrase.trim()) payload.phrase = draft.phrase.trim();
      break;
    case "topic_focus": {
      const topics = splitList(draft.topics);
      if (topics.length) payload.topics = topics;
      if (draft.requiresMajor !== null) payload.requires_major = draft.requiresMajor;
      break;
    }
    case "structure_hook": {
      const seconds = Number(draft.withinSeconds);
      payload.within_seconds = Number.isFinite(seconds) && seconds > 0 ? seconds : 2;
      break;
    }
    case "structure_cta": {
      const phrases = splitList(draft.phrases);
      if (phrases.length) payload.phrases = phrases;
      break;
    }
    case "visual_presence":
      if (draft.subject.trim()) payload.subject = draft.subject.trim();
      break;
    case "caption_rule": {
      const hashtags = splitList(draft.hashtags);
      const phrases = splitList(draft.phrases);
      const names = splitList(draft.names);
      if (hashtags.length) payload.hashtags = hashtags;
      if (phrases.length) payload.phrases = phrases;
      if (names.length) payload.names = names;
      break;
    }
    case "info_only":
      break;
  }
  return payload;
}

/**
 * Build the create-task `content_brief` payload from panel state.
 * Returns null when the panel is empty (the field is then omitted entirely,
 * keeping the no-brief create path byte-identical).
 */
function rangeDraftsToPayload(ranges: RangeDraft[]): BriefRangePayload[] {
  return ranges
    .filter((range) => range.start.trim())
    .map((range) => ({
      start: range.start.trim(),
      end: range.end.trim() ? range.end.trim() : null,
    }));
}

export function buildBriefPayload(
  state: BriefPanelState
): ContentBriefPayload | null {
  const requirements = state.requirements
    .map(requirementDraftToPayload)
    .filter((item): item is BriefRequirementPayload => item !== null);
  const allowedRanges = rangeDraftsToPayload(state.ranges);
  const excludedRanges = rangeDraftsToPayload(state.excludedRanges);
  const flagPoints: BriefFlagPointPayload[] = state.flagPoints
    .filter((point) => point.at.trim())
    .map((point) => {
      const payload: BriefFlagPointPayload = {
        at: point.at.trim(),
        category: point.category,
      };
      if (point.note.trim()) payload.note = point.note.trim();
      return payload;
    });
  const keyTimestamps: BriefKeyTimestampPayload[] = state.keyTimestamps
    .filter((point) => point.at.trim())
    .map((point) => {
      const payload: BriefKeyTimestampPayload = { at: point.at.trim() };
      if (point.description.trim()) {
        payload.description = point.description.trim();
      }
      return payload;
    });
  const exampleUrl = state.exampleUrl.trim();
  if (
    !requirements.length &&
    !allowedRanges.length &&
    !excludedRanges.length &&
    !flagPoints.length &&
    !keyTimestamps.length &&
    !exampleUrl
  ) {
    return null;
  }
  const payload: ContentBriefPayload = {
    version: 2,
    requirements,
    allowed_ranges: allowedRanges,
  };
  // The new field families ride along only when they carry content, so a
  // brief without them keeps its exact pre-existing payload shape.
  if (excludedRanges.length) payload.excluded_ranges = excludedRanges;
  if (flagPoints.length) payload.flag_points = flagPoints;
  if (keyTimestamps.length) payload.key_timestamps = keyTimestamps;
  if (exampleUrl) payload.example_url = exampleUrl;
  return payload;
}

/** Per-row range validation message key (null = valid). */
export function rangeDraftError(range: RangeDraft): string | null {
  if (!range.start.trim() && !range.end.trim()) return null;
  const start = parseTimecodeInput(range.start);
  if (start === null) return "invalidStart";
  if (range.end.trim()) {
    const end = parseTimecodeInput(range.end);
    if (end === null) return "invalidEnd";
    if (end <= start) return "endBeforeStart";
  }
  return null;
}

/** Per-row point validation message key (null = valid). */
export function pointDraftError(at: string): string | null {
  if (!at.trim()) return null;
  return parseTimecodeInput(at) === null ? "invalidTime" : null;
}

function storedRangesToDrafts(value: unknown): RangeDraft[] {
  const ranges: RangeDraft[] = [];
  const rawRanges = Array.isArray(value)
    ? (value as Array<Record<string, unknown>>)
    : [];
  for (const raw of rawRanges) {
    if (typeof raw.start_seconds === "number") {
      ranges.push(
        emptyRangeDraft({
          start:
            typeof raw.start_label === "string"
              ? raw.start_label
              : formatTimecode(raw.start_seconds),
          end:
            raw.end_seconds === null || raw.end_seconds === undefined
              ? ""
              : typeof raw.end_label === "string"
                ? raw.end_label
                : formatTimecode(raw.end_seconds as number),
        })
      );
    } else if (typeof raw.start === "string" || typeof raw.start === "number") {
      ranges.push(
        emptyRangeDraft({
          start: String(raw.start),
          end: raw.end === null || raw.end === undefined ? "" : String(raw.end),
        })
      );
    }
  }
  return ranges;
}

function storedPointLabel(raw: Record<string, unknown>): string {
  if (typeof raw.at_label === "string" && raw.at_label) return raw.at_label;
  if (typeof raw.at_seconds === "number") return formatTimecode(raw.at_seconds);
  if (typeof raw.at === "string" || typeof raw.at === "number") {
    return String(raw.at);
  }
  return "";
}

/**
 * Convert a stored/extracted brief (backend shape) into panel draft state.
 * Used by the campaign picker and the paste-a-brief review step.
 */
export function storedBriefToPanelState(
  brief: StoredBrief | Record<string, unknown> | null | undefined
): BriefPanelState {
  if (!brief || typeof brief !== "object") return emptyPanelState();
  const requirements: RequirementDraft[] = [];
  const rawRequirements = Array.isArray(brief.requirements)
    ? (brief.requirements as Array<Record<string, unknown>>)
    : [];
  for (const raw of rawRequirements) {
    const type = REQUIREMENT_TYPES.includes(raw.type as RequirementType)
      ? (raw.type as RequirementType)
      : "topic_focus";
    const joinList = (value: unknown) =>
      Array.isArray(value) ? value.map((item) => String(item)).join(", ") : "";
    requirements.push(
      emptyRequirementDraft({
        text: typeof raw.text === "string" ? raw.text : "",
        kind: raw.kind === "optional" ? "optional" : "mandatory",
        type,
        phrase: typeof raw.phrase === "string" ? raw.phrase : "",
        topics: joinList(raw.topics),
        requiresMajor:
          typeof raw.requires_major === "boolean" ? raw.requires_major : null,
        withinSeconds:
          typeof raw.within_seconds === "number" ? String(raw.within_seconds) : "2",
        phrases: joinList(raw.phrases),
        hashtags: joinList(raw.hashtags),
        names: joinList(raw.names),
        subject: typeof raw.subject === "string" ? raw.subject : "",
      })
    );
  }
  const flagPoints: FlagPointDraft[] = [];
  const rawFlags = Array.isArray(brief.flag_points)
    ? (brief.flag_points as Array<Record<string, unknown>>)
    : [];
  for (const raw of rawFlags) {
    flagPoints.push(
      emptyFlagPointDraft({
        at: storedPointLabel(raw),
        category: FLAG_CATEGORIES.includes(raw.category as FlagCategory)
          ? (raw.category as FlagCategory)
          : "other",
        note: typeof raw.note === "string" ? raw.note : "",
      })
    );
  }
  const keyTimestamps: KeyTimestampDraft[] = [];
  const rawKeys = Array.isArray(brief.key_timestamps)
    ? (brief.key_timestamps as Array<Record<string, unknown>>)
    : [];
  for (const raw of rawKeys) {
    keyTimestamps.push(
      emptyKeyTimestampDraft({
        at: storedPointLabel(raw),
        description: typeof raw.description === "string" ? raw.description : "",
      })
    );
  }
  return {
    requirements,
    ranges: storedRangesToDrafts(brief.allowed_ranges),
    excludedRanges: storedRangesToDrafts(brief.excluded_ranges),
    flagPoints,
    keyTimestamps,
    exampleUrl: typeof brief.example_url === "string" ? brief.example_url : "",
  };
}

/* ------------------------------------------------------------------ */
/* API client (via the Next.js proxy routes)                           */
/* ------------------------------------------------------------------ */

async function errorDetail(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data?.detail === "string" && data.detail) return data.detail;
  } catch {
    // fall through to the status-based message
  }
  return `Request failed: ${response.status}`;
}

export interface BriefExtractionResult {
  content_brief: StoredBrief;
  extraction: {
    source: "llm" | "deterministic";
    model: string | null;
    warnings: string[];
  };
}

/** POST /briefs/extract — drafts a brief from pasted campaign text. */
export async function extractBrief(briefText: string): Promise<BriefExtractionResult> {
  const response = await fetch("/api/briefs/extract", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brief_text: briefText }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as BriefExtractionResult;
}

export interface BriefImageExtractionResult extends BriefExtractionResult {
  /** The exact text OCR read from the screenshot, for user review. */
  brief_text: string;
  ocr: { characters: number };
}

/** POST /briefs/extract-image — OCR a brief screenshot locally on the
 *  server, then draft a brief from the recognised text (same pipeline
 *  as pasted text). The draft is never applied silently. */
export async function extractBriefImage(
  file: File
): Promise<BriefImageExtractionResult> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/briefs/extract-image", {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as BriefImageExtractionResult;
}

export interface CampaignBriefResponse {
  campaign_id: string;
  brief: StoredBrief | null;
  has_brief: boolean;
  example_url: string | null;
}

/** GET /publishing/campaigns/{id}/brief — a campaign's saved brief. */
export async function fetchCampaignBrief(
  campaignId: string
): Promise<CampaignBriefResponse> {
  const response = await fetch(
    `/api/publishing/campaigns/${encodeURIComponent(campaignId)}/brief`
  );
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as CampaignBriefResponse;
}

/** PATCH /publishing/campaigns/{id} — save a brief back onto a campaign. */
export async function saveCampaignBrief(
  campaignId: string,
  brief: ContentBriefPayload | null,
  // null is meaningful: it clears the campaign's saved example URL
  // (the body builder below maps it to example_url: null).
  exampleUrl?: string | null
): Promise<void> {
  const body: Record<string, unknown> = { brief };
  if (exampleUrl !== undefined) body.example_url = exampleUrl || null;
  const response = await fetch(
    `/api/publishing/campaigns/${encodeURIComponent(campaignId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
  if (!response.ok) throw new Error(await errorDetail(response));
}

/* ------------------------------------------------------------------ */
/* Compliance data (per-clip reports + task brief report)              */
/* ------------------------------------------------------------------ */

export type ComplianceStatus = "compliant" | "non_compliant" | "uncertain";

export interface ComplianceSummary {
  status: string;
  mandatory_failed: string[];
  mandatory_uncertain: string[];
  needs_review?: boolean;
  flag_points?: number;
  flag_categories?: string[];
}

export interface ComplianceFlagEntry {
  at_seconds: number;
  at_label?: string;
  category: string;
  note?: string;
}

export interface ComplianceEvidenceHit {
  start_seconds?: number;
  end_seconds?: number;
  excerpt?: string;
  match?: string;
}

export interface ComplianceRequirementEntry {
  id?: string;
  type: string;
  text?: string;
  kind?: string;
  status: string;
  focus?: string;
  requires_major?: boolean;
  phrase?: string;
  sources?: Record<string, string>;
  hit_sources?: string[];
  form?: string;
  window_seconds?: number;
  matched_by?: string;
  phrases?: string[] | Record<string, boolean>;
  hashtags?: Record<string, boolean>;
  names?: Record<string, boolean>;
  subject?: string;
  judgement_source?: string;
  decided_by?: string;
  detail?: string;
  evidence?: ComplianceEvidenceHit[];
  topics?: Array<{
    topic: string;
    focus?: string;
    status?: string;
    hits?: ComplianceEvidenceHit[];
  }>;
  within_seconds?: number;
  hook_status?: string | null;
  terms?: string[];
}

export interface ComplianceReport {
  version?: number;
  checked_at?: string;
  status: string;
  ranges?: {
    allowed: Array<[number, number | null]>;
    actual: Array<[number, number]>;
    violations: unknown[];
    excluded?: Array<[number, number]>;
    excluded_violations?: unknown[];
  };
  requirements: ComplianceRequirementEntry[];
  /** Flag points (licence plates, branding, ...) inside this clip: the
   *  clip is stamped needs-review for a human, never auto-edited. */
  needs_review?: boolean;
  flags?: ComplianceFlagEntry[];
  source_timestamps?: { start: string; end: string };
}

/** The clip's compliance_report arrives as raw TEXT (like qc_report);
 *  parse it defensively — a malformed report renders as "no report". */
export function parseComplianceReport(raw: unknown): ComplianceReport | null {
  let value: unknown = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== "object") return null;
  const report = value as ComplianceReport;
  if (typeof report.status !== "string") return null;
  if (!Array.isArray(report.requirements)) report.requirements = [];
  return report;
}

export interface BriefReport {
  version?: number;
  searched_ranges?: StoredBriefRange[];
  requirements?: Array<{
    id?: string;
    terms?: string[];
    term_source?: string;
    hit_lines?: number;
    candidates_examined?: number;
  }>;
  candidates_total?: number;
  candidates_in_range?: number;
  candidates_compliant?: number;
  candidates_dropped_range?: number;
  candidates_dropped_mandatory?: number;
  outcome?: string;
  llm_pass?: string;
}

export function parseBriefReport(raw: unknown): BriefReport | null {
  let value: unknown = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== "object") return null;
  return value as BriefReport;
}

/** Brief block the publishing board carries per clip (design §5.4). */
export interface BoardClipBrief {
  has_brief: boolean;
  status: string | null;
  mandatory_failed: string[];
  mandatory_uncertain: string[];
}
