"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, Check, Loader2, Youtube } from "lucide-react";
import { useTranslations } from "next-intl";

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
import { NICHE_OPTIONS } from "@/lib/niches";

interface ChannelMonitor {
  id: string;
  channel_id: string;
  channel_title: string | null;
  source_input: string;
  enabled: boolean;
  niche_override: string | null;
  music_mode: string | null;
  music_override: string | null;
  template_mode: string;
  creation_mode: string;
  target_clip_count: number | null;
  processing_mode: string | null;
  include_shorts: boolean;
  max_tasks_per_day: number | null;
  campaign_id: string | null;
  brand_kit_id: string | null;
  last_fallback_at: string | null;
  last_polled_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
}

interface ChannelVideo {
  id: string;
  title: string | null;
  channel_title: string | null;
  state: string;
  skip_reason: string | null;
  task_id: string | null;
  attempts: number;
  last_error: string | null;
}

const STATE_STYLES: Record<string, string> = {
  queued: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  processing: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  waiting: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  failed: "bg-red-500/15 text-red-600 dark:text-red-400",
  parked: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400",
  skipped: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400",
  baseline: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
};

export function ChannelMonitorsSection() {
  const t = useTranslations("settings.channelMonitors");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [masterEnabled, setMasterEnabled] = useState(false);
  const [monitors, setMonitors] = useState<ChannelMonitor[]>([]);
  const [videos, setVideos] = useState<ChannelVideo[]>([]);

  const [channelInput, setChannelInput] = useState("");
  const [niche, setNiche] = useState("auto");
  const [templateMode, setTemplateMode] = useState("plain");
  const [creationMode, setCreationMode] = useState("full_auto");
  const [mode, setMode] = useState("default");
  const [clipCount, setClipCount] = useState("");
  const [musicMode, setMusicMode] = useState("auto");
  const [music, setMusic] = useState("");
  const [includeShorts, setIncludeShorts] = useState(false);
  const [dailyCap, setDailyCap] = useState("3");
  const [campaignId, setCampaignId] = useState("");
  const [brandKitId, setBrandKitId] = useState("");
  const [clipNewest, setClipNewest] = useState(false);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [retryNotice, setRetryNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/channel-monitors", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("load failed");
      const data = await response.json();
      setMasterEnabled(Boolean(data.settings?.enabled));
      setMonitors(data.monitors ?? []);
      setVideos(data.videos ?? []);
    } catch {
      setError(t("loadError"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggleMaster(next: boolean) {
    setMasterEnabled(next);
    setError(null);
    try {
      const response = await fetch("/api/channel-monitors/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!response.ok) throw new Error("save failed");
    } catch {
      setMasterEnabled(!next);
      setError(t("saveError"));
    }
  }

  async function addChannel() {
    setAdding(true);
    setAdded(false);
    setError(null);
    const parsedCount = clipCount.trim() === "" ? null : Number(clipCount);
    const parsedCap = dailyCap.trim() === "" ? null : Number(dailyCap);
    try {
      const response = await fetch("/api/channel-monitors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_input: channelInput.trim(),
          niche_override: niche === "auto" ? null : niche,
          template_mode: templateMode,
          creation_mode: creationMode,
          processing_mode: mode === "default" ? null : mode,
          target_clip_count:
            parsedCount != null && Number.isFinite(parsedCount)
              ? parsedCount
              : null,
          music_mode: musicMode === "auto" ? null : musicMode,
          music_override: music.trim() === "" ? null : music.trim(),
          include_shorts: includeShorts,
          max_tasks_per_day:
            parsedCap != null && Number.isFinite(parsedCap) ? parsedCap : null,
          campaign_id: campaignId.trim() === "" ? null : campaignId.trim(),
          brand_kit_id: brandKitId.trim() === "" ? null : brandKitId.trim(),
          clip_newest_now: clipNewest,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof data?.detail === "string" ? data.detail : t("addError"),
        );
      }
      setChannelInput("");
      setCampaignId("");
      setBrandKitId("");
      setClipNewest(false);
      setAdded(true);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("addError"));
    } finally {
      setAdding(false);
    }
  }

  async function toggleMonitor(monitor: ChannelMonitor, next: boolean) {
    setError(null);
    try {
      const response = await fetch(`/api/channel-monitors/${monitor.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!response.ok) throw new Error("save failed");
      await load();
    } catch {
      setError(t("saveError"));
    }
  }

  async function removeMonitor(monitor: ChannelMonitor) {
    setError(null);
    try {
      const response = await fetch(`/api/channel-monitors/${monitor.id}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error("delete failed");
      await load();
    } catch {
      setError(t("saveError"));
    }
  }

  async function retryVideo(video: ChannelVideo) {
    setRetryingId(video.id);
    setRetryNotice(null);
    setError(null);
    try {
      const response = await fetch(
        `/api/channel-monitors/videos/${video.id}/retry`,
        { method: "POST" },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof data?.detail === "string" ? data.detail : t("retryError"),
        );
      }
      setRetryNotice(
        typeof data?.message === "string" ? data.message : t("retryDone"),
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("retryError"));
    } finally {
      setRetryingId(null);
    }
  }

  function stateLabel(state: string): string {
    switch (state) {
      case "processing":
        return t("states.processing");
      case "waiting":
        return t("states.waiting");
      case "queued":
        return t("states.queued");
      case "failed":
        return t("states.failed");
      case "parked":
        return t("states.parked");
      case "skipped":
        return t("states.skipped");
      case "baseline":
        return t("states.baseline");
      default:
        return state;
    }
  }

  function reasonText(video: ChannelVideo): string | null {
    switch (video.skip_reason) {
      case "over_duration_cap":
        return t("reasons.over_duration_cap");
      case "short":
        return t("reasons.short");
      case "daily_cap":
        return t("reasons.daily_cap");
      case "baseline":
        return t("reasons.baseline");
      default:
        break;
    }
    if (video.last_error) {
      return video.state === "parked"
        ? `${video.last_error} (${t("reasons.parkedAfter", { attempts: video.attempts })})`
        : video.last_error;
    }
    return null;
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="flex items-center gap-2 text-2xl font-semibold">
          <Youtube className="size-6" /> {t("title")}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">{t("intro")}</p>
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
            <Label htmlFor="channel-monitor-enabled">{t("masterToggle")}</Label>
            <p className="text-sm text-muted-foreground">
              {t("masterToggleHint")}
            </p>
          </div>
          <Switch
            id="channel-monitor-enabled"
            checked={masterEnabled}
            onCheckedChange={(next) => void toggleMaster(next)}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="channel-monitor-input">{t("channelLabel")}</Label>
          <Input
            id="channel-monitor-input"
            value={channelInput}
            onChange={(event) => setChannelInput(event.target.value)}
            placeholder={t("channelPlaceholder")}
          />
          <p className="text-sm text-muted-foreground">{t("baselineNote")}</p>
        </div>

        <div className="grid gap-6 sm:grid-cols-2">
          <div className="space-y-2">
            <Label>{t("defaults.style")}</Label>
            <Select value={niche} onValueChange={setNiche}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">{t("defaults.autoDetect")}</SelectItem>
                {NICHE_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>{t("defaults.template")}</Label>
            <Select value={templateMode} onValueChange={setTemplateMode}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="plain">
                  {t("defaults.templatePlain")}
                </SelectItem>
                <SelectItem value="auto">{t("defaults.templateAuto")}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>{t("defaults.creationMode")}</Label>
            <Select value={creationMode} onValueChange={setCreationMode}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="full_auto">
                  {t("defaults.creationFullAuto")}
                </SelectItem>
                <SelectItem value="moments_manual">
                  {t("defaults.creationMomentsManual")}
                </SelectItem>
                <SelectItem value="moments_ai">
                  {t("defaults.creationMomentsAi")}
                </SelectItem>
              </SelectContent>
            </Select>
            {creationMode !== "full_auto" && (
              <p className="text-sm text-muted-foreground">
                {t("defaults.momentsWarning")}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label>{t("defaults.processingMode")}</Label>
            <Select value={mode} onValueChange={setMode}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">
                  {t("defaults.processingDefault")}
                </SelectItem>
                <SelectItem value="fast">{t("defaults.processingFast")}</SelectItem>
                <SelectItem value="balanced">
                  {t("defaults.processingBalanced")}
                </SelectItem>
                <SelectItem value="quality">
                  {t("defaults.processingQuality")}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="channel-monitor-count">
              {t("defaults.clipCount")}
            </Label>
            <Input
              id="channel-monitor-count"
              type="number"
              min={1}
              value={clipCount}
              onChange={(event) => setClipCount(event.target.value)}
              placeholder={t("defaults.clipCountPlaceholder")}
            />
          </div>

          <div className="space-y-2">
            <Label>{t("defaults.music")}</Label>
            <Select value={musicMode} onValueChange={setMusicMode}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">{t("defaults.musicAuto")}</SelectItem>
                <SelectItem value="on">{t("defaults.musicOn")}</SelectItem>
                <SelectItem value="off">{t("defaults.musicOff")}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="channel-monitor-music">
              {t("defaults.musicTrack")}
            </Label>
            <Input
              id="channel-monitor-music"
              value={music}
              onChange={(event) => setMusic(event.target.value)}
              placeholder={t("defaults.musicTrackPlaceholder")}
              disabled={musicMode === "off"}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="channel-monitor-cap">{t("defaults.dailyCap")}</Label>
            <Input
              id="channel-monitor-cap"
              type="number"
              min={1}
              max={20}
              value={dailyCap}
              onChange={(event) => setDailyCap(event.target.value)}
            />
            <p className="text-sm text-muted-foreground">
              {t("defaults.dailyCapHint")}
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="channel-monitor-campaign">
              {t("defaults.campaign")}
            </Label>
            <Input
              id="channel-monitor-campaign"
              value={campaignId}
              onChange={(event) => setCampaignId(event.target.value)}
              placeholder={t("defaults.campaignPlaceholder")}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="channel-monitor-brand-kit">
              {t("defaults.brandKit")}
            </Label>
            <Input
              id="channel-monitor-brand-kit"
              value={brandKitId}
              onChange={(event) => setBrandKitId(event.target.value)}
              placeholder={t("defaults.brandKitPlaceholder")}
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="channel-monitor-shorts">
              {t("defaults.includeShorts")}
            </Label>
            <Switch
              id="channel-monitor-shorts"
              checked={includeShorts}
              onCheckedChange={setIncludeShorts}
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="channel-monitor-newest">{t("clipNewest")}</Label>
            <Switch
              id="channel-monitor-newest"
              checked={clipNewest}
              onCheckedChange={setClipNewest}
            />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Button
            onClick={() => void addChannel()}
            disabled={adding || channelInput.trim() === ""}
          >
            {adding ? <Loader2 className="size-4 animate-spin" /> : null}
            {added && !adding ? <Check className="size-4" /> : null}
            {t("addButton")}
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-lg font-medium">{t("listTitle")}</h3>
        {monitors.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("listEmpty")}</p>
        ) : (
          <ul className="divide-y rounded-2xl border">
            {monitors.map((monitor) => (
              <li key={monitor.id} className="space-y-2 p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {monitor.channel_title ?? monitor.channel_id}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {monitor.enabled ? t("enabled") : t("paused")}
                  </span>
                  <Switch
                    checked={monitor.enabled}
                    onCheckedChange={(next) => void toggleMonitor(monitor, next)}
                    aria-label={`${monitor.channel_title ?? monitor.channel_id} ${t("enabled")}`}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void removeMonitor(monitor)}
                  >
                    {t("remove")}
                  </Button>
                </div>
                <p className="font-mono text-xs text-muted-foreground">
                  {monitor.channel_id}
                </p>
                {(monitor.campaign_id || monitor.brand_kit_id) && (
                  <p className="text-xs text-muted-foreground">
                    {monitor.campaign_id &&
                      t("monitorCampaign", { id: monitor.campaign_id })}
                    {monitor.campaign_id && monitor.brand_kit_id ? " · " : ""}
                    {monitor.brand_kit_id &&
                      t("monitorBrandKit", { id: monitor.brand_kit_id })}
                  </p>
                )}
                <p className="text-sm text-muted-foreground">
                  {monitor.last_polled_at
                    ? t("lastChecked", {
                        time: new Date(monitor.last_polled_at).toLocaleString(),
                      })
                    : t("neverChecked")}
                </p>
                {monitor.last_error && (
                  <p className="text-sm text-red-600 dark:text-red-400">
                    {t("checkFailed", { error: monitor.last_error })}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-3">
        <h3 className="text-lg font-medium">{t("activityTitle")}</h3>
        {retryNotice && (
          <p className="text-sm text-muted-foreground" role="status">
            {retryNotice}
          </p>
        )}
        {videos.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("activityEmpty")}</p>
        ) : (
          <ul className="divide-y rounded-2xl border">
            {videos.map((video) => {
              const reason = reasonText(video);
              return (
                <li key={video.id} className="flex flex-wrap items-center gap-3 p-4">
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {video.title ?? video.id}
                    {video.channel_title && (
                      <span className="block truncate text-sm font-normal text-muted-foreground">
                        {video.channel_title}
                      </span>
                    )}
                  </span>
                  <Badge
                    variant="secondary"
                    className={STATE_STYLES[video.state] ?? ""}
                  >
                    {stateLabel(video.state)}
                  </Badge>
                  {video.task_id && (
                    <Link
                      href={`/tasks/${video.task_id}`}
                      className="text-sm underline underline-offset-4"
                    >
                      {t("openTask")}
                    </Link>
                  )}
                  {!video.task_id &&
                    (video.state === "parked" || video.state === "skipped") && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={retryingId === video.id}
                        onClick={() => void retryVideo(video)}
                      >
                        {retryingId === video.id ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : null}
                        {t("retry")}
                      </Button>
                    )}
                  {reason && (
                    <span className="w-full text-sm text-muted-foreground">
                      {reason}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
