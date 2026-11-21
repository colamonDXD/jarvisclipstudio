"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import {
  buildAudioOverrides,
  pollClipEdit,
  type AudioState,
  type AudioValues,
  type VoiceoverRequest,
} from "@/lib/clip-edit";

type AudioStudioPanelProps = {
  taskId: string;
  clipId: string;
  audio: AudioState;
  busy: boolean;
  onApply: (work: () => Promise<void>) => void;
  onRefreshed: (clip: Record<string, unknown>) => void;
  onPlanReload: () => void;
};

const SLIDERS: {
  key: keyof AudioValues;
  max: number;
  step: number;
  labelKey: string;
  suffix: string;
}[] = [
  { key: "music_volume", max: 100, step: 1, labelKey: "musicVolume", suffix: "%" },
  { key: "ducking_db", max: 30, step: 1, labelKey: "ducking", suffix: " dB" },
  { key: "noise_reduction", max: 100, step: 1, labelKey: "noiseReduction", suffix: "%" },
  { key: "voice_clarity", max: 100, step: 1, labelKey: "voiceClarity", suffix: "%" },
  { key: "fade_in", max: 5, step: 0.5, labelKey: "fadeIn", suffix: "s" },
  { key: "fade_out", max: 5, step: 0.5, labelKey: "fadeOut", suffix: "s" },
];

function valuesOf(audio: AudioState): AudioValues {
  return {
    music_track: audio.music_track,
    music_volume: audio.music_volume,
    ducking_db: audio.ducking_db,
    noise_reduction: audio.noise_reduction,
    voice_clarity: audio.voice_clarity,
    loudness: audio.loudness,
    fade_in: audio.fade_in,
    fade_out: audio.fade_out,
  };
}

/**
 * Audio Studio for one clip: music, ducking, cleanup, loudness, fades
 * and — when the local voiceover feature is switched on — a Kokoro
 * narration. Every control is wired to the render through the same edit
 * job the studio panels use; drafts start from the clip's effective
 * audio and only changed fields are sent back.
 */
