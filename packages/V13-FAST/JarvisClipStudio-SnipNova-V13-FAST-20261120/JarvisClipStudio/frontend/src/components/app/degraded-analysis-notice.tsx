"use client";

import { useTranslations } from "next-intl";

/**
 * Marker the deterministic selection floor stamps into a clip's reasoning
 * when it picks moments while the AI analysis is down (backend
 * visual_moments: "Visual selection (AI analysis unavailable): ...",
 * persisted on the clip and returned by the task read API). The sibling
 * "no usable transcript" floor context deliberately does NOT carry this
 * marker: that floor is the designed primary path for transcript-less
 * sources, not a degradation, so it must never trigger this notice.
 */
export const AI_ANALYSIS_UNAVAILABLE_MARKER = "(AI analysis unavailable)";

export function clipsPickedWithoutAiAnalysis(
  clips: readonly { reasoning?: string | null }[],
): boolean {
  return clips.some((clip) =>
    (clip.reasoning ?? "").includes(AI_ANALYSIS_UNAVAILABLE_MARKER),
  );
}

/**
 * Amber notice on the task page when a completed task's clips were picked
 * by the built-in sound-and-scene floor because AI analysis was
 * unavailable. Mirrors the partial-failure banner beside it: a degraded
 * run must never look like a normal AI-picked run.
 */
export function DegradedAnalysisNotice({
  status,
  clips,
}: {
  status: string;
  clips: readonly { reasoning?: string | null }[];
}) {
  const t = useTranslations("task");
  if (status !== "completed" || !clipsPickedWithoutAiAnalysis(clips)) {
    return null;
  }
  return (
    <div role="status" className="mb-6 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4">
      <p className="font-medium">{t("degradedTitle")}</p>
      <p className="mt-1 text-sm text-muted-foreground">{t("degradedBody")}</p>
    </div>
  );
}
