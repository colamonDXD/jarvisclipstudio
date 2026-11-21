"use client";

import { useTranslations } from "next-intl";
import {
  Check,
  Flag,
  HelpCircle,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  X,
} from "lucide-react";

import {
  formatTimecode,
  type BriefReport,
  type ComplianceReport,
  type ComplianceRequirementEntry,
  type StoredBrief,
} from "@/lib/content-brief";
import { cn } from "@/lib/utils";

/**
 * Campaign-brief compliance UI for the task page (Content Requirements):
 * the tile shield badge, the per-clip report section shown in ClipFocus,
 * the no-compliant-moments panel, and the partial-drop banner. All of it
 * renders recorded backend facts — nothing is re-judged in the browser.
 */

export function BriefBadge({ status }: { status: string | null | undefined }) {
  const t = useTranslations("task");
  if (status === "compliant") {
    return (
      <p className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
        <ShieldCheck className="size-3.5" aria-hidden />
        {t("brief.badgeCompliant")}
      </p>
    );
  }
  if (status === "non_compliant") {
    return (
      <p className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-red-600 dark:text-red-400">
        <ShieldAlert className="size-3.5" aria-hidden />
        {t("brief.badgeNonCompliant")}
      </p>
    );
  }
  if (status === "uncertain") {
    return (
      <p className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-amber-600 dark:text-amber-400">
        <ShieldQuestion className="size-3.5" aria-hidden />
        {t("brief.badgeUncertain")}
      </p>
    );
  }
  return null;
}

function StatusIcon({ status }: { status: string }) {
  if (status === "satisfied") {
    return <Check className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />;
  }
  if (status === "failed") {
    return <X className="size-4 shrink-0 text-red-600 dark:text-red-400" aria-hidden />;
  }
  return <HelpCircle className="size-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />;
}

function statusLabel(
  t: ReturnType<typeof useTranslations<"task">>,
  status: string
): string {
  switch (status) {
    case "satisfied":
      return t("brief.statusSatisfied");
    case "failed":
      return t("brief.statusFailed");
    case "not_checked":
      return t("brief.statusNotChecked");
    default:
      return t("brief.statusUncertain");
  }
}

function focusLabel(
  t: ReturnType<typeof useTranslations<"task">>,
  focus: string
): string {
  if (focus === "major") return t("brief.focusMajor");
  if (focus === "passing") return t("brief.focusPassing");
  return t("brief.focusAbsent");
}

