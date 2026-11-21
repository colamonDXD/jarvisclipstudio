"use client";

import { useTranslations } from "next-intl";

import type { AnalyticsData } from "@/lib/publishing";

const HEADING = "mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground";

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function CountList({ rows }: { rows: { label: string; count: number }[] }) {
  if (rows.length === 0) return null;
  return (
    <ul className="divide-y rounded-xl border bg-card">
      {rows.map((row) => (
        <li key={row.label} className="flex items-center justify-between p-3 text-sm">
          <span>{row.label}</span>
          <span className="tabular-nums text-muted-foreground">{row.count}</span>
        </li>
      ))}
    </ul>
  );
}

export function ProductionAnalytics({ data }: { data: AnalyticsData }) {
  const t = useTranslations("publishing");
  const { production, performance } = data;

  return (
    <section aria-label={t("analytics.heading")} className="space-y-8">
      <div>
        <h2 className="mb-3 text-lg font-semibold">{t("analytics.productionHeading")}</h2>
        {production.clips_total === 0 ? (
          <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
            {t("analytics.noClips")}
          </p>
        ) : (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile label={t("analytics.clipsProduced")} value={String(production.clips_total)} />
              <StatTile label={t("analytics.tasksTotal")} value={String(production.tasks_total)} />
              <StatTile
                label={t("analytics.qcHeading")}
                value={`${production.qc_distribution.pass ?? 0} / ${production.qc_distribution.warn ?? 0} / ${production.qc_distribution.fail ?? 0}`}
              />
              <StatTile
                label={t("analytics.complianceLabel")}
                value={
                  production.compliance.checked_clips > 0
                    ? `${production.compliance.compliant_clips} / ${production.compliance.checked_clips}`
                    : "–"
                }
              />
            </div>
            {production.compliance.rate !== null ? (
              <p className="text-sm text-muted-foreground">
                {t("analytics.compliance", {
                  compliant: production.compliance.compliant_clips,
                  checked: production.compliance.checked_clips,
                })}
              </p>
            ) : null}
            <div className="grid gap-6 md:grid-cols-2">
              <div>
                <h3 className={HEADING}>{t("analytics.perDay")}</h3>
                <CountList
                  rows={production.clips_by_day.map((row) => ({ label: row.date, count: row.clips }))}
                />
              </div>
              <div>
                <h3 className={HEADING}>{t("analytics.perNiche")}</h3>
                <CountList
                  rows={production.niche_counts.map((row) => ({ label: row.niche, count: row.clips }))}
                />
              </div>
              <div>
                <h3 className={HEADING}>{t("analytics.hooksHeading")}</h3>
                <CountList
                  rows={production.hook_distribution.map((row) => ({
                    label: t(`hookStatus.${row.status}`),
                    count: row.clips,
                  }))}
                />
              </div>
              <div>
                <h3 className={HEADING}>{t("analytics.stageTimings")}</h3>
                <CountList
                  rows={Object.entries(production.stage_timings).map(([stage, info]) => ({
                    label: stage.replace(/_seconds$/, "").replace(/_/g, " "),
                    count: info.mean_seconds,
                  }))}
                />
              </div>
            </div>
            <p className="text-sm text-muted-foreground">
              {t("analytics.tokens", {
                total: production.token_totals.total_tokens,
                calls: production.token_totals.calls,
              })}
            </p>
          </div>
        )}
      </div>

      <div>
        <h2 className="mb-3 text-lg font-semibold">{t("analytics.performanceHeading")}</h2>
        {performance.posts_total === 0 ? (
          <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
            {t("analytics.noPosts")}
          </p>
        ) : (
          <div className="space-y-4">
            {performance.posts_measured === 0 ? (
              <p className="text-sm text-muted-foreground">{t("analytics.noStats")}</p>
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatTile label={t("post.views")} value={String(performance.totals.views)} />
                <StatTile label={t("post.likes")} value={String(performance.totals.likes)} />
                <StatTile label={t("post.comments")} value={String(performance.totals.comments)} />
                <StatTile label={t("post.earnings")} value={String(performance.totals.earnings)} />
              </div>
            )}
            <h3 className={HEADING}>{t("analytics.perClip")}</h3>
            <ul className="divide-y rounded-xl border bg-card">
              {performance.per_clip.map((row) => (
                <li key={row.post_id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
                  <span className="min-w-0">
                    <span className="font-medium">{row.title || row.clip_id}</span>
                    <span className="text-muted-foreground">
                      {" "}· {t(`platforms.${row.platform}`)}
                      {row.campaign_name ? ` · ${row.campaign_name}` : ""}
                    </span>
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {row.stats
                      ? `${row.stats.views ?? "–"} ${t("post.views").toLowerCase()} · ${row.stats.likes ?? "–"} ${t("post.likes").toLowerCase()} · ${row.stats.earnings ?? "–"}`
                      : t("analytics.unmeasured")}
                  </span>
                </li>
              ))}
            </ul>
            {performance.per_campaign.length > 0 ? (
              <>
                <h3 className={HEADING}>{t("analytics.perCampaign")}</h3>
                <CountList
                  rows={performance.per_campaign.map((bucket) => ({
                    label: bucket.campaign_name || "—",
                    count: bucket.views,
                  }))}
                />
              </>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}
