"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { CampaignsPanel } from "@/components/app/campaigns-panel";
import { PageLoading } from "@/components/app/page-state";
import { ProductionAnalytics } from "@/components/app/production-analytics";
import { PublishingBoard } from "@/components/app/publishing-board";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  fetchAnalytics,
  fetchBoard,
  type AnalyticsData,
  type BoardData,
} from "@/lib/publishing";

export default function PublishingPage() {
  const t = useTranslations("publishing");
  const [board, setBoard] = useState<BoardData | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [boardData, analyticsData] = await Promise.all([
        fetchBoard(),
        fetchAnalytics(),
      ]);
      setBoard(boardData);
      setAnalytics(analyticsData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("loadFailed"));
    }
  }, [t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <main className="mx-auto w-full max-w-5xl space-y-10 px-4 py-8 sm:px-8">
      <header>
        <h1 className="font-display text-2xl font-bold tracking-tight">{t("title")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{t("subtitle")}</p>
      </header>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {!board && !error ? (
        <PageLoading />
      ) : board ? (
        <>
          <PublishingBoard data={board} onChanged={() => void reload()} />
          <CampaignsPanel campaigns={board.campaigns} onChanged={() => void reload()} />
          {analytics ? <ProductionAnalytics data={analytics} /> : null}
        </>
      ) : null}
    </main>
  );
}