export function AudioStudioPanel({
  taskId,
  clipId,
  audio,
  busy,
  onApply,
  onRefreshed,
  onPlanReload,
}: AudioStudioPanelProps) {
  const t = useTranslations("task");
  const [draft, setDraft] = useState<AudioValues>(() => valuesOf(audio));
  const [script, setScript] = useState(audio.voiceover?.script ?? "");
  const [voice, setVoice] = useState(
    audio.voiceover?.voice ?? audio.voices[0]?.name ?? "",
  );
  const [mode, setMode] = useState(audio.voiceover?.mode ?? "hook");
  const [clearVo, setClearVo] = useState(false);

  // A reloaded plan replaces the drafts so the panel always opens on
  // the clip's current truth.
  useEffect(() => {
    setDraft(valuesOf(audio));
    setScript(audio.voiceover?.script ?? "");
    setVoice(audio.voiceover?.voice ?? audio.voices[0]?.name ?? "");
    setMode(audio.voiceover?.mode ?? "hook");
    setClearVo(false);
  }, [audio]);

  const audioOverrides = buildAudioOverrides(valuesOf(audio), draft);
  let voiceover: VoiceoverRequest | undefined;
  if (audio.voiceover_enabled) {
    if (clearVo && audio.voiceover) {
      voiceover = { clear: true };
    } else if (
      !clearVo &&
      script.trim() &&
      (script.trim() !== (audio.voiceover?.script ?? "") ||
        voice !== (audio.voiceover?.voice ?? "") ||
        mode !== (audio.voiceover?.mode ?? "hook"))
    ) {
      voiceover = { script: script.trim(), voice, mode };
    }
  }
  const changed = audioOverrides !== null || voiceover !== undefined;

  const handleApply = () => {
    if (!changed) return;
    const payload: Record<string, unknown> = {};
    if (audioOverrides) payload.audio = audioOverrides;
    if (voiceover) payload.voiceover = voiceover;
    onApply(async () => {
      const outcome = await pollClipEdit({
        start: () =>
          fetch(`/api/tasks/${taskId}/clips/${clipId}/edit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
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

  const setField = (key: keyof AudioValues, value: number | string) =>
    setDraft((current) => ({ ...current, [key]: value }));

  return (
    <div className="space-y-3">
      <div>
        <h4 className="text-sm font-semibold">{t("editor.audio.title")}</h4>
        <p className="text-xs text-muted-foreground">{t("editor.audio.hint")}</p>
        {audio.stored ? (
          <p className="mt-1 text-xs text-muted-foreground">
            {t("editor.audio.storedBadge")}
          </p>
        ) : null}
      </div>

      <div className="space-y-2">
        <label
          className="text-xs font-medium"
          htmlFor="audio-music-track"
        >
          {t("editor.audio.musicTrack")}
        </label>
        <Select
          value={draft.music_track}
          onValueChange={(value) => setField("music_track", value)}
          disabled={busy}
        >
          <SelectTrigger id="audio-music-track">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">{t("editor.audio.trackAuto")}</SelectItem>
            <SelectItem value="none">{t("editor.audio.trackNone")}</SelectItem>
            {audio.tracks.map((track) => (
              <SelectItem key={track.filename} value={track.filename}>
                {track.display_name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {SLIDERS.map((control) => (
          <div key={control.key} className="space-y-1">
            <label
              className="text-xs font-medium"
              htmlFor={`audio-${control.key}`}
            >
              {t(`editor.audio.${control.labelKey}`, {
                value: `${draft[control.key]}${control.suffix}`,
              })}
            </label>
            <Slider
              id={`audio-${control.key}`}
              aria-label={t(`editor.audio.${control.labelKey}`, {
                value: `${draft[control.key]}${control.suffix}`,
              })}
              min={0}
              max={control.max}
              step={control.step}
              value={[Number(draft[control.key])]}
              disabled={busy}
              onValueChange={([value]) => setField(control.key, value)}
            />
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <label className="text-xs font-medium" htmlFor="audio-loudness">
          {t("editor.audio.loudness")}
        </label>
        <Select
          value={draft.loudness}
          onValueChange={(value) => setField("loudness", value)}
          disabled={busy}
        >
          <SelectTrigger id="audio-loudness">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">{t("editor.audio.loudnessAuto")}</SelectItem>
            <SelectItem value="-16">{t("editor.audio.loudness16")}</SelectItem>
            <SelectItem value="off">{t("editor.audio.loudnessOff")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {audio.voiceover_enabled ? (
        <div className="space-y-3 border-t pt-3">
          <div>
            <h5 className="text-sm font-semibold">
              {t("editor.audio.voiceoverTitle")}
            </h5>
            <p className="text-xs text-muted-foreground">
              {t("editor.audio.voiceoverHint")}
            </p>
          </div>
          <div className="space-y-2">
            <label className="text-xs font-medium" htmlFor="audio-vo-script">
              {t("editor.audio.scriptLabel")}
            </label>
            <Textarea
              id="audio-vo-script"
              value={script}
              maxLength={600}
              disabled={busy || clearVo}
              placeholder={t("editor.audio.scriptPlaceholder")}
              onChange={(event) => setScript(event.target.value)}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <label className="text-xs font-medium" htmlFor="audio-vo-voice">
                {t("editor.audio.voice")}
              </label>
              <Select
                value={voice}
                onValueChange={setVoice}
                disabled={busy || clearVo}
              >
                <SelectTrigger id="audio-vo-voice">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {audio.voices.map((option) => (
                    <SelectItem key={option.name} value={option.name}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-xs font-medium" htmlFor="audio-vo-mode">
                {t("editor.audio.mode")}
              </label>
              <Select
                value={mode}
                onValueChange={setMode}
                disabled={busy || clearVo}
              >
                <SelectTrigger id="audio-vo-mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="hook">
                    {t("editor.audio.modeHook")}
                  </SelectItem>
                  <SelectItem value="replace">
                    {t("editor.audio.modeReplace")}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          {audio.voiceover && !clearVo ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setClearVo(true)}
            >
              {t("editor.audio.removeVoiceover")}
            </Button>
          ) : null}
          {clearVo ? (
            <p className="text-xs text-muted-foreground">
              {t("editor.audio.voiceoverWillClear")}
            </p>
          ) : null}
        </div>
      ) : null}

      <Button
        type="button"
        size="sm"
        disabled={busy || !changed}
        onClick={handleApply}
      >
        {t("editor.audio.apply")}
      </Button>
    </div>
  );
}
