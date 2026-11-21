"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Check, Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { getClipUrl } from "@/lib/clip-actions";
import {
  applyHookVariant,
  fetchHookPreviews,
  generateHookPreviews,
  readHookVariantError,
  type HookPreview,
} from "@/lib/hook-variants";

/**
 * Hook variants strip (Lane K): generate short previews of alternative
 * openings for the focused clip, compare them side by side, and pick
 * one — the full clip is then re-rendered with that opening and the
 * pick stays marked. Labels and descriptions come from the API (they
 * name the actual hook moment and treatment); this component only owns
 * the chrome text.
 */
export function HookVariantsStrip({
  taskId,
  clipId,
  onApplied,
}: {
  taskId: string;
  clipId: string;
  onApplied?: (clip: Record<string, unknown>) => void;
}) {
  const t = useTranslations("task");
  const [previews, setPreviews] = useState<HookPreview[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [pickingId, setPickingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const busy = generating || pickingId !== null;

  useEffect(() => {
    let cancelled = false;
    setPreviews([]);
    setLoaded(false);
    setError(null);
    fetchHookPreviews(taskId, clipId, (response) =>
      readHookVariantError(response, t("hookVariants.loadFailed")),
    )
      .then((list) => {
        if (!cancelled) {
          setPreviews(list);
          setLoaded(true);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? caught.message
              : t("hookVariants.loadFailed"),
          );
          setLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, clipId, t]);

  const handleGenerate = useCallback(async () => {
    setGenerating(true);
    setError(null);
    try {
      const list = await generateHookPreviews(taskId, clipId, {
        readError: (response) =>
          readHookVariantError(response, t("hookVariants.generateFailed")),
        fallbackMessage: t("hookVariants.generateFailed"),
        timeoutMessage: t("hookVariants.generateFailed"),
      });
      setPreviews(list);
      setLoaded(true);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : t("hookVariants.generateFailed"),
      );
    } finally {
      setGenerating(false);
    }
  }, [taskId, clipId, t]);

  const handlePick = useCallback(
    async (preview: HookPreview) => {
      setPickingId(preview.id);
      setError(null);
      try {
        const clip = await applyHookVariant(taskId, clipId, preview.id, {
          readError: (response) =>
            readHookVariantError(response, t("hookVariants.applyFailed")),
          fallbackMessage: t("hookVariants.applyFailed"),
          timeoutMessage: t("hookVariants.applyFailed"),
        });
        const appliedVariant =
          (typeof clip?.hook_variant === "string" && clip.hook_variant) ||
          (preview.variant === "current" ? "current" : preview.variant);
        setPreviews((current) =>
          current.map((entry) => ({
            ...entry,
            selected: entry.variant === appliedVariant,
          })),
        );
        if (clip) onApplied?.(clip);
      } catch (caught) {
        setError(
          caught instanceof Error
            ? caught.message
            : t("hookVariants.applyFailed"),
        );
      } finally {
        setPickingId(null);
      }
    },
    [taskId, clipId, t, onApplied],
  );

  return (
    <section aria-label={t("hookVariants.title")} className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{t("hookVariants.title")}</h4>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => void handleGenerate()}
          disabled={busy}
        >
          {generating ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Sparkles className="size-4" />
          )}
          {previews.length > 0
            ? t("hookVariants.regenerate")
            : t("hookVariants.generate")}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t("hookVariants.body")}</p>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {loaded && previews.length === 0 && !error ? (
        <p className="text-sm text-muted-foreground">
          {t("hookVariants.empty")}
        </p>
      ) : null}
      {previews.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {previews.map((preview) => (
            <div
              key={preview.id}
              role="group"
              aria-label={preview.label}
              className="space-y-2 rounded-lg border border-border p-2"
            >
              <video
                src={getClipUrl(preview.video_url, preview.id)}
                controls
                preload="metadata"
                className="aspect-[9/16] w-full rounded bg-black object-contain"
              />
              <div className="space-y-1">
                <p className="flex items-center gap-1.5 text-sm font-medium">
                  {preview.label}
                  {preview.selected ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                      <Check className="size-3" />
                      {t("hookVariants.picked")}
                    </span>
                  ) : null}
                </p>
                <p className="text-xs text-muted-foreground">
                  {preview.description}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("hookVariants.duration", {
                    seconds: Math.max(1, Math.round(preview.duration)),
                  })}
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                className="w-full"
                variant={preview.selected ? "secondary" : "default"}
                onClick={() => void handlePick(preview)}
                disabled={busy || preview.selected}
              >
                {pickingId === preview.id ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : null}
                {preview.selected
                  ? t("hookVariants.picked")
                  : pickingId === preview.id
                    ? t("hookVariants.applying")
                    : t("hookVariants.pick")}
              </Button>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
