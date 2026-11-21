"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import {
  pollClipEdit,
  type SfxPlacement,
  type SfxState,
} from "@/lib/clip-edit";

const MAX_PLACEMENTS = 16;
const GAIN_MIN = -30;
const GAIN_MAX = 12;

interface SfxPanelProps {
  taskId: string;
  clipId: string;
  /** Stored placements + the placeable library (assets/sfx). */
  initial: SfxState;
  /** The clip's total duration in seconds (placement upper bound). */
  duration: number;
  busy: boolean;
  /** Runs an async apply under the task page's per-clip busy state. */
  onApply: (apply: () => Promise<void>) => void;
  onRefreshed: (clip: Record<string, unknown>) => void;
  /** Ask the parent editor to reload the plan after a re-render. */
  onPlanReload: () => void;
}

const round2 = (value: number) => Math.round(value * 100) / 100;

/**
 * SFX panel for one clip: browse the renderer's own SFX library
 * (assets/sfx), preview files, and place them on the clip's timeline
 * with a per-placement gain. Applying queues an edit whose placements
 * the worker appends to the EDL's sfx_events, so the exported file
 * actually contains them — there is no preview-only state here. The
 * panel always sends the FULL placement list: an empty list is the
 * explicit "no placements" and replaces whatever was stored.
 */
