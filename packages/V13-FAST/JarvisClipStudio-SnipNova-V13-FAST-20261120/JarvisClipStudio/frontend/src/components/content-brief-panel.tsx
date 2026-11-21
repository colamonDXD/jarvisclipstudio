"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { ChevronDown, ImagePlus, Loader2, Plus, X } from "lucide-react";

import {
  buildBriefPayload,
  emptyFlagPointDraft,
  emptyKeyTimestampDraft,
  emptyPanelState,
  emptyRangeDraft,
  emptyRequirementDraft,
  extractBrief,
  extractBriefImage,
  formatTimecode,
  parseTimecodeInput,
  pointDraftError,
  rangeDraftError,
  requiresMajorFocus,
  storedBriefToPanelState,
  FLAG_CATEGORIES,
  REQUIREMENT_TYPES,
  type BriefPanelState,
  type BriefRequirementPayload,
  type ContentBriefPayload,
  type FlagPointDraft,
  type KeyTimestampDraft,
  type RangeDraft,
  type RequirementDraft,
  type RequirementType,
} from "@/lib/content-brief";
import { cn } from "@/lib/utils";

export const MAX_REQUIREMENTS = 10;
export const MAX_RANGES = 8;
export const MAX_FLAG_POINTS = 40;
export const MAX_KEY_TIMESTAMPS = 20;

interface ContentBriefPanelProps {
  disabled?: boolean;
  /** Called with the exact `content_brief` payload (or null when empty)
   *  whenever the panel's enforceable content changes. */
  onChange: (payload: ContentBriefPayload | null) => void;
  /** A brief loaded from outside (campaign library). The panel adopts
   *  the state whenever `nonce` changes; the load expands the panel. */
  externalLoad?: { state: BriefPanelState; nonce: number } | null;
  /** Create-page context for the pre-generation summary. */
  summaryContext?: BriefSummaryContext;
}

export interface BriefSummaryContext {
  sourceLabel?: string | null;
  targetClipCount?: number | null;
  /** Clip-length picker value ("auto" | "short" | "medium" | "long"). */
  clipLength?: string;
  campaignName?: string | null;
  /** True when the campaign's example link will feed the reference
   *  style (campaign has one and the user picked no style of their own). */
  referenceFromCampaign?: boolean;
}

function requirementBar(
  t: ReturnType<typeof useTranslations<"home">>,
  requirement: BriefRequirementPayload
): string {
  switch (requirement.type) {
    case "topic_focus": {
      const major =
        requirement.requires_major ?? requiresMajorFocus(requirement.text);
      return major
        ? t("contentBrief.summary.barMajor")
        : t("contentBrief.summary.barMention");
    }
    case "must_mention":
      return requirement.phrase
        ? t("contentBrief.summary.barMentionPhrase", { phrase: requirement.phrase })
        : t("contentBrief.summary.barMention");
    case "caption_rule":
      return t("contentBrief.summary.barCaption");
    case "visual_presence":
      return t("contentBrief.summary.barVisual");
    case "structure_hook":
      return t("contentBrief.summary.barHook", {
        seconds: requirement.within_seconds ?? 2,
      });
    case "structure_cta":
      return t("contentBrief.summary.barCta");
    case "info_only":
      return t("contentBrief.summary.barInfo");
  }
}

function formatRangeLabel(
  t: ReturnType<typeof useTranslations<"home">>,
  range: { start: string | number; end: string | number | null }
): string {
  const format = (value: string | number) => {
    const seconds = parseTimecodeInput(value);
    return seconds === null ? String(value) : formatTimecode(seconds);
  };
  const end =
    range.end === null || range.end === undefined || range.end === ""
      ? t("contentBrief.summary.rangeOpenEnd")
      : format(range.end);
  return `${format(range.start)} – ${end}`;
}

/**
 * The pre-generation summary: exactly what will be enforced, derived
 * live from the same payload the create request will carry. Visible
 * whenever the panel has any content, even while collapsed.
 */
