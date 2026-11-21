import { CREATION_MODE_OPTIONS } from "@/lib/creation-modes";

/**
 * The run summary on a task page (F61): what a finished run
 * produced and the choices it ran with, assembled only from fields
 * the task and clips payloads already return — the task's stored
 * template_mode and creation_mode (the task API returns both; see
 * the repository serializer) and the clips' own durations. Returns
 * null unless the task completed with clips: a run that is still
 * going, failed, or produced nothing has no run to summarise.
 * A part is omitted when its field is absent or holds a value this
 * build does not know — nothing is invented to fill the line.
 * (Per-clip formats are not part of either payload, so the summary
 * does not claim any.)
 */
export interface RunSummaryPart {
  label: string;
  value: string;
}

const TEMPLATE_LABELS: Record<string, string> = {
  auto: "Auto template",
  plain: "Plain — just cuts",
};

export function runSummaryParts(
  task: {
    status?: string;
    template_mode?: string | null;
    creation_mode?: string | null;
  } | null | undefined,
  clips: { duration?: number | null }[] | null | undefined,
  formatDuration: (seconds: number) => string
): RunSummaryPart[] | null {
  if (!task || task.status !== "completed" || !clips || clips.length === 0) {
    return null;
  }
  const totalSeconds = clips.reduce((sum, clip) => sum + (clip.duration || 0), 0);
  const parts: RunSummaryPart[] = [
    { label: "Clips", value: String(clips.length) },
    { label: "Total length", value: formatDuration(totalSeconds) },
  ];
  const templateLabel = task.template_mode
    ? TEMPLATE_LABELS[task.template_mode]
    : undefined;
  if (templateLabel) {
    parts.push({ label: "Template", value: templateLabel });
  }
  const modeLabel = task.creation_mode
    ? CREATION_MODE_OPTIONS.find((mode) => mode.value === task.creation_mode)
        ?.label
    : undefined;
  if (modeLabel) {
    parts.push({ label: "Mode", value: modeLabel });
  }
  return parts;
}
