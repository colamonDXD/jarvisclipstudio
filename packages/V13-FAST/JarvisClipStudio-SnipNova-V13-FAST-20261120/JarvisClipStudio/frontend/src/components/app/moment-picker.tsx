"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Check, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

export interface MomentCandidate {
  start_time?: string;
  end_time?: string;
  text?: string;
  reasoning?: string;
  virality_score?: number;
  relevance_score?: number;
  /**
   * The hook title the analysis already wrote for this moment
   * (carried on the candidate slate by the backend; null on the
   * visual-led path until its floor title is filled). Shown only
   * when a real title exists — never invented here.
   */
  hook_title?: string | null;
}

/**
 * How long the park lasts, as reported by the candidates endpoint
 * for an awaiting-selection task: the pick is not a forever wait —
 * past the deadline the task continues with the top picks on its own
 * (and records that the pick was automatic).
 */
export interface SelectionWaitInfo {
  autoPickMinutes: number;
  autoPickDeadline: string | null;
}

function formatDeadline(deadline: string | null): string | null {
  if (!deadline) return null;
  const date = new Date(deadline);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

/**
 * The Find-Moments review step (tracker 1.9): the pipeline found
 * candidate moments and parked the task at awaiting_selection. The
 * user ticks the moments worth rendering; submitting writes the pick
 * as the task's final plan and queues rendering. Nothing here invents
 * candidates — an empty slate says so instead of rendering a picker
 * over nothing.
 */
export function MomentPicker({
  taskId,
  readOnly = false,
  onSubmitted,
}: {
  taskId: string;
  readOnly?: boolean;
  onSubmitted?: () => void;
}) {
  const [candidates, setCandidates] = useState<MomentCandidate[] | null>(null);
  const [waitInfo, setWaitInfo] = useState<SelectionWaitInfo | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/tasks/${taskId}/candidates`);
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      const data = (await response.json()) as {
        candidates?: MomentCandidate[];
        auto_pick_minutes?: number;
        auto_pick_deadline?: string | null;
      };
      setCandidates(Array.isArray(data.candidates) ? data.candidates : []);
      setWaitInfo(
        typeof data.auto_pick_minutes === "number" &&
          data.auto_pick_minutes > 0
          ? {
              autoPickMinutes: data.auto_pick_minutes,
              autoPickDeadline:
                typeof data.auto_pick_deadline === "string"
                  ? data.auto_pick_deadline
                  : null,
            }
          : null,
      );
    } catch {
      setLoadError("Could not load the candidate moments. Reload to try again.");
    }
  }, [taskId]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = (index: number) => {
    setPicked((previous) => {
      const next = new Set(previous);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const submit = async () => {
    if (picked.size === 0 || isSubmitting) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const response = await fetch(`/api/tasks/${taskId}/select-moments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segment_indexes: [...picked].sort((a, b) => a - b) }),
      });
      if (!response.ok) {
        const info = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(
          typeof info?.detail === "string"
            ? info.detail
            : `Request failed (${response.status})`,
        );
      }
      setSubmitted(true);
      onSubmitted?.();
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : "Could not queue rendering.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div role="status" className="rounded-2xl border bg-card p-6">
        <p className="flex items-center gap-2 font-medium">
          <Check className="size-4" /> Your picks are queued
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          Rendering starts in a moment — this page will switch to progress
          automatically.
        </p>
      </div>
    );
  }

  return (
    <section aria-label="Pick moments to render" className="rounded-2xl border bg-card p-4 sm:p-6">
      <h2 className="font-display text-lg font-bold tracking-tight">
        Pick the moments to render
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        The AI found these candidate moments and stopped, as this creation
        mode requires. Only the moments you tick will be rendered.
      </p>
      {waitInfo && (
        <p role="status" className="mt-2 text-sm text-muted-foreground">
          Waiting for your pick — if you don&apos;t pick within{" "}
          {waitInfo.autoPickMinutes} minutes
          {formatDeadline(waitInfo.autoPickDeadline)
            ? ` (by ${formatDeadline(waitInfo.autoPickDeadline)})`
            : ""}
          , this continues with the top picks automatically.
        </p>
      )}

      {loadError && (
        <p role="alert" className="mt-4 flex items-center gap-2 text-sm text-destructive">
          <AlertCircle className="size-4" /> {loadError}
        </p>
      )}
      {candidates === null && !loadError && (
        <p role="status" className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading moments…
        </p>
      )}
      {candidates !== null && candidates.length === 0 && !loadError && (
        <p role="status" className="mt-4 text-sm text-muted-foreground">
          No candidate moments are stored for this task anymore, so there is
          nothing to pick.
        </p>
      )}

      {candidates !== null && candidates.length > 0 && (
        <>
          <ul className="mt-4 divide-y rounded-xl border">
            {candidates.map((candidate, index) => {
              const isPicked = picked.has(index);
              return (
                <li key={index}>
                  <label
                    className={cn(
                      "flex cursor-pointer items-start gap-3 px-4 py-3 transition-colors hover:bg-muted/60",
                      isPicked && "bg-muted/60",
                    )}
                  >
                    <Checkbox
                      checked={isPicked}
                      onCheckedChange={() => toggle(index)}
                      disabled={readOnly || isSubmitting}
                      aria-label={`Pick moment ${index + 1}`}
                      className="mt-0.5"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <span className="font-mono text-sm font-medium">
                          {candidate.start_time ?? "?"} – {candidate.end_time ?? "?"}
                        </span>
                        {typeof candidate.virality_score === "number" &&
                          candidate.virality_score > 0 && (
                            <span className="rounded-full bg-foreground px-1.5 py-0.5 text-[10px] font-semibold text-background">
                              {Math.round(candidate.virality_score)}
                            </span>
                          )}
                      </span>
                      {typeof candidate.hook_title === "string" &&
                        candidate.hook_title.trim() && (
                          <span className="mt-0.5 block text-sm font-medium">
                            {candidate.hook_title}
                          </span>
                        )}
                      {candidate.text && (
                        <span className="mt-0.5 line-clamp-2 block text-sm text-muted-foreground">
                          {candidate.text}
                        </span>
                      )}
                      {candidate.reasoning && (
                        <span className="mt-0.5 line-clamp-1 block text-xs text-muted-foreground">
                          {candidate.reasoning}
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>

          {submitError && (
            <p role="alert" className="mt-3 flex items-center gap-2 text-sm text-destructive">
              <AlertCircle className="size-4" /> {submitError}
            </p>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => void submit()}
              disabled={readOnly || picked.size === 0 || isSubmitting}
            >
              {isSubmitting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Check className="size-4" />
              )}
              Render {picked.size > 0 ? `${picked.size} ` : ""}selected
            </Button>
            <span className="text-xs text-muted-foreground">
              {picked.size} of {candidates.length} picked
            </span>
          </div>
        </>
      )}
    </section>
  );
}
