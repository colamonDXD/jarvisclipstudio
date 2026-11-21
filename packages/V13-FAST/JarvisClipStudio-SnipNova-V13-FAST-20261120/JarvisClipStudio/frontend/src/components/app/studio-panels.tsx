"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import {
  buildLookOverrides,
  pollClipEdit,
  type GradePreset,
  type GradeValues,
  type LookState,
  type LutOption,
  type TransitionOption,
  type VfxValues,
} from "@/lib/clip-edit";

type PanelKind = "transitions" | "colour" | "vfx";

const GRADE_CONTROLS: { key: keyof GradeValues }[] = [
  { key: "exposure" },
  { key: "contrast" },
  { key: "saturation" },
  { key: "temperature" },
  { key: "tint" },
  { key: "highlights" },
  { key: "shadows" },
];

const VFX_CONTROLS: { key: keyof VfxValues; max: number }[] = [
  { key: "grain", max: 100 },
  { key: "vignette", max: 100 },
  { key: "glow", max: 100 },
  { key: "sharpen", max: 100 },
  { key: "letterbox", max: 40 },
  { key: "shake", max: 100 },
];

const GROUP_ORDER = ["fades", "slides", "wipes", "shapes", "zoom_blur"];

interface StudioPanelsProps {
  taskId: string;
  clipId: string;
  /** The clip's current effective look (profile look + stored user fields). */
  initial: LookState;
  transitions: TransitionOption[];
  presets: GradePreset[];
  /** The user's imported .cube LUT library (upload adds to it live). */
  luts?: LutOption[];
  busy: boolean;
  /** Runs an async apply under the task page's per-clip busy state. */
  onApply: (apply: () => Promise<void>) => void;
  onRefreshed: (clip: Record<string, unknown>) => void;
  /** Ask the parent editor to reload the plan after a studio re-render. */
  onPlanReload: () => void;
}

function SliderRow({
  label,
  value,
  min,
  max,
  disabled,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  disabled: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span>{label}</span>
        <span className="text-muted-foreground">{value}</span>
      </div>
      <Slider
        aria-label={label}
        min={min}
        max={max}
        step={1}
        value={[value]}
        disabled={disabled}
        onValueChange={(values) => onChange(values[0] ?? value)}
      />
    </div>
  );
}

/**
 * Studio panels (Transitions / Colour / VFX) for one clip. Every control
 * is wired to the render: applying queues an edit whose look overrides the
 * worker stamps onto the clip's EDL, so the exported file changes — there
 * is no preview-only state here. Drafts start from the clip's effective
 * look (its profile's grade first, the user's saved fields on top) and
 * only changed fields are sent back.
 */
