"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createCampaign, deleteCampaign, type Campaign } from "@/lib/publishing";

export function CampaignsPanel({
  campaigns,
  onChanged,
}: {
  campaigns: Campaign[];
  onChanged: () => void;
}) {
  const t = useTranslations("publishing");
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("");
  const [url, setUrl] = useState("");
  const [budget, setBudget] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await createCampaign({
        name: name.trim(),
        platform: platform.trim() || undefined,
        campaign_url: url.trim() || undefined,
        budget: budget.trim() === "" ? undefined : Number(budget),
        notes: notes.trim() || undefined,
      });
      setName("");
      setPlatform("");
      setUrl("");
      setBudget("");
      setNotes("");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label={t("campaigns.heading")}>
      <h2 className="mb-1 text-lg font-semibold">{t("campaigns.heading")}</h2>
      <p className="mb-3 text-sm text-muted-foreground">{t("campaigns.goldWindow")}</p>

      <div className="mb-4 grid gap-2 rounded-xl border bg-card p-3 sm:grid-cols-2">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t("campaigns.namePlaceholder")}
          aria-label={t("campaigns.name")}
        />
        <Input
          value={platform}
          onChange={(event) => setPlatform(event.target.value)}
          placeholder={t("campaigns.platform")}
          aria-label={t("campaigns.platform")}
        />
        <Input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder={t("campaigns.url")}
          aria-label={t("campaigns.url")}
        />
        <Input
          value={budget}
          inputMode="decimal"
          onChange={(event) => setBudget(event.target.value)}
          placeholder={t("campaigns.budget")}
          aria-label={t("campaigns.budget")}
        />
        <Input
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder={t("campaigns.notes")}
          aria-label={t("campaigns.notes")}
          className="sm:col-span-2"
        />
        {error ? <p className="text-xs text-red-500 sm:col-span-2">{error}</p> : null}
        <div>
          <Button onClick={() => void create()} disabled={busy || !name.trim()}>
            {t("campaigns.create")}
          </Button>
        </div>
      </div>

      {campaigns.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{t("campaigns.empty")}</p>
      ) : (
        <ul className="divide-y rounded-xl border bg-card">
          {campaigns.map((campaign) => (
            <li key={campaign.id} className="flex flex-wrap items-center justify-between gap-2 p-3">
              <div className="min-w-0">
                <p className="font-medium">
                  {campaign.name}
                  {campaign.platform ? (
                    <span className="text-muted-foreground"> · {campaign.platform}</span>
                  ) : null}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("campaigns.posts", { count: campaign.post_count ?? 0 })}
                  {campaign.entered_stats
                    ? ` · ${t("campaigns.enteredStats", {
                        views: campaign.entered_stats.views,
                        earnings: campaign.entered_stats.earnings,
                      })}`
                    : ""}
                  {campaign.notes ? ` · ${campaign.notes}` : ""}
                </p>
                {campaign.campaign_url ? (
                  <a className="text-xs underline" href={campaign.campaign_url} target="_blank" rel="noreferrer">
                    {campaign.campaign_url}
                  </a>
                ) : null}
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  await deleteCampaign(campaign.id);
                  onChanged();
                }}
              >
                {t("campaigns.delete")}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