export function SfxPanel({
  taskId,
  clipId,
  initial,
  duration,
  busy,
  onApply,
  onRefreshed,
  onPlanReload,
}: SfxPanelProps) {
  const t = useTranslations("task");
  const [placements, setPlacements] = useState<SfxPlacement[]>(
    initial.placements,
  );
  const [library, setLibrary] = useState(initial.library);
  const [uploading, setUploading] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // A reloaded plan replaces the draft so the panel always opens on
  // the clip's current truth.
  useEffect(() => {
    setPlacements(initial.placements);
    setLibrary(initial.library);
  }, [initial]);

  useEffect(() => {
    return () => {
      audioRef.current?.pause();
      audioRef.current = null;
    };
  }, []);

  const changed =
    JSON.stringify(placements) !== JSON.stringify(initial.placements);

  const togglePreview = (filename: string) => {
    if (playing === filename) {
      audioRef.current?.pause();
      setPlaying(null);
      return;
    }
    audioRef.current?.pause();
    const audio = new Audio(
      `/api/sfx/file/${encodeURIComponent(filename)}`,
    );
    audio.onended = () => setPlaying(null);
    audioRef.current = audio;
    setPlaying(filename);
    void audio.play().catch(() => setPlaying(null));
  };

  const addPlacement = (name: string) => {
    setPlacements((current) =>
      current.length >= MAX_PLACEMENTS
        ? current
        : [...current, { name, at_sec: 0, gain_db: 0 }],
    );
  };

  const updatePlacement = (index: number, patch: Partial<SfxPlacement>) => {
    setPlacements((current) =>
      current.map((placement, i) =>
        i === index ? { ...placement, ...patch } : placement,
      ),
    );
  };

  const removePlacement = (index: number) => {
    setPlacements((current) => current.filter((_, i) => i !== index));
  };

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/sfx/upload", {
        method: "POST",
        body: form,
      });
      if (!response.ok) {
        throw new Error(`upload failed: ${response.status}`);
      }
      const entry = (await response.json()) as {
        filename: string;
        display_name: string;
        duration: number | null;
        source?: "bundled" | "user";
      };
      setLibrary((current) =>
        current.some((item) => item.filename === entry.filename)
          ? current
          : [...current, entry],
      );
    } catch {
      toast.error(t("editor.sfx.uploadFailed"));
    } finally {
      setUploading(false);
    }
  };

  const handleApply = () => {
    onApply(async () => {
      const outcome = await pollClipEdit({
        start: () =>
          fetch(`/api/tasks/${taskId}/clips/${clipId}/edit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sfx: placements }),
            cache: "no-store",
          }),
        status: (jobId) =>
          fetch(
            `/api/tasks/${taskId}/clips/${clipId}/edit/${encodeURIComponent(jobId)}`,
            { cache: "no-store" },
          ),
        readError: async () => t("errors.editClip"),
        fallbackMessage: t("errors.editClip"),
        timeoutMessage: t("errors.editClip"),
      });
      if (outcome.clip) {
        onRefreshed(outcome.clip);
      }
      onPlanReload();
      toast.success(t("toasts.clipEdited"));
    });
  };

  return (
    <div className="space-y-3 rounded-md border p-3">
      <div>
        <h4 className="text-sm font-semibold">{t("editor.sfx.title")}</h4>
        <p className="text-xs text-muted-foreground">{t("editor.sfx.hint")}</p>
      </div>

      {library.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {t("editor.sfx.emptyLibrary")}
        </p>
      ) : (
        <ul className="space-y-1">
          {library.map((entry) => (
            <li
              key={entry.filename}
              className="flex items-center justify-between gap-2 text-xs"
            >
              <span className="truncate">
                {entry.display_name}
                {entry.source === "user" ? (
                  <span className="text-muted-foreground">
                    {" "}
                    · {t("editor.sfx.yourEffects")}
                  </span>
                ) : null}
                {entry.duration != null ? (
                  <span className="text-muted-foreground">
                    {" "}
                    · {entry.duration.toFixed(1)}s
                  </span>
                ) : null}
              </span>
              <span className="flex shrink-0 gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  aria-label={t("editor.sfx.preview", {
                    name: entry.display_name,
                  })}
                  onClick={() => togglePreview(entry.filename)}
                >
                  {playing === entry.filename
                    ? t("editor.sfx.stop")
                    : t("editor.sfx.play")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || placements.length >= MAX_PLACEMENTS}
                  aria-label={t("editor.sfx.place", {
                    name: entry.display_name,
                  })}
                  onClick={() => addPlacement(entry.filename)}
                >
                  {t("editor.sfx.placeButton")}
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}

      <div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".mp3,.wav,.m4a,.aac,.ogg,.flac,.opus,.webm"
          className="hidden"
          aria-label={t("editor.sfx.upload")}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) {
              void handleUpload(file);
            }
          }}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy || uploading}
          onClick={() => fileInputRef.current?.click()}
        >
          {uploading ? t("editor.sfx.uploading") : t("editor.sfx.upload")}
        </Button>
      </div>

      {placements.length > 0 ? (
        <ul className="space-y-2">
          {placements.map((placement, index) => (
            <li key={`${placement.name}-${index}`} className="space-y-1">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate font-medium">{placement.name}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  aria-label={t("editor.sfx.remove", { name: placement.name })}
                  onClick={() => removePlacement(index)}
                >
                  {t("editor.sfx.removeButton")}
                </Button>
              </div>
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1 text-xs">
                  {t("editor.sfx.at")}
                  <Input
                    type="number"
                    min={0}
                    max={duration > 0 ? duration : undefined}
                    step={0.1}
                    value={placement.at_sec}
                    disabled={busy}
                    aria-label={t("editor.sfx.atLabel", {
                      name: placement.name,
                    })}
                    className="h-7 w-20"
                    onChange={(event) => {
                      const value = Number(event.target.value);
                      if (Number.isFinite(value)) {
                        updatePlacement(index, {
                          at_sec: Math.min(
                            Math.max(0, value),
                            duration > 0 ? duration : value,
                          ),
                        });
                      }
                    }}
                  />
                </label>
                <div className="flex flex-1 items-center gap-2">
                  <span className="text-xs">{t("editor.sfx.volume")}</span>
                  <Slider
                    aria-label={t("editor.sfx.volumeLabel", {
                      name: placement.name,
                    })}
                    min={GAIN_MIN}
                    max={GAIN_MAX}
                    step={1}
                    value={[placement.gain_db]}
                    disabled={busy}
                    onValueChange={(values) =>
                      updatePlacement(index, {
                        gain_db: round2(values[0] ?? placement.gain_db),
                      })
                    }
                  />
                  <span className="w-12 text-right text-xs text-muted-foreground">
                    {placement.gain_db} dB
                  </span>
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">{t("editor.sfx.none")}</p>
      )}

      <Button
        onClick={handleApply}
        disabled={busy || !changed}
        variant="secondary"
        className="w-full"
      >
        {t("editor.sfx.apply")}
      </Button>
    </div>
  );
}
