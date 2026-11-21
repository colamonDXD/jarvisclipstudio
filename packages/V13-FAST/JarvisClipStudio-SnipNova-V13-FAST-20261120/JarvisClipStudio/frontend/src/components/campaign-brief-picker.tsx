"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";

import {
  fetchCampaignBrief,
  saveCampaignBrief,
  storedBriefToPanelState,
  type BriefPanelState,
  type ContentBriefPayload,
} from "@/lib/content-brief";
import { fetchCampaigns, type Campaign } from "@/lib/publishing";

interface CampaignBriefPickerProps {
  disabled?: boolean;
  /** The exact brief payload the panel currently reports (for save-back). */
  currentBrief: ContentBriefPayload | null;
  /** A campaign was picked; `briefState` is set when its saved brief
   *  should be loaded into the panel. */
  onCampaignChange: (
    campaign: Campaign | null,
    briefState: BriefPanelState | null
  ) => void;
}

/**
 * Create-page campaign picker (Content Requirements brief library).
 * Picking a campaign records it for the create payload; when the
 * campaign has a saved brief, the brief is loaded into the brief panel
 * for review/edit — edits affect this generation only unless the user
 * explicitly saves them back here.
 */
export function CampaignBriefPicker({
  disabled,
  currentBrief,
  onCampaignChange,
}: CampaignBriefPickerProps) {
  const t = useTranslations("home");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchCampaigns()
      .then((list) => {
        if (!cancelled) setCampaigns(list);
      })
      .catch(() => {
        // No campaign list (backend down, signed out) — the picker just
        // stays empty and the brief panel works standalone.
        if (!cancelled) setCampaigns([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = campaigns.find((campaign) => campaign.id === selectedId) ?? null;

  const handleSelect = async (campaignId: string) => {
    setSelectedId(campaignId);
    setMessage(null);
    setError(null);
    const campaign = campaigns.find((item) => item.id === campaignId) ?? null;
    if (!campaign) {
      onCampaignChange(null, null);
      return;
    }
    if (!campaign.has_brief) {
      onCampaignChange(campaign, null);
      return;
    }
    setLoading(true);
    try {
      const response = await fetchCampaignBrief(campaign.id);
      if (response.brief) {
        onCampaignChange(campaign, storedBriefToPanelState(response.brief));
        setMessage(t("contentBrief.campaignLoaded", { name: campaign.name }));
      } else {
        onCampaignChange(campaign, null);
      }
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : t("contentBrief.campaignLoadFailed")
      );
      onCampaignChange(campaign, null);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveBack = async () => {
    if (!selected) return;
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      await saveCampaignBrief(
        selected.id,
        currentBrief,
        currentBrief?.example_url ?? null
      );
      setCampaigns((current) =>
        current.map((campaign) =>
          campaign.id === selected.id
            ? { ...campaign, has_brief: currentBrief !== null }
            : campaign
        )
      );
      setMessage(t("contentBrief.campaignSaved", { name: selected.name }));
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : t("contentBrief.campaignSaveFailed")
      );
    } finally {
      setSaving(false);
    }
  };

  if (!campaigns.length) return null;

  return (
    <section className="rounded-2xl border bg-background p-4 shadow-[0_1px_2px_rgb(0_0_0/0.04)]">
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor="brief-campaign-picker"
          className="text-sm font-medium"
        >
          {t("contentBrief.campaignLabel")}
        </label>
        <select
          id="brief-campaign-picker"
          value={selectedId}
          disabled={disabled || loading}
          onChange={(event) => void handleSelect(event.target.value)}
          className="h-9 min-w-0 flex-1 rounded-lg border bg-transparent px-2.5 text-sm outline-none disabled:opacity-50 sm:flex-none sm:min-w-56"
        >
          <option value="">{t("contentBrief.campaignNone")}</option>
          {campaigns.map((campaign) => (
            <option key={campaign.id} value={campaign.id}>
              {campaign.name} —{" "}
              {campaign.has_brief
                ? t("contentBrief.campaignHasBrief")
                : t("contentBrief.campaignNoBrief")}
            </option>
          ))}
        </select>
        {loading && <Loader2 className="size-4 animate-spin" />}
        {selected && (
          <button
            type="button"
            onClick={() => void handleSaveBack()}
            disabled={disabled || saving}
            className="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
          >
            {saving && <Loader2 className="size-3.5 animate-spin" />}
            {t("contentBrief.saveToCampaign")}
          </button>
        )}
      </div>
      {selected?.example_url && (
        <p className="mt-2 text-xs text-muted-foreground">
          {t("contentBrief.campaignExampleNote")}
        </p>
      )}
      {message && (
        <p role="status" className="mt-2 text-xs text-muted-foreground">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </section>
  );
}
