import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { ProductionAnalytics } from "./production-analytics";
import type { AnalyticsData } from "@/lib/publishing";

const emptyData: AnalyticsData = {
  production: {
    clips_total: 0,
    tasks_total: 0,
    clips_by_day: [],
    niche_counts: [],
    qc_distribution: { pass: 0, warn: 0, fail: 0, none: 0 },
    compliance: { checked_clips: 0, compliant_clips: 0, rate: null },
    hook_distribution: [],
    stage_timings: {},
    token_totals: { calls: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, unknown_usage_calls: 0 },
  },
  performance: {
    entered_by_user: true,
    posts_total: 0,
    posts_measured: 0,
    totals: { views: 0, likes: 0, comments: 0, earnings: 0 },
    per_clip: [],
    per_campaign: [],
  },
};

const filledData: AnalyticsData = {
  production: {
    clips_total: 4,
    tasks_total: 3,
    clips_by_day: [{ date: "2026-11-08", clips: 4 }],
    niche_counts: [{ niche: "gaming", clips: 4 }],
    qc_distribution: { pass: 3, warn: 1, fail: 0, none: 0 },
    compliance: { checked_clips: 3, compliant_clips: 2, rate: 0.6667 },
    hook_distribution: [{ status: "cold_opened", clips: 2 }],
    stage_timings: { analysis_seconds: { mean_seconds: 40, tasks: 2 } },
    token_totals: { calls: 9, prompt_tokens: 1000, completion_tokens: 250, total_tokens: 1250, unknown_usage_calls: 1 },
  },
  performance: {
    entered_by_user: true,
    posts_total: 2,
    posts_measured: 1,
    totals: { views: 1500, likes: 90, comments: 15, earnings: 25.5 },
    per_clip: [
      {
        post_id: "p1",
        clip_id: "c1",
        title: "First",
        platform: "youtube_shorts",
        post_url: "https://youtube.com/shorts/a",
        posted_at: "2026-11-08T12:00:00Z",
        campaign_id: "camp-1",
        campaign_name: "Whop Push",
        stats: { post_id: "p1", views: 1500, likes: 90, comments: 15, earnings: 25.5, updated_at: null },
      },
      {
        post_id: "p2",
        clip_id: "c2",
        title: "Second",
        platform: "tiktok",
        post_url: "",
        posted_at: "2026-11-08T13:00:00Z",
        campaign_id: null,
        campaign_name: null,
        stats: null,
      },
    ],
    per_campaign: [
      { campaign_id: "camp-1", campaign_name: "Whop Push", posts: 1, views: 1500, likes: 90, comments: 15, earnings: 25.5 },
    ],
  },
};

it("shows honest empty states with no clips and no posts", () => {
  render(<ProductionAnalytics data={emptyData} />);
  expect(screen.getByText(/No clips produced yet/)).toBeInTheDocument();
  expect(screen.getByText(/No posted clips yet/)).toBeInTheDocument();
});

it("renders recorded production facts and user-entered performance", () => {
  render(<ProductionAnalytics data={filledData} />);
  expect(screen.getByText("Performance (entered by you)")).toBeInTheDocument();
  expect(screen.getByText(/Workflow compliance: 2 of 3 checked clips/)).toBeInTheDocument();
  expect(screen.getAllByText("1500").length).toBeGreaterThan(0);
  expect(screen.getByText(/Not measured yet/)).toBeInTheDocument();
  expect(screen.getAllByText("Whop Push").length).toBeGreaterThan(0);
  expect(screen.getByText(/AI tokens used across your generations: 1250/)).toBeInTheDocument();
});
