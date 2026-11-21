"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { AlertTriangle, CheckCircle2, Download, HelpCircle, XCircle } from "lucide-react";

import { ClipCover } from "@/components/app/clip-cover";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getClipUrl } from "@/lib/clip-actions";
import {
  bundleUrl,
  deletePost,
  markPosted,
  saveStats,
  updatePost,
  type BoardClip,
  type BoardData,
  type ClipPost,
  type PlatformId,
  type PostPlatform,
  type Readiness,
} from "@/lib/publishing";
import { cn } from "@/lib/utils";

const PLATFORM_ORDER: PlatformId[] = ["youtube_shorts", "instagram_reels", "tiktok"];
const POST_PLATFORMS: PostPlatform[] = [...PLATFORM_ORDER, "other"];

function ReadinessBadge({ readiness }: { readiness: Readiness }) {
  const t = useTranslations("publishing");
  const styles = {
    ready: "text-emerald-600 dark:text-emerald-400",
    warning: "text-amber-600 dark:text-amber-400",
    blocked: "text-red-600 dark:text-red-400",
    unknown: "text-muted-foreground",
  } as const;
  const Icon = {
    ready: CheckCircle2,
    warning: AlertTriangle,
    blocked: XCircle,
    unknown: HelpCircle,
  }[readiness.status];
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-medium", styles[readiness.status])}>
      <Icon className="size-3.5" aria-hidden />
      {t(`readiness.${readiness.status}`)}
    </span>
  );
}

function ReadinessRow({ platform, readiness }: { platform: PlatformId; readiness: Readiness }) {
  const t = useTranslations("publishing");
  return (
    <details className="group">
      <summary className="flex cursor-pointer items-center justify-between gap-2 py-1 text-sm">
        <span>{t(`platforms.${platform}`)}</span>
        <ReadinessBadge readiness={readiness} />
      </summary>
      <ul className="space-y-1 pb-2 pl-1 text-xs text-muted-foreground">
        {readiness.checks.map((check) => (
          <li key={check.name}>
            <span className="font-medium capitalize">{check.name}: </span>
            {check.detail}
          </li>
        ))}
      </ul>
    </details>
  );
}

function StatsForm({ post, onSaved }: { post: ClipPost; onSaved: () => void }) {
  const t = useTranslations("publishing");
  const [values, setValues] = useState({
    views: post.stats?.views?.toString() ?? "",
    likes: post.stats?.likes?.toString() ?? "",
    comments: post.stats?.comments?.toString() ?? "",
    earnings: post.stats?.earnings?.toString() ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const parse = (raw: string) => (raw.trim() === "" ? null : Number(raw));
      await saveStats(post.id, {
        views: parse(values.views),
        likes: parse(values.likes),
        comments: parse(values.comments),
        earnings: parse(values.earnings),
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const field = (key: keyof typeof values, label: string) => (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      {label}
      <Input
        inputMode="numeric"
        value={values[key]}
        onChange={(event) => setValues({ ...values, [key]: event.target.value })}
        aria-label={label}
      />
    </label>
  );

  return (
    <div className="mt-2 rounded-lg bg-muted/50 p-3">
      <p className="text-xs font-medium">{t("post.statsHeading")}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{t("post.statsNote")}</p>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {field("views", t("post.views"))}
        {field("likes", t("post.likes"))}
        {field("comments", t("post.comments"))}
        {field("earnings", t("post.earnings"))}
      </div>
      {error ? <p className="mt-2 text-xs text-red-500">{error}</p> : null}
      <Button size="sm" className="mt-2" onClick={() => void save()} disabled={busy}>
        {t("post.saveStats")}
      </Button>
    </div>
  );
}

function SubmissionReminder({ post, onSaved }: { post: ClipPost; onSaved: () => void }) {
  const t = useTranslations("publishing");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
      <p className="font-medium text-amber-700 dark:text-amber-300">{t("post.needsSubmission")}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <Input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder={t("post.submissionUrlPlaceholder")}
          aria-label={t("post.submissionUrl")}
          className="min-w-48 flex-1"
        />
        <Button
          size="sm"
          disabled={busy || !url.trim()}
          onClick={async () => {
            setBusy(true);
            try {
              await updatePost(post.id, { submission_url: url.trim() });
              onSaved();
            } finally {
              setBusy(false);
            }
          }}
        >
          {t("post.addSubmission")}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await updatePost(post.id, { submission_dismissed: true });
              onSaved();
            } finally {
              setBusy(false);
            }
          }}
        >
          {t("post.dismiss")}
        </Button>
      </div>
    </div>
  );
}

function PostRecord({ post, onChanged }: { post: ClipPost; onChanged: () => void }) {
  const t = useTranslations("publishing");
  return (
    <div className="border-t py-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span>
          {t("post.postedOn", { platform: t(`platforms.${post.platform}`) })}
          {post.campaign_name ? <span className="text-muted-foreground"> · {post.campaign_name}</span> : null}
        </span>
        <span className="flex items-center gap-2">
          {post.post_url ? (
            <a className="text-xs underline" href={post.post_url} target="_blank" rel="noreferrer">
              {post.post_url}
            </a>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              await deletePost(post.id);
              onChanged();
            }}
          >
            {t("post.remove")}
          </Button>
        </span>
      </div>
      {post.needs_submission ? <SubmissionReminder post={post} onSaved={onChanged} /> : null}
      <StatsForm post={post} onSaved={onChanged} />
    </div>
  );
}

