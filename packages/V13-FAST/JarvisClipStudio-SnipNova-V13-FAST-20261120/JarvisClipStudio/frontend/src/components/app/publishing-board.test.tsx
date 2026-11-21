import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { PublishingBoard } from "./publishing-board";
import type { BoardData } from "@/lib/publishing";

const ready = (platform: string) => ({
  platform,
  status: "ready" as const,
  checks: [{ name: "aspect", status: "pass" as const, detail: "Vertical" }],
});

const filledBoard: BoardData = {
  campaigns: [{ id: "camp-1", name: "Whop Push", platform: "", campaign_url: null, budget: null, notes: null, created_at: null }],
  clips: [
    {
      clip_id: "clip-1",
      task_id: "task-1",
      title: "He said what?!",
      filename: "clip_0.mp4",
      source_title: "Big stream",
      duration: 42,
      width: 1080,
      height: 1920,
      niche: "gaming",
      qc_status: "pass",
      captions_present: true,
      hook_status: "cold_opened",
      created_at: "2026-11-08T10:00:00Z",
      video_url: "/tasks/task-1/clips/clip-1/file",
      compliance: { checked: true, compliant: true, checks: { hook_window: "pass" } },
      readiness: {
        youtube_shorts: ready("youtube_shorts"),
        instagram_reels: ready("instagram_reels"),
        tiktok: {
          platform: "tiktok",
          status: "blocked",
          checks: [{ name: "quality", status: "fail", detail: "Quality control failed this clip" }],
        },
      },
      posts: [
        {
          id: "post-1",
          clip_id: "clip-1",
          platform: "youtube_shorts",
          post_url: "https://youtube.com/shorts/a",
          posted_at: "2026-11-08T12:00:00Z",
          campaign_id: "camp-1",
          campaign_name: "Whop Push",
          submission_url: null,
          submission_dismissed: false,
          needs_submission: true,
          stats: null,
        },
      ],
    },
  ],
};

it("shows an honest empty state when there are no clips", () => {
  render(<PublishingBoard data={{ clips: [], campaigns: [] }} onChanged={() => {}} />);
  expect(screen.getByText(/No finished clips yet/)).toBeInTheDocument();
});

it("renders readiness, the Whop submission reminder and entered-stats form", () => {
  render(<PublishingBoard data={filledBoard} onChanged={() => {}} />);
  expect(screen.getByText("He said what?!")).toBeInTheDocument();
  expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
  expect(screen.getByText("Not ready")).toBeInTheDocument();
  // The unsubmitted post must carry the 30-minute Whop reminder.
  expect(screen.getByText(/Content Rewards links are due within 30 minutes/)).toBeInTheDocument();
  expect(screen.getByText(/Results \(entered by you\)/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Download bundle/ })).toHaveAttribute(
    "href",
    "/api/publishing/clips/clip-1/bundle",
  );
});

it("opens the mark-as-posted form with campaign choices", () => {
  render(<PublishingBoard data={filledBoard} onChanged={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Mark as posted" }));
  expect(screen.getByPlaceholderText("Paste the link to your post")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Whop Push" })).toBeInTheDocument();
});

it("saves a submission link through the API", async () => {
  const onChanged = vi.fn();
  const fetchMock = vi.fn(async () =>
    Response.json({ post: { id: "post-1" } }, { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<PublishingBoard data={filledBoard} onChanged={onChanged} />);
  fireEvent.change(screen.getByLabelText("Campaign submission link"), {
    target: { value: "https://whop.example/submit/1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add submission link" }));
  await screen.findByText("He said what?!");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/publishing/posts/post-1",
    expect.objectContaining({ method: "PATCH" }),
  );
  vi.unstubAllGlobals();
});

it("flags brief compliance per clip on the board", () => {
  const briefBoard: BoardData = {
    ...filledBoard,
    clips: filledBoard.clips.map((clip) => ({
      ...clip,
      brief: {
        has_brief: true,
        status: "non_compliant",
        mandatory_failed: ["r1"],
        mandatory_uncertain: [],
      },
    })),
  };
  const { rerender } = render(<PublishingBoard data={briefBoard} onChanged={() => {}} />);
  expect(
    screen.getByText("Brief: non-compliant — do not post for this campaign")
  ).toBeInTheDocument();

  const uncertainBoard: BoardData = {
    ...filledBoard,
    clips: filledBoard.clips.map((clip) => ({
      ...clip,
      brief: {
        has_brief: true,
        status: "uncertain",
        mandatory_failed: [],
        mandatory_uncertain: ["r2"],
      },
    })),
  };
  rerender(<PublishingBoard data={uncertainBoard} onChanged={() => {}} />);
  expect(
    screen.getByText("Brief: uncertain — review the compliance report first")
  ).toBeInTheDocument();

  rerender(<PublishingBoard data={filledBoard} onChanged={() => {}} />);
  expect(screen.queryByText(/Brief: non-compliant/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Brief: uncertain/)).not.toBeInTheDocument();
});