function RequirementDetails({
  entry,
}: {
  entry: ComplianceRequirementEntry;
}) {
  const t = useTranslations("task");
  const lines: string[] = [];

  if (entry.type === "topic_focus" && entry.focus) {
    lines.push(t("brief.focusLabel", { focus: focusLabel(t, entry.focus) }));
  }
  if (entry.type === "must_mention") {
    if (entry.hit_sources && entry.hit_sources.length) {
      lines.push(t("brief.mentionSources", { sources: entry.hit_sources.join(", ") }));
    } else {
      lines.push(t("brief.mentionNone"));
    }
  }
  if (entry.type === "structure_cta" && entry.form) {
    const formKey =
      entry.form === "spoken"
        ? "ctaFormSpoken"
        : entry.form === "caption_text"
          ? "ctaFormCaption"
          : entry.form === "both"
            ? "ctaFormBoth"
            : "ctaFormNone";
    lines.push(t("brief.ctaForm", { form: t(`brief.${formKey}`) }));
  }
  if (entry.type === "structure_hook" && entry.within_seconds !== undefined) {
    lines.push(t("brief.hookWithin", { seconds: entry.within_seconds }));
  }
  if (entry.type === "caption_rule") {
    lines.push(t("brief.captionChecked"));
  }
  if (entry.type === "visual_presence" && entry.subject) {
    lines.push(t("brief.visualSubject", { subject: entry.subject }));
  }
  if (entry.detail) lines.push(entry.detail);

  const evidence = entry.evidence ?? [];
  const topicHits = (entry.topics ?? []).flatMap((topic) =>
    (topic.hits ?? []).map((hit) => ({ ...hit, topic: topic.topic }))
  );

  return (
    <>
      {lines.length > 0 && (
        <p className="mt-0.5 text-xs text-muted-foreground">{lines.join(" · ")}</p>
      )}
      {evidence.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {evidence.map((hit, index) => (
            <li key={index} className="text-xs text-muted-foreground">
              <span className="tabular-nums">
                {hit.start_seconds !== undefined
                  ? formatTimecode(hit.start_seconds)
                  : ""}
                {hit.end_seconds !== undefined
                  ? `–${formatTimecode(hit.end_seconds)}`
                  : ""}
              </span>{" "}
              “{hit.excerpt}”
            </li>
          ))}
        </ul>
      )}
      {topicHits.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {topicHits.map((hit, index) => (
            <li key={index} className="text-xs text-muted-foreground">
              <span className="tabular-nums">
                {hit.start_seconds !== undefined
                  ? formatTimecode(hit.start_seconds)
                  : ""}
              </span>{" "}
              {hit.topic}: “{hit.excerpt}”
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/** The "Campaign brief" section inside ClipFocus for one clip. */
export function ClipComplianceSection({ report }: { report: ComplianceReport }) {
  const t = useTranslations("task");
  const allowed = report.ranges?.allowed ?? [];
  const violations = report.ranges?.violations ?? [];
  const excludedViolations = report.ranges?.excluded_violations ?? [];
  const flags = report.flags ?? [];
  const actualLabel = report.source_timestamps
    ? `${report.source_timestamps.start}–${report.source_timestamps.end}`
    : "";
  const allowedLabel = allowed
    .map(
      ([start, end]) =>
        `${formatTimecode(start)}–${end === null ? "end" : formatTimecode(end)}`
    )
    .join(", ");

  return (
    <section aria-label={t("brief.reportTitle")} className="rounded-xl border p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{t("brief.reportTitle")}</h3>
        <BriefBadge status={report.status} />
      </div>
      {actualLabel && (
        <p className="mt-1 text-xs text-muted-foreground">
          {allowed.length === 0
            ? t("brief.rangeNoLimits", { actual: actualLabel })
            : violations.length
              ? t("brief.rangeOutside", { actual: actualLabel })
              : t("brief.rangeInside", { actual: actualLabel, allowed: allowedLabel })}
        </p>
      )}
      {excludedViolations.length > 0 && (
        <p className="mt-1 text-xs font-medium text-red-600 dark:text-red-400">
          {t("brief.rangeExcluded")}
        </p>
      )}
      {flags.length > 0 && (
        <div className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/5 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-amber-700 dark:text-amber-300">
            <Flag className="size-3.5" aria-hidden />
            {t("brief.needsReview")}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("brief.needsReviewBody")}
          </p>
          <ul className="mt-1.5 space-y-0.5">
            {flags.map((flag, index) => (
              <li key={index} className="text-xs">
                <span className="font-medium tabular-nums">
                  {flag.at_label ?? formatTimecode(flag.at_seconds)}
                </span>{" "}
                — {t(`brief.flagCategories.${flag.category}`)}
                {flag.note ? `: ${flag.note}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      <ul className="mt-3 space-y-3">
        {report.requirements.map((entry, index) => (
          <li key={entry.id ?? index} className="flex gap-2">
            <StatusIcon status={entry.status} />
            <div className="min-w-0">
              <p className="text-sm">
                <span className="font-medium">{statusLabel(t, entry.status)}</span>
                {" — "}
                {entry.text || entry.type}
                {entry.kind && (
                  <span
                    className={cn(
                      "ml-2 rounded-full px-1.5 py-0.5 text-[10px] font-semibold",
                      entry.kind === "mandatory"
                        ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                        : "bg-muted text-muted-foreground"
                    )}
                  >
                    {entry.kind === "mandatory"
                      ? t("brief.mandatory")
                      : t("brief.optional")}
                  </span>
                )}
              </p>
              <RequirementDetails entry={entry} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The dedicated panel for the honest no-match outcome (zero clips). */
export function BriefNoMatchPanel({
  brief,
  report,
}: {
  brief: StoredBrief | null;
  report: BriefReport | null;
}) {
  const t = useTranslations("task");
  const ranges = report?.searched_ranges ?? brief?.allowed_ranges ?? [];
  const rangesLabel = ranges.length
    ? ranges
        .map((range) => `${range.start_label}–${range.end_label ?? "end"}`)
        .join(", ")
    : t("brief.searchedFullVideo");
  const requirementLines: string[] = [];
  const briefRequirements = new Map(
    (brief?.requirements ?? []).map((req) => [
      String(req.id ?? ""),
      typeof req.text === "string" ? req.text : "",
    ])
  );
  for (const stat of report?.requirements ?? []) {
    const text = briefRequirements.get(String(stat.id ?? "")) || stat.id || "";
    const terms = (stat.terms ?? []).join(", ");
    if (text) {
      requirementLines.push(
        t("brief.searchedRequirement", { text, terms: terms || "—" })
      );
    }
  }
  if (!requirementLines.length && brief) {
    for (const req of brief.requirements) {
      if (typeof req.text === "string" && req.text) requirementLines.push(req.text);
    }
  }

  return (
    <div className="flex flex-col items-center rounded-2xl border border-dashed px-6 py-16 text-center">
      <span className="flex size-14 items-center justify-center rounded-2xl bg-amber-500/10 text-amber-600 dark:text-amber-400">
        <ShieldAlert className="size-7" aria-hidden />
      </span>
      <h2 className="mt-4 text-lg font-semibold">{t("brief.noMatchTitle")}</h2>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">
        {t("brief.noMatchBody")}
      </p>
      <div className="mt-5 max-w-lg space-y-1.5 text-left text-sm">
        <p>{t("brief.searchedRanges", { ranges: rangesLabel })}</p>
        {requirementLines.map((line) => (
          <p key={line} className="text-muted-foreground">
            {line}
          </p>
        ))}
        {report && (
          <p className="tabular-nums">
            {t("brief.candidatesLine", {
              examined: report.candidates_total ?? 0,
              droppedRange: report.candidates_dropped_range ?? 0,
              droppedMandatory: report.candidates_dropped_mandatory ?? 0,
            })}
          </p>
        )}
        <p className="font-medium">{t("brief.noMatchNext")}</p>
      </div>
    </div>
  );
}

/** Small banner on completed brief tasks where candidates were dropped. */
export function BriefDropsBanner({ report }: { report: BriefReport }) {
  const t = useTranslations("task");
  const droppedRange = report.candidates_dropped_range ?? 0;
  const droppedMandatory = report.candidates_dropped_mandatory ?? 0;
  if (droppedRange + droppedMandatory === 0) return null;
  return (
    <div
      role="status"
      className="mb-6 rounded-lg border border-amber-500/25 bg-amber-500/5 p-4 text-sm"
    >
      {t("brief.droppedBanner", {
        dropped: droppedRange + droppedMandatory,
        range: droppedRange,
        mandatory: droppedMandatory,
      })}
    </div>
  );
}