function MarkPostedForm({ clip, data, onChanged }: { clip: BoardClip; data: BoardData; onChanged: () => void }) {
  const t = useTranslations("publishing");
  const [open, setOpen] = useState(false);
  const [platform, setPlatform] = useState<PostPlatform>("youtube_shorts");
  const [postUrl, setPostUrl] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!open) {
    return (
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        {t("post.markPosted")}
      </Button>
    );
  }
  return (
    <div className="mt-2 space-y-2 rounded-lg bg-muted/50 p-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          {t("campaigns.platform")}
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm text-foreground"
            value={platform}
            onChange={(event) => setPlatform(event.target.value as PostPlatform)}
            aria-label={t("campaigns.platform")}
          >
            {POST_PLATFORMS.map((id) => (
              <option key={id} value={id}>
                {t(`platforms.${id}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          {t("post.campaign")}
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm text-foreground"
            value={campaignId}
            onChange={(event) => setCampaignId(event.target.value)}
            aria-label={t("post.campaign")}
          >
            <option value="">{t("post.noCampaign")}</option>
            {data.campaigns.map((campaign) => (
              <option key={campaign.id} value={campaign.id}>
                {campaign.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Input
        value={postUrl}
        onChange={(event) => setPostUrl(event.target.value)}
        placeholder={t("post.postUrlPlaceholder")}
        aria-label={t("post.postUrl")}
      />
      {error ? <p className="text-xs text-red-500">{error}</p> : null}
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await markPosted({
                clip_id: clip.clip_id,
                platform,
                post_url: postUrl.trim() || undefined,
                campaign_id: campaignId || null,
              });
              setOpen(false);
              onChanged();
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          {t("post.save")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          {t("post.cancel")}
        </Button>
      </div>
    </div>
  );
}

function ClipCard({ clip, data, onChanged }: { clip: BoardClip; data: BoardData; onChanged: () => void }) {
  const t = useTranslations("publishing");
  const title = clip.title || clip.filename || clip.clip_id;
  return (
    <article className="overflow-hidden rounded-xl border bg-card">
      <div className="flex gap-3 p-3">
        <ClipCover src={clip.video_url ? getClipUrl(clip.video_url) : null} className="aspect-[9/16] w-20 shrink-0 rounded-lg" />
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-medium" title={title}>{title}</h3>
          <p className="truncate text-xs text-muted-foreground">
            {clip.source_title ? t("board.from", { source: clip.source_title }) : null}
            {clip.duration ? <> · {t("board.duration", { seconds: Math.round(clip.duration) })}</> : null}
            {clip.niche ? <> · {clip.niche}</> : null}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {clip.qc_status ? <>{t("board.qc", { status: clip.qc_status })} · </> : null}
            {clip.compliance.checked
              ? clip.compliance.compliant
                ? t("board.complianceOk")
                : t("board.complianceWarn")
              : t("board.complianceNone")}
          </p>
          {clip.hook_status ? (
            <p className="text-xs text-muted-foreground">
              {t("board.hook", { status: t(`hookStatus.${clip.hook_status}`) })}
            </p>
          ) : null}
          {clip.brief?.has_brief && clip.brief.status === "non_compliant" ? (
            <p className="text-xs font-medium text-red-600 dark:text-red-400">
              {t("board.briefNonCompliant")}
            </p>
          ) : clip.brief?.has_brief && clip.brief.status === "uncertain" ? (
            <p className="text-xs font-medium text-amber-600 dark:text-amber-400">
              {t("board.briefUncertain")}
            </p>
          ) : clip.brief?.has_brief && clip.brief.status === "compliant" ? (
            <p className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
              {t("board.briefCompliant")}
            </p>
          ) : null}
          <div className="mt-2 divide-y border-t">
            {PLATFORM_ORDER.map((platform) => (
              <ReadinessRow key={platform} platform={platform} readiness={clip.readiness[platform]} />
            ))}
          </div>
          <div className="mt-2">
            <a href={bundleUrl(clip.clip_id)} download>
              <Button size="sm" variant="outline">
                <Download className="mr-1 size-4" aria-hidden />
                {t("board.downloadBundle")}
              </Button>
            </a>
          </div>
        </div>
      </div>
      <div className="px-3 pb-3">
        {clip.posts.map((post) => (
          <PostRecord key={post.id} post={post} onChanged={onChanged} />
        ))}
        <MarkPostedForm clip={clip} data={data} onChanged={onChanged} />
      </div>
    </article>
  );
}

export function PublishingBoard({ data, onChanged }: { data: BoardData; onChanged: () => void }) {
  const t = useTranslations("publishing");
  return (
    <section aria-label={t("board.heading")}>
      <h2 className="mb-3 text-lg font-semibold">{t("board.heading")}</h2>
      {data.clips.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{t("board.empty")}</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {data.clips.map((clip) => (
            <ClipCard key={clip.clip_id} clip={clip} data={data} onChanged={onChanged} />
          ))}
        </div>
      )}
    </section>
  );
}
