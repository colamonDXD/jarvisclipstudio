"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, Check, FolderOpen, Loader2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ChannelMonitorsSection } from "@/components/channel-monitors-section";
import { NICHE_OPTIONS } from "@/lib/niches";

interface WatchSettings {
  enabled: boolean;
  folder_path: string | null;
  niche_override: string | null;
  music_override: string | null;
  music_mode: string | null;
  target_clip_count: number | null;
  processing_mode: string | null;
  effective_folder: string;
  default_folder: string;
}

interface WatchFile {
  id: string;
  file_name: string;
  state: string;
  task_id: string | null;
  attempts: number;
  last_error: string | null;
  created_at: string | null;
}

const STATE_STYLES: Record<string, string> = {
  queued: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  processing: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  failed: "bg-red-500/15 text-red-600 dark:text-red-400",
  parked: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400",
};

export default function AutopilotSettingsPage() {
  const [settings, setSettings] = useState<WatchSettings | null>(null);
  const [files, setFiles] = useState<WatchFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [enabled, setEnabled] = useState(false);
  const [folderPath, setFolderPath] = useState("");
  const [niche, setNiche] = useState("auto");
  const [music, setMusic] = useState("");
  const [musicMode, setMusicMode] = useState("auto");
  const [clipCount, setClipCount] = useState("");
  const [mode, setMode] = useState("default");

  const applySettings = useCallback((next: WatchSettings) => {
    setSettings(next);
    setEnabled(next.enabled);
    setFolderPath(next.folder_path ?? "");
    setNiche(next.niche_override ?? "auto");
    setMusic(next.music_override ?? "");
    setMusicMode(next.music_mode ?? "auto");
    setClipCount(
      next.target_clip_count != null ? String(next.target_clip_count) : "",
    );
    setMode(next.processing_mode ?? "default");
  }, []);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/watch-folder", { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load autopilot settings");
      const data = await response.json();
      applySettings(data.settings);
      setFiles(data.files ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load settings");
    } finally {
      setLoading(false);
    }
  }, [applySettings]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setSaving(true);
    setSaved(false);
    setError(null);
    const parsedCount = clipCount.trim() === "" ? null : Number(clipCount);
    try {
      const response = await fetch("/api/watch-folder/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled,
          folder_path: folderPath.trim() === "" ? null : folderPath.trim(),
          niche_override: niche === "auto" ? null : niche,
          music_override: music.trim() === "" ? null : music.trim(),
          music_mode: musicMode === "auto" ? null : musicMode,
          target_clip_count:
            parsedCount != null && Number.isFinite(parsedCount)
              ? parsedCount
              : null,
          processing_mode: mode === "default" ? null : mode,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof data?.detail === "string"
            ? data.detail
            : "Could not save autopilot settings",
        );
      }
      applySettings(data.settings);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save settings");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto w-full max-w-3xl space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-8 p-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          <FolderOpen className="size-6" /> Watch-folder autopilot
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Drop a video into the watched folder and it becomes a task on its
          own, with the defaults below. A file is only picked up once its
          size stops changing, each file is queued at most once, and your
          originals are never moved or deleted.
        </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="size-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="space-y-6 rounded-2xl border bg-background p-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <Label htmlFor="autopilot-enabled">Autopilot</Label>
            <p className="text-sm text-muted-foreground">
              When on, the app scans the folder every few seconds.
            </p>
          </div>
          <Switch
            id="autopilot-enabled"
            checked={enabled}
            onCheckedChange={setEnabled}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="autopilot-folder">Folder to watch</Label>
          <Input
            id="autopilot-folder"
            value={folderPath}
            onChange={(event) => setFolderPath(event.target.value)}
            placeholder={settings?.default_folder ?? ""}
          />
          <p className="text-sm text-muted-foreground">
            Leave empty to use the default folder. Currently watching:{" "}
            <span className="font-mono">
              {folderPath.trim() || settings?.effective_folder}
            </span>
          </p>
        </div>

        <div className="grid gap-6 sm:grid-cols-2">
          <div className="space-y-2">
            <Label>Style for dropped videos</Label>
            <Select value={niche} onValueChange={setNiche}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto-detect</SelectItem>
                {NICHE_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Processing mode</Label>
            <Select value={mode} onValueChange={setMode}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">App default</SelectItem>
                <SelectItem value="fast">Fast</SelectItem>
                <SelectItem value="balanced">Balanced</SelectItem>
                <SelectItem value="quality">Quality</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="autopilot-count">Clips per video</Label>
            <Input
              id="autopilot-count"
              type="number"
              min={1}
              value={clipCount}
              onChange={(event) => setClipCount(event.target.value)}
              placeholder="Auto"
            />
          </div>

          <div className="space-y-2">
            <Label>Music</Label>
            <Select value={musicMode} onValueChange={setMusicMode}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto — let the style decide</SelectItem>
                <SelectItem value="on">On — always play music</SelectItem>
                <SelectItem value="off">Off — no music</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-sm text-muted-foreground">
              Auto lets the style decide. On plays your track below, or the
              style&rsquo;s own music when no track is set. Off adds no music,
              so you can add your own later.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="autopilot-music">Music track</Label>
            <Input
              id="autopilot-music"
              value={music}
              onChange={(event) => setMusic(event.target.value)}
              placeholder="None — the style decides"
              disabled={musicMode === "off"}
            />
            <p className="text-sm text-muted-foreground">
              A filename from your music library, or empty.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Button onClick={() => void save()} disabled={saving}>
            {saving ? (
              <Loader2 className="size-4 animate-spin" />
            ) : saved ? (
              <Check className="size-4" />
            ) : null}
            Save autopilot settings
          </Button>
          {saved && (
            <span className="text-sm text-muted-foreground">
              Saved. Changes apply from the next scan.
            </span>
          )}
        </div>
      </div>

      <div className="space-y-3">
        <h2 className="text-lg font-medium">Recent autopilot files</h2>
        {files.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing picked up yet. Files the autopilot queues will show up
            here with the task they created.
          </p>
        ) : (
          <ul className="divide-y rounded-2xl border">
            {files.map((file) => (
              <li
                key={file.id}
                className="flex flex-wrap items-center gap-3 p-4"
              >
                <span className="min-w-0 flex-1 truncate font-medium">
                  {file.file_name}
                </span>
                <Badge
                  variant="secondary"
                  className={STATE_STYLES[file.state] ?? ""}
                >
                  {file.state}
                </Badge>
                {file.task_id && (
                  <Link
                    href={`/tasks/${file.task_id}`}
                    className="text-sm underline underline-offset-4"
                  >
                    Open task
                  </Link>
                )}
                {file.last_error && (
                  <span className="w-full text-sm text-red-600 dark:text-red-400">
                    {file.last_error}
                    {file.state === "parked"
                      ? ` (gave up after ${file.attempts} attempts)`
                      : ""}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <ChannelMonitorsSection />
    </div>
  );
}
