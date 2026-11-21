"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, Scissors, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { FontOption } from "@/components/font-select-option";
import { AudioStudioPanel } from "@/components/app/audio-studio-panel";
import { SfxPanel } from "@/components/app/sfx-panel";
import { StudioPanels } from "@/components/app/studio-panels";
import {
  buildEditRequest,
  captionsChanged,
  moveSegmentDown,
  moveSegmentUp,
  pollClipEdit,
  splitAtMidpoint,
  trimSegment,
  type CaptionOverrides,
  type EditPlanResponse,
  type EditorSegment,
  type SegmentOperation,
} from "@/lib/clip-edit";

/** Minimal clip shape the panel needs (the task page's Clip satisfies it). */
export type EditableClip = {
  id: string;
  filename?: string;
};

const CAPTION_POSITIONS = ["top", "middle", "bottom"] as const;
const KEEP_TEMPLATE = "__keep__";
const KEEP_FONT = "__keep__";

type CaptionTemplateOption = { id: string; name: string };

interface ClipEditorPanelProps {
  taskId: string;
  clip: EditableClip;
  templates: CaptionTemplateOption[];
  fonts: FontOption[];
  busy: boolean;
  onApply: (clip: EditableClip, apply: () => Promise<void>) => void;
  onRefreshed: (clip: Record<string, unknown>) => void;
}

/**
 * Segment editor + Caption Studio for one clip. Edits are queued through
 * the same worker/re-render pipeline as a restyle, so an applied edit
 * always produces a new rendered file (never a fake timeline).
 */