function BriefSummary({
  payload,
  context,
}: {
  payload: ContentBriefPayload;
  context?: BriefSummaryContext;
}) {
  const t = useTranslations("home");
  return (
    <div className="mt-4 rounded-xl border border-dashed p-3 text-xs">
      <p className="text-sm font-medium">{t("contentBrief.summary.title")}</p>
      <dl className="mt-2 space-y-1.5">
        {context?.sourceLabel && (
          <div className="flex gap-2">
            <dt className="shrink-0 text-muted-foreground">
              {t("contentBrief.summary.source")}:
            </dt>
            <dd className="min-w-0 truncate font-medium">{context.sourceLabel}</dd>
          </div>
        )}
        <div className="flex gap-2">
          <dt className="shrink-0 text-muted-foreground">
            {t("contentBrief.summary.ranges")}:
          </dt>
          <dd className="font-medium">
            {payload.allowed_ranges.length
              ? payload.allowed_ranges.map((range) => formatRangeLabel(t, range)).join(", ")
              : t("contentBrief.summary.fullVideo")}
          </dd>
        </div>
        {payload.excluded_ranges && payload.excluded_ranges.length > 0 && (
          <div className="flex gap-2">
            <dt className="shrink-0 text-muted-foreground">
              {t("contentBrief.summary.excluded")}:
            </dt>
            <dd className="font-medium">
              {payload.excluded_ranges
                .map((range) => formatRangeLabel(t, range))
                .join(", ")}
            </dd>
          </div>
        )}
        {payload.key_timestamps && payload.key_timestamps.length > 0 && (
          <div className="flex gap-2">
            <dt className="shrink-0 text-muted-foreground">
              {t("contentBrief.summary.keys")}:
            </dt>
            <dd className="font-medium">
              {payload.key_timestamps
                .map((point) => {
                  const seconds = parseTimecodeInput(point.at);
                  const label =
                    seconds === null ? String(point.at) : formatTimecode(seconds);
                  return point.description
                    ? `${label} — ${point.description}`
                    : label;
                })
                .join(", ")}
            </dd>
          </div>
        )}
        {payload.flag_points && payload.flag_points.length > 0 && (
          <div className="flex gap-2">
            <dt className="shrink-0 text-muted-foreground">
              {t("contentBrief.summary.flags")}:
            </dt>
            <dd className="font-medium">
              {t("contentBrief.summary.flagsValue", {
                count: payload.flag_points.length,
              })}{" "}
              (
              {[...new Set(payload.flag_points.map((point) => point.category))]
                .map((category) => t(`contentBrief.flagCategories.${category}`))
                .join(", ")}
              )
            </dd>
          </div>
        )}
        {(context?.targetClipCount !== undefined || context?.clipLength) && (
          <div className="flex gap-2">
            <dt className="shrink-0 text-muted-foreground">
              {context.targetClipCount
                ? t("contentBrief.summary.clipsCount", { count: context.targetClipCount })
                : t("contentBrief.summary.clipsAuto")}
            </dt>
            {context.clipLength && (
              <dd className="font-medium">
                {t("contentBrief.summary.length", {
                  label: t(`clipLength.${context.clipLength}` as "clipLength.auto"),
                })}
              </dd>
            )}
          </div>
        )}
        {context?.campaignName && (
          <div className="flex gap-2">
            <dt className="font-medium">
              {t("contentBrief.summary.campaign", { name: context.campaignName })}
            </dt>
          </div>
        )}
        {context?.referenceFromCampaign && (
          <div className="flex gap-2">
            <dt className="font-medium">
              {t("contentBrief.summary.referenceFromCampaign")}
            </dt>
          </div>
        )}
      </dl>
      {payload.requirements.length > 0 && (
        <ul className="mt-2 space-y-1">
          {payload.requirements.map((requirement, index) => (
            <li key={`${requirement.type}-${index}`} className="flex flex-wrap items-baseline gap-x-2">
              <span
                className={
                  requirement.type === "info_only"
                    ? "rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground"
                    : requirement.kind === "mandatory"
                      ? "rounded-full bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold text-red-700 dark:bg-red-950 dark:text-red-300"
                      : "rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground"
                }
              >
                {requirement.type === "info_only"
                  ? t("contentBrief.summary.notCheckedBadge")
                  : requirement.kind === "mandatory"
                    ? t("contentBrief.summary.mandatoryBadge")
                    : t("contentBrief.summary.optionalBadge")}
              </span>
              <span className="font-medium">{requirement.text}</span>
              <span className="text-muted-foreground">
                — {requirementBar(t, requirement)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 font-medium">
        {payload.allowed_ranges.length
          ? t("contentBrief.summary.enforcement")
          : t("contentBrief.summary.enforcementNoRanges")}
      </p>
      {payload.excluded_ranges && payload.excluded_ranges.length > 0 && (
        <p className="mt-1 font-medium">
          {t("contentBrief.summary.enforcementExcluded")}
        </p>
      )}
    </div>
  );
}

const inputClass =
  "h-9 w-full rounded-lg border bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground/80 disabled:opacity-50";
const smallInputClass =
  "h-8 w-full rounded-lg border bg-transparent px-2.5 text-xs outline-none placeholder:text-muted-foreground/80 disabled:opacity-50";
const labelClass = "text-xs font-medium";

function RequirementRow({
  draft,
  disabled,
  onUpdate,
  onRemove,
}: {
  draft: RequirementDraft;
  disabled?: boolean;
  onUpdate: (patch: Partial<RequirementDraft>) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("home");
  const textTooLong = draft.text.length > 500;

  return (
    <div className="rounded-xl border p-3">
      <div className="flex items-start gap-2">
        <input
          type="text"
          value={draft.text}
          onChange={(event) => onUpdate({ text: event.target.value })}
          placeholder={t("contentBrief.requirementPlaceholder")}
          disabled={disabled}
          aria-label={t("contentBrief.requirementTextLabel")}
          className={cn(inputClass, "min-w-0 flex-1")}
        />
        {draft.type !== "info_only" && (
          <div
            role="group"
            aria-label={t("contentBrief.kindLabel")}
            className="flex shrink-0 overflow-hidden rounded-full border text-xs font-medium"
          >
            {(["mandatory", "optional"] as const).map((kind) => (
              <button
                key={kind}
                type="button"
                disabled={disabled}
                aria-pressed={draft.kind === kind}
                onClick={() => onUpdate({ kind })}
                className={cn(
                  "h-9 px-3 transition-colors disabled:opacity-50",
                  draft.kind === kind
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:bg-accent"
                )}
              >
                {t(`contentBrief.${kind}`)}
              </button>
            ))}
          </div>
        )}
        <button
          type="button"
          aria-label={t("contentBrief.removeRequirement")}
          onClick={onRemove}
          disabled={disabled}
          className="inline-flex size-9 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="mt-2 flex items-center gap-2">
        <label className={labelClass} htmlFor={`brief-type-${draft.key}`}>
          {t("contentBrief.typeLabel")}
        </label>
        <select
          id={`brief-type-${draft.key}`}
          value={draft.type}
          disabled={disabled}
          onChange={(event) =>
            onUpdate({ type: event.target.value as RequirementType })
          }
          className="h-8 rounded-lg border bg-transparent px-2 text-xs outline-none disabled:opacity-50"
        >
          {REQUIREMENT_TYPES.map((type) => (
            <option key={type} value={type}>
              {t(`contentBrief.types.${type}`)}
            </option>
          ))}
        </select>
      </div>

      {draft.type === "must_mention" && (
        <div className="mt-2">
          <label className={labelClass} htmlFor={`brief-phrase-${draft.key}`}>
            {t("contentBrief.phraseLabel")}
          </label>
          <input
            id={`brief-phrase-${draft.key}`}
            type="text"
            value={draft.phrase}
            onChange={(event) => onUpdate({ phrase: event.target.value })}
            placeholder={t("contentBrief.phrasePlaceholder")}
            disabled={disabled}
            className={cn(smallInputClass, "mt-1")}
          />
        </div>
      )}

      {draft.type === "topic_focus" && (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <div>
            <label className={labelClass} htmlFor={`brief-topics-${draft.key}`}>
              {t("contentBrief.topicsLabel")}
            </label>
            <input
              id={`brief-topics-${draft.key}`}
              type="text"
              value={draft.topics}
              onChange={(event) => onUpdate({ topics: event.target.value })}
              placeholder={t("contentBrief.topicsPlaceholder")}
              disabled={disabled}
              className={cn(smallInputClass, "mt-1")}
            />
          </div>
          <div>
            <label className={labelClass} htmlFor={`brief-major-${draft.key}`}>
              {t("contentBrief.majorLabel")}
            </label>
            <select
              id={`brief-major-${draft.key}`}
              value={
                draft.requiresMajor === null
                  ? "auto"
                  : draft.requiresMajor
                    ? "major"
                    : "mention"
              }
              disabled={disabled}
              onChange={(event) =>
                onUpdate({
                  requiresMajor:
                    event.target.value === "auto"
                      ? null
                      : event.target.value === "major",
                })
              }
              className="mt-1 h-8 w-full rounded-lg border bg-transparent px-2 text-xs outline-none disabled:opacity-50"
            >
              <option value="auto">{t("contentBrief.majorAuto")}</option>
              <option value="major">{t("contentBrief.majorYes")}</option>
              <option value="mention">{t("contentBrief.majorNo")}</option>
            </select>
          </div>
        </div>
      )}

      {draft.type === "structure_hook" && (
        <div className="mt-2">
          <label className={labelClass} htmlFor={`brief-hook-${draft.key}`}>
            {t("contentBrief.withinSecondsLabel")}
          </label>
          <input
            id={`brief-hook-${draft.key}`}
            type="number"
            min="0.5"
            step="0.5"
            value={draft.withinSeconds}
            onChange={(event) => onUpdate({ withinSeconds: event.target.value })}
            disabled={disabled}
            className={cn(smallInputClass, "mt-1 w-28")}
          />
        </div>
      )}

      {draft.type === "structure_cta" && (
        <div className="mt-2">
          <label className={labelClass} htmlFor={`brief-cta-${draft.key}`}>
            {t("contentBrief.ctaPhrasesLabel")}
          </label>
          <input
            id={`brief-cta-${draft.key}`}
            type="text"
            value={draft.phrases}
            onChange={(event) => onUpdate({ phrases: event.target.value })}
            placeholder={t("contentBrief.ctaPhrasesPlaceholder")}
            disabled={disabled}
            className={cn(smallInputClass, "mt-1")}
          />
        </div>
      )}

      {draft.type === "visual_presence" && (
        <div className="mt-2">
          <label className={labelClass} htmlFor={`brief-subject-${draft.key}`}>
            {t("contentBrief.subjectLabel")}
          </label>
          <input
            id={`brief-subject-${draft.key}`}
            type="text"
            value={draft.subject}
            onChange={(event) => onUpdate({ subject: event.target.value })}
            placeholder={t("contentBrief.subjectPlaceholder")}
            disabled={disabled}
            className={cn(smallInputClass, "mt-1")}
          />
        </div>
      )}

      {draft.type === "caption_rule" && (
        <div className="mt-2 grid gap-2 sm:grid-cols-3">
          <div>
            <label className={labelClass} htmlFor={`brief-hashtags-${draft.key}`}>
              {t("contentBrief.hashtagsLabel")}
            </label>
            <input
              id={`brief-hashtags-${draft.key}`}
              type="text"
              value={draft.hashtags}
              onChange={(event) => onUpdate({ hashtags: event.target.value })}
              placeholder={t("contentBrief.hashtagsPlaceholder")}
              disabled={disabled}
              className={cn(smallInputClass, "mt-1")}
            />
          </div>
          <div>
            <label className={labelClass} htmlFor={`brief-capphrases-${draft.key}`}>
              {t("contentBrief.captionPhrasesLabel")}
            </label>
            <input
              id={`brief-capphrases-${draft.key}`}
              type="text"
              value={draft.phrases}
              onChange={(event) => onUpdate({ phrases: event.target.value })}
              disabled={disabled}
              className={cn(smallInputClass, "mt-1")}
            />
          </div>
          <div>
            <label className={labelClass} htmlFor={`brief-names-${draft.key}`}>
              {t("contentBrief.namesLabel")}
            </label>
            <input
              id={`brief-names-${draft.key}`}
              type="text"
              value={draft.names}
              onChange={(event) => onUpdate({ names: event.target.value })}
              placeholder={t("contentBrief.namesPlaceholder")}
              disabled={disabled}
              className={cn(smallInputClass, "mt-1")}
            />
          </div>
        </div>
      )}

      {draft.type === "info_only" && (
        <p className="mt-2 text-xs text-muted-foreground">
          {t("contentBrief.infoOnlyNote")}
        </p>
      )}

      {textTooLong && (
        <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400">
          {t("contentBrief.textTooLong")}
        </p>
      )}
    </div>
  );
}

function RangeRow({
  draft,
  disabled,
  onUpdate,
  onRemove,
}: {
  draft: RangeDraft;
  disabled?: boolean;
  onUpdate: (patch: Partial<RangeDraft>) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("home");
  const error = rangeDraftError(draft);
  return (
    <div>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={draft.start}
          onChange={(event) => onUpdate({ start: event.target.value })}
          placeholder="0:00"
          disabled={disabled}
          aria-label={t("contentBrief.rangeStartLabel")}
          className={cn(smallInputClass, "w-28")}
        />
        <span className="text-xs text-muted-foreground">–</span>
        <input
          type="text"
          value={draft.end}
          onChange={(event) => onUpdate({ end: event.target.value })}
          placeholder={t("contentBrief.rangeEndPlaceholder")}
          disabled={disabled}
          aria-label={t("contentBrief.rangeEndLabel")}
          className={cn(smallInputClass, "w-28")}
        />
        <button
          type="button"
          aria-label={t("contentBrief.removeRange")}
          onClick={onRemove}
          disabled={disabled}
          className="inline-flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
        >
          <X className="size-4" />
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-xs text-red-600 dark:text-red-400">
          {t(`contentBrief.rangeErrors.${error}`)}
        </p>
      )}
    </div>
  );
}

function FlagPointRow({
  draft,
  disabled,
  onUpdate,
  onRemove,
}: {
  draft: FlagPointDraft;
  disabled?: boolean;
  onUpdate: (patch: Partial<FlagPointDraft>) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("home");
  const error = pointDraftError(draft.at);
  return (
    <div>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={draft.at}
          onChange={(event) => onUpdate({ at: event.target.value })}
          placeholder="0:00"
          disabled={disabled}
          aria-label={t("contentBrief.flagAtLabel")}
          className={cn(smallInputClass, "w-24 shrink-0")}
        />
        <select
          value={draft.category}
          disabled={disabled}
          onChange={(event) =>
            onUpdate({ category: event.target.value as FlagPointDraft["category"] })
          }
          aria-label={t("contentBrief.flagCategoryLabel")}
          className="h-8 shrink-0 rounded-lg border bg-transparent px-2 text-xs outline-none disabled:opacity-50"
        >
          {FLAG_CATEGORIES.map((category) => (
            <option key={category} value={category}>
              {t(`contentBrief.flagCategories.${category}`)}
            </option>
          ))}
        </select>
        <input
          type="text"
          value={draft.note}
          onChange={(event) => onUpdate({ note: event.target.value })}
          placeholder={t("contentBrief.flagNotePlaceholder")}
          disabled={disabled}
          aria-label={t("contentBrief.flagNoteLabel")}
          className={cn(smallInputClass, "min-w-0 flex-1")}
        />
        <button
          type="button"
          aria-label={t("contentBrief.removeFlag")}
          onClick={onRemove}
          disabled={disabled}
          className="inline-flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
        >
          <X className="size-4" />
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-xs text-red-600 dark:text-red-400">
          {t(`contentBrief.pointErrors.${error}`)}
        </p>
      )}
    </div>
  );
}

function KeyTimestampRow({
  draft,
  disabled,
  onUpdate,
  onRemove,
}: {
  draft: KeyTimestampDraft;
  disabled?: boolean;
  onUpdate: (patch: Partial<KeyTimestampDraft>) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("home");
  const error = pointDraftError(draft.at);
  return (
    <div>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={draft.at}
          onChange={(event) => onUpdate({ at: event.target.value })}
          placeholder="0:00"
          disabled={disabled}
          aria-label={t("contentBrief.keyAtLabel")}
          className={cn(smallInputClass, "w-24 shrink-0")}
        />
        <input
          type="text"
          value={draft.description}
          onChange={(event) => onUpdate({ description: event.target.value })}
          placeholder={t("contentBrief.keyDescriptionPlaceholder")}
          disabled={disabled}
          aria-label={t("contentBrief.keyDescriptionLabel")}
          className={cn(smallInputClass, "min-w-0 flex-1")}
        />
        <button
          type="button"
          aria-label={t("contentBrief.removeKey")}
          onClick={onRemove}
          disabled={disabled}
          className="inline-flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
        >
          <X className="size-4" />
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-xs text-red-600 dark:text-red-400">
          {t(`contentBrief.pointErrors.${error}`)}
        </p>
      )}
    </div>
  );
}

/**
 * The create page's Campaign brief panel (Content Requirements & Clip
 * Targeting). Collapsed by default; everything in it is optional and the
 * panel reports the exact payload it built via `onChange` — an empty
 * panel reports null so the create request stays byte-identical to the
 * no-brief path.
 */
interface PendingDraft {
  state: BriefPanelState;
  source: "llm" | "deterministic";
  warnings: string[];
  /** Set when the draft came from a screenshot: OCR character count. */
  ocrCharacters?: number;
}

export function ContentBriefPanel({
  disabled,
  onChange,
  externalLoad,
  summaryContext,
}: ContentBriefPanelProps) {
  const t = useTranslations("home");
  const [expanded, setExpanded] = useState(false);
  const [state, setState] = useState<BriefPanelState>(() => emptyPanelState());
  const lastLoadNonce = useRef<number>(0);

  useEffect(() => {
    if (externalLoad && externalLoad.nonce !== lastLoadNonce.current) {
      lastLoadNonce.current = externalLoad.nonce;
      setState(externalLoad.state);
      setPendingDraft(null);
      setExpanded(true);
    }
  }, [externalLoad]);
  const [pasteText, setPasteText] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [readingImage, setReadingImage] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [pendingDraft, setPendingDraft] = useState<PendingDraft | null>(null);
  const lastReported = useRef<string>("null");

  const handleExtract = async () => {
    setExtracting(true);
    setExtractError(null);
    setPendingDraft(null);
    try {
      const result = await extractBrief(pasteText.trim());
      setPendingDraft({
        state: storedBriefToPanelState(result.content_brief),
        source: result.extraction.source,
        warnings: result.extraction.warnings ?? [],
      });
    } catch (err) {
      setExtractError(
        err instanceof Error && err.message
          ? err.message
          : t("contentBrief.extractFailed")
      );
    } finally {
      setExtracting(false);
    }
  };

  const handleImageUpload = async (file: File) => {
    setReadingImage(true);
    setExtractError(null);
    setPendingDraft(null);
    try {
      const result = await extractBriefImage(file);
      // The recognised text lands in the paste box so the user reviews
      // exactly what the machine read; the draft itself still needs
      // the explicit "Use this draft" confirmation.
      setPasteText(result.brief_text);
      setPendingDraft({
        state: storedBriefToPanelState(result.content_brief),
        source: result.extraction.source,
        warnings: result.extraction.warnings ?? [],
        ocrCharacters: result.ocr?.characters,
      });
    } catch (err) {
      setExtractError(
        err instanceof Error && err.message
          ? err.message
          : t("contentBrief.extractFailed")
      );
    } finally {
      setReadingImage(false);
    }
  };

  const applyDraft = () => {
    if (!pendingDraft) return;
    setState(pendingDraft.state);
    setPendingDraft(null);
  };

  useEffect(() => {
    const payload = buildBriefPayload(state);
    const serialised = JSON.stringify(payload);
    if (serialised !== lastReported.current) {
      lastReported.current = serialised;
      onChange(payload);
    }
  }, [state, onChange]);

  const updateRequirement = (key: string, patch: Partial<RequirementDraft>) =>
    setState((current) => ({
      ...current,
      requirements: current.requirements.map((draft) =>
        draft.key === key ? { ...draft, ...patch } : draft
      ),
    }));

  const updateRange = (key: string, patch: Partial<RangeDraft>) =>
    setState((current) => ({
      ...current,
      ranges: current.ranges.map((draft) =>
        draft.key === key ? { ...draft, ...patch } : draft
      ),
    }));

  const updateExcludedRange = (key: string, patch: Partial<RangeDraft>) =>
    setState((current) => ({
      ...current,
      excludedRanges: current.excludedRanges.map((draft) =>
        draft.key === key ? { ...draft, ...patch } : draft
      ),
    }));

  const updateFlagPoint = (key: string, patch: Partial<FlagPointDraft>) =>
    setState((current) => ({
      ...current,
      flagPoints: current.flagPoints.map((draft) =>
        draft.key === key ? { ...draft, ...patch } : draft
      ),
    }));

  const updateKeyTimestamp = (key: string, patch: Partial<KeyTimestampDraft>) =>
    setState((current) => ({
      ...current,
      keyTimestamps: current.keyTimestamps.map((draft) =>
        draft.key === key ? { ...draft, ...patch } : draft
      ),
    }));

  return (
    <section
      aria-label={t("contentBrief.title")}
      className="rounded-2xl border bg-background p-4 shadow-[0_1px_2px_rgb(0_0_0/0.04)]"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-medium">
            {t("contentBrief.title")}{" "}
            <span className="font-normal text-muted-foreground">
              — {t("contentBrief.optionalSuffix")}
            </span>
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("contentBrief.body")}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          disabled={disabled}
          className="inline-flex h-8 shrink-0 items-center gap-1 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
        >
          {expanded ? t("contentBrief.collapse") : t("contentBrief.expand")}
          <ChevronDown
            className={cn("size-3.5 transition-transform", expanded && "rotate-180")}
          />
        </button>
      </div>

      {expanded && (
        <div className="mt-4 space-y-5">
          <div>
            <h3 className="text-xs font-medium">{t("contentBrief.pasteTitle")}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t("contentBrief.pasteBody")}
            </p>
            <textarea
              value={pasteText}
              onChange={(event) => setPasteText(event.target.value)}
              placeholder={t("contentBrief.pastePlaceholder")}
              disabled={disabled || extracting}
              aria-label={t("contentBrief.pasteTitle")}
              rows={4}
              className="mt-2 w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/80 disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => void handleExtract()}
              disabled={disabled || extracting || !pasteText.trim()}
              className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full bg-foreground px-3.5 text-xs font-medium text-background transition-opacity hover:opacity-90 disabled:pointer-events-none disabled:opacity-40"
            >
              {extracting && <Loader2 className="size-3.5 animate-spin" />}
              {extracting ? t("contentBrief.extracting") : t("contentBrief.extract")}
            </button>
            <div className="mt-3 border-t border-dashed pt-3">
              <h4 className="text-xs font-medium">{t("contentBrief.imageTitle")}</h4>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t("contentBrief.imageBody")}
              </p>
              <label
                className={cn(
                  "mt-2 inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent",
                  (disabled || readingImage) && "pointer-events-none opacity-50"
                )}
              >
                {readingImage ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <ImagePlus className="size-3.5" />
                )}
                {readingImage
                  ? t("contentBrief.imageReading")
                  : t("contentBrief.imageButton")}
                <input
                  type="file"
                  accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
                  className="sr-only"
                  aria-label={t("contentBrief.imageTitle")}
                  disabled={disabled || readingImage}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    if (file) void handleImageUpload(file);
                  }}
                />
              </label>
            </div>
            {extractError && (
              <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400">
                {extractError}
              </p>
            )}
            {pendingDraft && (
              <div className="mt-3 rounded-xl bg-muted/60 p-3 text-xs">
                <p className="font-medium">
                  {pendingDraft.source === "llm"
                    ? t("contentBrief.extractedByModel")
                    : t("contentBrief.extractedBasic")}
                </p>
                <p className="mt-0.5 text-muted-foreground">
                  {t("contentBrief.draftCounts", {
                    requirements: pendingDraft.state.requirements.length,
                    ranges: pendingDraft.state.ranges.length,
                  })}
                </p>
                {(pendingDraft.state.excludedRanges.length > 0 ||
                  pendingDraft.state.flagPoints.length > 0 ||
                  pendingDraft.state.keyTimestamps.length > 0) && (
                  <p className="mt-0.5 text-muted-foreground">
                    {t("contentBrief.draftCountsExtra", {
                      excluded: pendingDraft.state.excludedRanges.length,
                      flags: pendingDraft.state.flagPoints.length,
                      keys: pendingDraft.state.keyTimestamps.length,
                    })}
                  </p>
                )}
                {pendingDraft.ocrCharacters !== undefined && (
                  <p className="mt-0.5 text-muted-foreground">
                    {t("contentBrief.imageDraftNote", {
                      characters: pendingDraft.ocrCharacters,
                    })}
                  </p>
                )}
                {pendingDraft.warnings.map((warning) => (
                  <p key={warning} className="mt-0.5 text-muted-foreground">
                    {warning}
                  </p>
                ))}
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    onClick={applyDraft}
                    disabled={disabled}
                    className="inline-flex h-8 items-center rounded-full bg-foreground px-3.5 text-xs font-medium text-background transition-opacity hover:opacity-90 disabled:pointer-events-none disabled:opacity-40"
                  >
                    {t("contentBrief.useDraft")}
                  </button>
                  <button
                    type="button"
                    onClick={() => setPendingDraft(null)}
                    disabled={disabled}
                    className="inline-flex h-8 items-center rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
                  >
                    {t("contentBrief.discardDraft")}
                  </button>
                </div>
              </div>
            )}
          </div>

          <div>
            <h3 className="text-xs font-medium">{t("contentBrief.requirementsTitle")}</h3>
            <div className="mt-2 space-y-2">
              {state.requirements.map((draft) => (
                <RequirementRow
                  key={draft.key}
                  draft={draft}
                  disabled={disabled}
                  onUpdate={(patch) => updateRequirement(draft.key, patch)}
                  onRemove={() =>
                    setState((current) => ({
                      ...current,
                      requirements: current.requirements.filter(
                        (item) => item.key !== draft.key
                      ),
                    }))
                  }
                />
              ))}
            </div>
            <button
              type="button"
              onClick={() =>
                setState((current) => ({
                  ...current,
                  requirements: [...current.requirements, emptyRequirementDraft()],
                }))
              }
              disabled={disabled || state.requirements.length >= MAX_REQUIREMENTS}
              className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
            >
              <Plus className="size-3.5" />
              {t("contentBrief.addRequirement")}
            </button>
            {state.requirements.length >= MAX_REQUIREMENTS && (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("contentBrief.maxRequirements")}
              </p>
            )}
          </div>

          <div>
            <h3 className="text-xs font-medium">{t("contentBrief.rangesTitle")}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t("contentBrief.rangesBody")}
            </p>
            <div className="mt-2 space-y-2">
              {state.ranges.map((draft) => (
                <RangeRow
                  key={draft.key}
                  draft={draft}
                  disabled={disabled}
                  onUpdate={(patch) => updateRange(draft.key, patch)}
                  onRemove={() =>
                    setState((current) => ({
                      ...current,
                      ranges: current.ranges.filter((item) => item.key !== draft.key),
                    }))
                  }
                />
              ))}
            </div>
            <button
              type="button"
              onClick={() =>
                setState((current) => ({
                  ...current,
                  ranges: [...current.ranges, emptyRangeDraft()],
                }))
              }
              disabled={disabled || state.ranges.length >= MAX_RANGES}
              className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
            >
              <Plus className="size-3.5" />
              {t("contentBrief.addRange")}
            </button>
            {state.ranges.length >= MAX_RANGES && (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("contentBrief.maxRanges")}
              </p>
            )}
          </div>

          <div>
            <h3 className="text-xs font-medium">{t("contentBrief.excludedTitle")}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t("contentBrief.excludedBody")}
            </p>
            <div className="mt-2 space-y-2">
              {state.excludedRanges.map((draft) => (
                <RangeRow
                  key={draft.key}
                  draft={draft}
                  disabled={disabled}
                  onUpdate={(patch) => updateExcludedRange(draft.key, patch)}
                  onRemove={() =>
                    setState((current) => ({
                      ...current,
                      excludedRanges: current.excludedRanges.filter(
                        (item) => item.key !== draft.key
                      ),
                    }))
                  }
                />
              ))}
            </div>
            <button
              type="button"
              onClick={() =>
                setState((current) => ({
                  ...current,
                  excludedRanges: [...current.excludedRanges, emptyRangeDraft()],
                }))
              }
              disabled={disabled || state.excludedRanges.length >= MAX_RANGES}
              className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
            >
              <Plus className="size-3.5" />
              {t("contentBrief.addRange")}
            </button>
            {state.excludedRanges.length >= MAX_RANGES && (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("contentBrief.maxRanges")}
              </p>
            )}
          </div>

          <div>
            <h3 className="text-xs font-medium">{t("contentBrief.keysTitle")}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t("contentBrief.keysBody")}
            </p>
            <div className="mt-2 space-y-2">
              {state.keyTimestamps.map((draft) => (
                <KeyTimestampRow
                  key={draft.key}
                  draft={draft}
                  disabled={disabled}
                  onUpdate={(patch) => updateKeyTimestamp(draft.key, patch)}
                  onRemove={() =>
                    setState((current) => ({
                      ...current,
                      keyTimestamps: current.keyTimestamps.filter(
                        (item) => item.key !== draft.key
                      ),
                    }))
                  }
                />
              ))}
            </div>
            <button
              type="button"
              onClick={() =>
                setState((current) => ({
                  ...current,
                  keyTimestamps: [...current.keyTimestamps, emptyKeyTimestampDraft()],
                }))
              }
              disabled={disabled || state.keyTimestamps.length >= MAX_KEY_TIMESTAMPS}
              className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
            >
              <Plus className="size-3.5" />
              {t("contentBrief.addKey")}
            </button>
            {state.keyTimestamps.length >= MAX_KEY_TIMESTAMPS && (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("contentBrief.maxKeys")}
              </p>
            )}
          </div>

          <div>
            <h3 className="text-xs font-medium">{t("contentBrief.flagsTitle")}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t("contentBrief.flagsBody")}
            </p>
            <div className="mt-2 space-y-2">
              {state.flagPoints.map((draft) => (
                <FlagPointRow
                  key={draft.key}
                  draft={draft}
                  disabled={disabled}
                  onUpdate={(patch) => updateFlagPoint(draft.key, patch)}
                  onRemove={() =>
                    setState((current) => ({
                      ...current,
                      flagPoints: current.flagPoints.filter(
                        (item) => item.key !== draft.key
                      ),
                    }))
                  }
                />
              ))}
            </div>
            <button
              type="button"
              onClick={() =>
                setState((current) => ({
                  ...current,
                  flagPoints: [...current.flagPoints, emptyFlagPointDraft()],
                }))
              }
              disabled={disabled || state.flagPoints.length >= MAX_FLAG_POINTS}
              className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
            >
              <Plus className="size-3.5" />
              {t("contentBrief.addFlag")}
            </button>
            {state.flagPoints.length >= MAX_FLAG_POINTS && (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("contentBrief.maxFlags")}
              </p>
            )}
          </div>
        </div>
      )}

      {(() => {
        const payload = buildBriefPayload(state);
        return payload ? (
          <BriefSummary payload={payload} context={summaryContext} />
        ) : null;
      })()}
    </section>
  );
}