export function StudioPanels({
  taskId,
  clipId,
  initial,
  transitions,
  presets,
  luts = [],
  busy,
  onApply,
  onRefreshed,
  onPlanReload,
}: StudioPanelsProps) {
  const t = useTranslations("task");
  const [panel, setPanel] = useState<PanelKind | null>(null);
  const [transition, setTransition] = useState(initial.transition);
  const [grade, setGrade] = useState<GradeValues>({ ...initial.grade });
  const [vfx, setVfx] = useState<VfxValues>({ ...initial.vfx });
  const [presetName, setPresetName] = useState<string>("");
  const [lut, setLut] = useState<string | null>(initial.lut ?? null);
  const [library, setLibrary] = useState<LutOption[]>(luts);
  const [uploading, setUploading] = useState(false);
  const lutFileRef = useRef<HTMLInputElement>(null);

  // A reloaded plan (e.g. after a studio re-render) replaces the draft so
  // the panels always open on the clip's current truth.
  useEffect(() => {
    setTransition(initial.transition);
    setGrade({ ...initial.grade });
    setVfx({ ...initial.vfx });
    setPresetName("");
    setLut(initial.lut ?? null);
    setLibrary(luts);
    // The library list travels with the plan load that produced
    // `initial`; uploads between loads are kept in local state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);

  const look = buildLookOverrides(initial, { transition, grade, vfx, lut });
  const changed = look !== null;

  const handleLutUpload = async (file: File) => {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/luts/upload", {
        method: "POST",
        body: form,
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
          error?: string;
        } | null;
        toast.error(
          payload?.detail ?? payload?.error ?? t("editor.studio.lutFailed"),
        );
        return;
      }
      const entry = (await response.json()) as LutOption;
      setLibrary((current) => [
        ...current.filter((item) => item.filename !== entry.filename),
        entry,
      ]);
      setLut(entry.filename);
      toast.success(t("editor.studio.lutImported", { name: entry.display_name }));
    } catch {
      toast.error(t("editor.studio.lutFailed"));
    } finally {
      setUploading(false);
    }
  };

  const handleLutDelete = async () => {
    if (!lut) return;
    try {
      const response = await fetch(`/api/luts/${encodeURIComponent(lut)}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        toast.error(t("editor.studio.lutFailed"));
        return;
      }
      setLibrary((current) => current.filter((item) => item.filename !== lut));
      setLut(null);
    } catch {
      toast.error(t("editor.studio.lutFailed"));
    }
  };

  const handleApply = () => {
    if (!look) return;
    onApply(async () => {
      const outcome = await pollClipEdit({
        start: () =>
          fetch(`/api/tasks/${taskId}/clips/${clipId}/edit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ look }),
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

  const toggle = (kind: PanelKind) =>
    setPanel((current) => (current === kind ? null : kind));

  return (
    <div className="space-y-3">
      <div>
        <h4 className="text-sm font-semibold">{t("editor.studio.title")}</h4>
        <p className="text-xs text-muted-foreground">{t("editor.studio.hint")}</p>
      </div>

      <div className="flex gap-2" role="group" aria-label={t("editor.studio.title")}>
        <Button
          type="button"
          size="sm"
          variant={panel === "transitions" ? "default" : "outline"}
          disabled={busy}
          aria-pressed={panel === "transitions"}
          onClick={() => toggle("transitions")}
        >
          {t("editor.studio.transitions")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant={panel === "colour" ? "default" : "outline"}
          disabled={busy}
          aria-pressed={panel === "colour"}
          onClick={() => toggle("colour")}
        >
          {t("editor.studio.colour")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant={panel === "vfx" ? "default" : "outline"}
          disabled={busy}
          aria-pressed={panel === "vfx"}
          onClick={() => toggle("vfx")}
        >
          {t("editor.studio.vfx")}
        </Button>
      </div>

      {panel === "transitions" ? (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            {t("editor.studio.transitionsHint")}
          </p>
          <Select
            value={transition}
            disabled={busy}
            onValueChange={(value: string) => setTransition(value)}
          >
            <SelectTrigger aria-label={t("editor.studio.transitions")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {GROUP_ORDER.map((group) => (
                <SelectGroup key={group}>
                  <SelectLabel>{t(`editor.studio.group.${group}`)}</SelectLabel>
                  {transitions
                    .filter((entry) => entry.group === group)
                    .map((entry) => (
                      <SelectItem key={entry.name} value={entry.name}>
                        {t(`editor.studio.transition.${entry.name}`)}
                      </SelectItem>
                    ))}
                </SelectGroup>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

      {panel === "colour" ? (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {t("editor.studio.colourHint")}
          </p>
          <p className="text-xs text-muted-foreground">
            {t("editor.studio.profileLook", { look: initial.profile_lut })}
          </p>
          <label className="block space-y-1 text-xs">
            {t("editor.studio.preset")}
            <Select
              value={presetName || undefined}
              disabled={busy}
              onValueChange={(value: string) => {
                const preset = presets.find((entry) => entry.name === value);
                if (!preset) return;
                setPresetName(value);
                setGrade({ ...preset.grade });
              }}
            >
              <SelectTrigger aria-label={t("editor.studio.preset")}>
                <SelectValue placeholder={t("editor.studio.presetPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {presets.map((preset) => (
                  <SelectItem key={preset.name} value={preset.name}>
                    {t(`editor.studio.presetName.${preset.name}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <div className="space-y-2">
            <label className="block space-y-1 text-xs">
              {t("editor.studio.lut")}
              <Select
                value={lut ?? "none"}
                disabled={busy || uploading}
                onValueChange={(value: string) =>
                  setLut(value === "none" ? null : value)
                }
              >
                <SelectTrigger aria-label={t("editor.studio.lut")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">
                    {t("editor.studio.lutNone")}
                  </SelectItem>
                  {library.map((entry) => (
                    <SelectItem key={entry.filename} value={entry.filename}>
                      {`${entry.display_name} (${entry.size}³)`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <p className="text-xs text-muted-foreground">
              {t("editor.studio.lutHint")}
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy || uploading}
                onClick={() => lutFileRef.current?.click()}
              >
                {uploading
                  ? t("editor.studio.lutUploading")
                  : t("editor.studio.lutUpload")}
              </Button>
              {lut ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || uploading}
                  onClick={handleLutDelete}
                >
                  {t("editor.studio.lutDelete")}
                </Button>
              ) : null}
              <input
                ref={lutFileRef}
                type="file"
                accept=".cube"
                className="hidden"
                aria-label={t("editor.studio.lutUpload")}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void handleLutUpload(file);
                }}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {GRADE_CONTROLS.map(({ key }) => (
              <SliderRow
                key={key}
                label={t(`editor.studio.grade.${key}`)}
                value={grade[key]}
                min={-100}
                max={100}
                disabled={busy}
                onChange={(value) => {
                  setPresetName("");
                  setGrade((current) => ({ ...current, [key]: value }));
                }}
              />
            ))}
          </div>
        </div>
      ) : null}

      {panel === "vfx" ? (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">{t("editor.studio.vfxHint")}</p>
          <div className="grid grid-cols-2 gap-3">
            {VFX_CONTROLS.map(({ key, max }) => (
              <SliderRow
                key={key}
                label={t(`editor.studio.effect.${key}`)}
                value={vfx[key]}
                min={0}
                max={max}
                disabled={busy}
                onChange={(value) =>
                  setVfx((current) => ({ ...current, [key]: value }))
                }
              />
            ))}
          </div>
        </div>
      ) : null}

      <Button
        onClick={handleApply}
        disabled={busy || !changed}
        variant="secondary"
        className="w-full"
      >
        {t("editor.studio.apply")}
      </Button>
    </div>
  );
}