export function ClipEditorPanel({
  taskId,
  clip,
  templates,
  fonts,
  busy,
  onApply,
  onRefreshed,
}: ClipEditorPanelProps) {
  const t = useTranslations("task");
  const [plan, setPlan] = useState<EditPlanResponse | null>(null);
  const [rows, setRows] = useState<EditorSegment[]>([]);
  const [extraOps, setExtraOps] = useState<SegmentOperation[]>([]);
  const [captions, setCaptions] = useState<CaptionOverrides | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [planVersion, setPlanVersion] = useState(0);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiSummary, setAiSummary] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPlan(null);
    setRows([]);
    setExtraOps([]);
    setCaptions(null);
    setLoadError(null);
    setAiSummary(null);
    void fetch(`/api/tasks/${taskId}/clips/${clip.id}/edit`, { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(String(response.status));
        return (await response.json()) as EditPlanResponse;
      })
      .then((data) => {
        if (cancelled) return;
        setPlan(data);
        setRows(data.plan.segments);
        setCaptions(data.plan.captions ?? {});
      })
      .catch(() => {
        if (!cancelled) setLoadError(t("editor.loadFailed"));
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, clip.id, t, planVersion]);

  const updateRow = (position: number, patch: Partial<EditorSegment>) => {
    setRows((current) =>
      current.map((row, i) => (i === position ? { ...row, ...patch } : row)),
    );
  };

  const commitTrim = (position: number) => {
    const row = rows[position];
    if (!row) return;
    const op = trimSegment(row, row.start, row.end);
    if (!op || op.start === undefined || op.end === undefined) return;
    const newStart = op.start;
    const newEnd = op.end;
    setExtraOps((current) => [...current, op]);
    // Mirror the backend's shift so the local preview stays tiled.
    setRows((current) => {
      const delta = newEnd - newStart - (row.end - row.start);
      return current.map((entry, i) => {
        if (i === position) return { ...entry, start: newStart, end: newEnd };
        if (i > position) {
          return { ...entry, start: entry.start + delta, end: entry.end + delta };
        }
        return entry;
      });
    });
  };

  const handleApply = () => {
    if (!plan || !captions) return;
    const request = buildEditRequest(plan.plan.segments, rows, null);
    const operations = [...extraOps.filter((op) => op.type === "split"), ...(request?.operations ?? [])];
    const finalRequest = {
      ...(operations.length > 0 ? { operations } : {}),
      ...(captionsChanged(plan.plan.captions, captions) ? { captions } : {}),
    };
    if (Object.keys(finalRequest).length === 0) return;
    onApply(clip, async () => {
      const outcome = await pollClipEdit({
        start: () =>
          fetch(`/api/tasks/${taskId}/clips/${clip.id}/edit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(finalRequest),
            cache: "no-store",
          }),
        status: (jobId) =>
          fetch(
            `/api/tasks/${taskId}/clips/${clip.id}/edit/${encodeURIComponent(jobId)}`,
            { cache: "no-store" },
          ),
        readError: async () => t("errors.editClip"),
        fallbackMessage: t("errors.editClip"),
        timeoutMessage: t("errors.editClip"),
      });
      if (outcome.clip) {
        onRefreshed(outcome.clip);
      }
      toast.success(t("toasts.clipEdited"));
    });
  };

  const handleAiApply = () => {
    const instruction = aiInstruction.trim();
    if (!instruction) return;
    onApply(clip, async () => {
      const response = await fetch(
        `/api/tasks/${taskId}/clips/${clip.id}/ai-edit`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instruction }),
          cache: "no-store",
        },
      );
      if (!response.ok) {
        let detail = t("errors.editClip");
        try {
          const data = (await response.json()) as { detail?: unknown };
          if (typeof data?.detail === "string" && data.detail) {
            detail = data.detail;
          }
        } catch {
          // Keep the generic message when the body is not JSON.
        }
        throw new Error(detail);
      }
      const data = (await response
        .clone()
        .json()
        .catch(() => null)) as { summary?: unknown } | null;
      if (data && typeof data.summary === "string") {
        setAiSummary(data.summary);
      }
      const outcome = await pollClipEdit({
        start: () => Promise.resolve(response),
        status: (jobId) =>
          fetch(
            `/api/tasks/${taskId}/clips/${clip.id}/edit/${encodeURIComponent(jobId)}`,
            { cache: "no-store" },
          ),
        readError: async () => t("errors.editClip"),
        fallbackMessage: t("errors.editClip"),
        timeoutMessage: t("errors.editClip"),
      });
      if (outcome.clip) {
        onRefreshed(outcome.clip);
      }
      setAiInstruction("");
      // The AI may have changed the segment layout: reload the plan so
      // the rows below describe the clip as it now is.
      setPlanVersion((version) => version + 1);
      toast.success(t("toasts.clipEdited"));
    });
  };

  if (loadError) {
    return <p className="px-1 py-2 text-sm text-muted-foreground">{loadError}</p>;
  }
  if (!plan || !captions) {
    return (
      <p className="px-1 py-2 text-sm text-muted-foreground">{t("editor.loading")}</p>
    );
  }

  const captionValue = (key: keyof CaptionOverrides) => captions[key];

  return (
    <div className="space-y-4 px-1 py-3">
      <div>
        <h3 className="text-sm font-semibold">{t("editor.title")}</h3>
        <p className="text-xs text-muted-foreground">{t("editor.hint")}</p>
      </div>

      <div className="space-y-2">
        {rows.map((row, position) => (
          <div
            key={row.index}
            className="flex flex-wrap items-center gap-2 rounded-md border p-2"
          >
            <span className="text-xs font-medium">
              {t("editor.segment", { index: position + 1 })}
            </span>
            <Input
              type="number"
              step="0.1"
              min={0}
              className="w-20"
              value={row.start}
              disabled={busy}
              aria-label={t("editor.start")}
              onChange={(event) =>
                updateRow(position, { start: Number(event.target.value) })
              }
              onBlur={() => commitTrim(position)}
            />
            <span className="text-xs text-muted-foreground">→</span>
            <Input
              type="number"
              step="0.1"
              min={0}
              className="w-20"
              value={row.end}
              disabled={busy}
              aria-label={t("editor.end")}
              onChange={(event) =>
                updateRow(position, { end: Number(event.target.value) })
              }
              onBlur={() => commitTrim(position)}
            />
            <span className="text-xs text-muted-foreground">
              {(row.end - row.start).toFixed(1)}s
            </span>
            <div className="ml-auto flex gap-1">
              <Button
                size="icon"
                variant="ghost"
                disabled={busy || position === 0}
                aria-label={t("editor.moveUp")}
                onClick={() => {
                  const op = moveSegmentUp(rows, position);
                  if (!op) return;
                  setExtraOps((current) => [...current, op]);
                  setRows((current) => {
                    const next = [...current];
                    const [moved] = next.splice(position, 1);
                    next.splice(position - 1, 0, moved);
                    return retile(next);
                  });
                }}
              >
                <ArrowUp className="size-4" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                disabled={busy || position === rows.length - 1}
                aria-label={t("editor.moveDown")}
                onClick={() => {
                  const op = moveSegmentDown(rows, position);
                  if (!op) return;
                  setExtraOps((current) => [...current, op]);
                  setRows((current) => {
                    const next = [...current];
                    const [moved] = next.splice(position, 1);
                    next.splice(position + 1, 0, moved);
                    return retile(next);
                  });
                }}
              >
                <ArrowDown className="size-4" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                disabled={busy}
                aria-label={t("editor.split")}
                onClick={() => {
                  const op = splitAtMidpoint(row);
                  const at = op.at;
                  setExtraOps((current) => [...current, op]);
                  setRows((current) => {
                    const next = [...current];
                    const first = { ...row, end: at };
                    const second: EditorSegment = {
                      ...row,
                      index: Math.max(...current.map((s) => s.index)) + 1,
                      start: at,
                      transition_in: "hard_cut",
                      transition_duration: 0,
                    };
                    next.splice(position, 1, first, second);
                    return retile(next);
                  });
                }}
              >
                <Scissors className="size-4" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                disabled={busy || rows.length <= 1}
                aria-label={t("editor.delete")}
                onClick={() =>
                  setRows((current) =>
                    retile(current.filter((_, i) => i !== position)),
                  )
                }
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <h4 className="text-sm font-semibold">{t("editor.captionsTitle")}</h4>
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-xs">
            {t("editor.captionTemplate")}
            <Select
              value={(captionValue("template") as string) ?? KEEP_TEMPLATE}
              disabled={busy}
              onValueChange={(value: string) =>
                setCaptions((current) => ({
                  ...current,
                  template: value === KEEP_TEMPLATE ? undefined : value,
                }))
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={KEEP_TEMPLATE}>{t("editor.keepCurrent")}</SelectItem>
                {templates.map((template) => (
                  <SelectItem key={template.id} value={template.id}>
                    {template.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-1 text-xs">
            {t("editor.captionFont")}
            <Select
              value={(captionValue("font_family") as string) ?? KEEP_FONT}
              disabled={busy}
              onValueChange={(value: string) =>
                setCaptions((current) => ({
                  ...current,
                  font_family: value === KEEP_FONT ? undefined : value,
                }))
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={KEEP_FONT}>{t("editor.keepCurrent")}</SelectItem>
                {fonts.map((font) => (
                  <SelectItem key={font.name} value={font.name}>
                    {font.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-1 text-xs">
            {t("editor.captionSize")}
            <Input
              type="number"
              min={12}
              max={72}
              disabled={busy}
              value={(captionValue("font_size") as number) ?? ""}
              onChange={(event) =>
                setCaptions((current) => ({
                  ...current,
                  font_size:
                    event.target.value === ""
                      ? undefined
                      : Number(event.target.value),
                }))
              }
            />
          </label>
          <label className="space-y-1 text-xs">
            {t("editor.captionColour")}
            <Input
              type="color"
              disabled={busy}
              value={(captionValue("font_color") as string) ?? "#ffffff"}
              onChange={(event) =>
                setCaptions((current) => ({
                  ...current,
                  font_color: event.target.value,
                }))
              }
            />
          </label>
          <label className="space-y-1 text-xs">
            {t("editor.captionPosition")}
            <Select
              value={(captionValue("position") as string) ?? KEEP_TEMPLATE}
              disabled={busy}
              onValueChange={(value: string) =>
                setCaptions((current) => ({
                  ...current,
                  position: value === KEEP_TEMPLATE ? undefined : value,
                }))
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={KEEP_TEMPLATE}>{t("editor.keepCurrent")}</SelectItem>
                {CAPTION_POSITIONS.map((position) => (
                  <SelectItem key={position} value={position}>
                    {t(`editor.position.${position}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="space-y-1 text-xs">
            {t("editor.captionWordsPerLine")}
            <Input
              type="number"
              min={1}
              max={8}
              disabled={busy}
              value={(captionValue("words_per_line") as number) ?? ""}
              onChange={(event) =>
                setCaptions((current) => ({
                  ...current,
                  words_per_line:
                    event.target.value === ""
                      ? undefined
                      : Number(event.target.value),
                }))
              }
            />
          </label>
        </div>
      </div>

      {plan.look && plan.transitions && plan.grade_presets ? (
        <StudioPanels
          taskId={taskId}
          clipId={clip.id}
          initial={plan.look}
          transitions={plan.transitions}
          presets={plan.grade_presets}
          luts={plan.luts ?? []}
          busy={busy}
          onApply={(apply) => onApply(clip, apply)}
          onRefreshed={onRefreshed}
          onPlanReload={() => setPlanVersion((version) => version + 1)}
        />
      ) : null}

      {plan.audio ? (
        <AudioStudioPanel
          taskId={taskId}
          clipId={clip.id}
          audio={plan.audio}
          busy={busy}
          onApply={(apply) => onApply(clip, apply)}
          onRefreshed={onRefreshed}
          onPlanReload={() => setPlanVersion((version) => version + 1)}
        />
      ) : null}

      {plan.sfx ? (
        <SfxPanel
          taskId={taskId}
          clipId={clip.id}
          initial={plan.sfx}
          duration={plan.plan.total_duration}
          busy={busy}
          onApply={(apply) => onApply(clip, apply)}
          onRefreshed={onRefreshed}
          onPlanReload={() => setPlanVersion((version) => version + 1)}
        />
      ) : null}

      <div className="space-y-2 rounded-md border p-3">
        <h4 className="text-sm font-semibold">{t("editor.aiTitle")}</h4>
        <p className="text-xs text-muted-foreground">{t("editor.aiHint")}</p>
        <Input
          value={aiInstruction}
          disabled={busy}
          placeholder={t("editor.aiPlaceholder")}
          aria-label={t("editor.aiTitle")}
          onChange={(event) => setAiInstruction(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") handleAiApply();
          }}
        />
        <Button
          onClick={handleAiApply}
          disabled={busy || !aiInstruction.trim()}
          variant="secondary"
          className="w-full"
        >
          {t("editor.aiApply")}
        </Button>
        {aiSummary ? (
          <p className="text-xs text-muted-foreground">{aiSummary}</p>
        ) : null}
      </div>

      <Button onClick={handleApply} disabled={busy} className="w-full">
        {busy ? t("editor.applying") : t("editor.apply")}
      </Button>
    </div>
  );
}

/** Recompute start/end so local rows tile [0, total] after a reorder/split. */
function retile(rows: EditorSegment[]): EditorSegment[] {
  let cursor = 0;
  return rows.map((row) => {
    const duration = row.end - row.start;
    const next = { ...row, start: Math.round(cursor * 1000) / 1000 };
    cursor += duration;
    next.end = Math.round(cursor * 1000) / 1000;
    next.duration = Math.round(duration * 1000) / 1000;
    return next;
  });
}
