"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Check, ChevronDown, Loader2, Music2, Pause, Play, Plus } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface MusicTrack {
  filename: string;
  display_name: string;
  source: "bundled" | "user";
  duration_sec: number | null;
}

interface MusicSelectProps {
  /** Selected library filename; null means Auto (the profile mood picks). */
  value: string | null;
  onChange: (filename: string | null) => void;
  disabled?: boolean;
}

function formatDuration(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds) || seconds <= 0) return null;
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/**
 * The create bar's music picker: Auto, every library track with a
 * preview play/pause toggle, and a Custom music upload. The library is
 * folder-driven on the server, so this control just lists what the API
 * returns; if the fetch fails it degrades to Auto only and stays usable.
 */
export function MusicSelect({ value, onChange, disabled }: MusicSelectProps) {
  const t = useTranslations("home");
  const [open, setOpen] = useState(false);
  const [tracks, setTracks] = useState<MusicTrack[]>([]);
  const [playing, setPlaying] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadTracks = async (): Promise<MusicTrack[]> => {
    try {
      const response = await fetch("/api/music/library", { cache: "no-store" });
      if (!response.ok) return [];
      const data = await response.json();
      const list = Array.isArray(data?.tracks) ? (data.tracks as MusicTrack[]) : [];
      setTracks(list);
      return list;
    } catch {
      setTracks([]);
      return [];
    }
  };

  useEffect(() => {
    void loadTracks();
  }, []);

  const stopPreview = () => {
    audioRef.current?.pause();
    setPlaying(null);
  };

  // Never leave a preview playing after the control unmounts.
  useEffect(() => {
    return () => {
      audioRef.current?.pause();
      audioRef.current = null;
    };
  }, []);

  const togglePreview = (track: MusicTrack) => {
    if (playing === track.filename) {
      stopPreview();
      return;
    }
    // One shared element: starting a preview replaces any other.
    const audio = audioRef.current ?? new Audio();
    audioRef.current = audio;
    audio.src = `/api/music/file/${encodeURIComponent(track.filename)}`;
    audio.onended = () => setPlaying(null);
    setPlaying(track.filename);
    void audio.play().catch(() => setPlaying(null));
  };

  const choose = (filename: string | null) => {
    stopPreview();
    onChange(filename);
    setOpen(false);
  };

  const handleUpload = async (file: File) => {
    setUploading(true);
    setUploadError(false);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/music/upload", {
        method: "POST",
        body: form,
      });
      if (!response.ok) throw new Error(`Upload failed: ${response.status}`);
      const entry = (await response.json()) as MusicTrack;
      await loadTracks();
      if (entry?.filename) choose(entry.filename);
    } catch {
      setUploadError(true);
    } finally {
      setUploading(false);
    }
  };

  const selectedTrack = tracks.find((track) => track.filename === value) ?? null;
  const triggerLabel = selectedTrack ? selectedTrack.display_name : t("music.auto");

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) stopPreview();
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={t("music.label")}
          className="border-input data-[placeholder]:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 flex h-8 w-[168px] items-center justify-between gap-2 rounded-md border bg-transparent px-3 text-xs whitespace-nowrap shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50"
        >
          <span className="flex min-w-0 items-center gap-2">
            <Music2 className="size-3.5 shrink-0 opacity-60" />
            <span className="truncate">{triggerLabel}</span>
          </span>
          <ChevronDown className="size-3.5 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[280px] rounded-2xl p-1.5">
        <div className="max-h-[320px] overflow-y-auto">
          <button
            type="button"
            onClick={() => choose(null)}
            className={cn(
              "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-accent",
              value === null && "font-medium"
            )}
          >
            <span className="flex size-6 shrink-0 items-center justify-center">
              {value === null && <Check className="size-3.5" />}
            </span>
            <span className="min-w-0 flex-1 truncate">{t("music.auto")}</span>
          </button>

          {tracks.map((track) => {
            const isPlaying = playing === track.filename;
            const duration = formatDuration(track.duration_sec);
            return (
              <div
                key={track.filename}
                className={cn(
                  "flex w-full items-center gap-1 rounded-lg pr-1 hover:bg-accent",
                  value === track.filename && "font-medium"
                )}
              >
                <button
                  type="button"
                  aria-label={isPlaying ? t("music.stopPreview") : t("music.preview", { name: track.display_name })}
                  onClick={() => togglePreview(track)}
                  className="flex size-7 shrink-0 items-center justify-center rounded-full hover:bg-accent-foreground/10"
                >
                  {isPlaying ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                </button>
                <button
                  type="button"
                  onClick={() => choose(track.filename)}
                  className="flex min-w-0 flex-1 items-center gap-2 py-1.5 text-left text-sm"
                >
                  <span className="min-w-0 flex-1 truncate">{track.display_name}</span>
                  {duration && <span className="shrink-0 text-xs text-muted-foreground">{duration}</span>}
                  {value === track.filename && <Check className="size-3.5 shrink-0" />}
                </button>
              </div>
            );
          })}
        </div>

        <div className="mt-1 border-t pt-1.5">
          <button
            type="button"
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
            className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-accent disabled:opacity-50"
          >
            <span className="flex size-6 shrink-0 items-center justify-center">
              {uploading ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
            </span>
            <span>{uploading ? t("music.uploading") : t("music.customMusic")}</span>
          </button>
          {uploadError && (
            <p role="alert" className="px-2 pt-1 text-xs text-red-600 dark:text-red-400">
              {t("music.uploadFailed")}
            </p>
          )}
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept=".mp3,.wav,.ogg,.m4a,.flac,audio/*"
          className="hidden"
          aria-hidden="true"
          tabIndex={-1}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void handleUpload(file);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
