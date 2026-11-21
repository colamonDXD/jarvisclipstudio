import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ChannelMonitorsSection } from "./channel-monitors-section";

const MONITOR = {
  id: "mon-1",
  channel_id: "UC1234567890abcdefghijkl",
  channel_title: "Speed Clips",
  source_input: "@speedclips",
  enabled: true,
  niche_override: null,
  music_mode: null,
  music_override: null,
  template_mode: "plain",
  creation_mode: "full_auto",
  target_clip_count: null,
  processing_mode: null,
  include_shorts: false,
  max_tasks_per_day: 3,
  campaign_id: "camp-1",
  brand_kit_id: "kit-9",
  last_fallback_at: null,
  last_polled_at: null,
  last_error: null,
  consecutive_failures: 0,
};

const VIDEOS = [
  {
    id: "vid-wait",
    title: "Big Premiere",
    channel_title: "Speed Clips",
    state: "waiting",
    skip_reason: null,
    task_id: null,
    attempts: 1,
    last_error: "Premiere — the video is not downloadable yet.",
  },
  {
    id: "vid-parked",
    title: "Unlucky Upload",
    channel_title: "Speed Clips",
    state: "parked",
    skip_reason: null,
    task_id: null,
    attempts: 3,
    last_error: "metadata exploded",
  },
  {
    id: "vid-skipped",
    title: "Over The Cap",
    channel_title: "Speed Clips",
    state: "skipped",
    skip_reason: "daily_cap",
    task_id: null,
    attempts: 0,
    last_error: null,
  },
  {
    id: "vid-queued",
    title: "Already Clipping",
    channel_title: "Speed Clips",
    state: "queued",
    skip_reason: null,
    task_id: "task-7",
    attempts: 1,
    last_error: null,
  },
];

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch() {
  const fetchMock = vi.fn(async (url: string, options?: { method?: string }) => {
    const method = options?.method ?? "GET";
    if (url === "/api/channel-monitors" && method === "GET") {
      return jsonResponse({
        settings: { enabled: true },
        monitors: [MONITOR],
        videos: VIDEOS,
      });
    }
    if (url === "/api/channel-monitors" && method === "POST") {
      return jsonResponse({ monitor: MONITOR }, 201);
    }
    if (url === "/api/channel-monitors/videos/vid-parked/retry" && method === "POST") {
      return jsonResponse({
        outcome: "queued",
        message: "Task created — it is in the queue now.",
        video: { ...VIDEOS[1], state: "queued", task_id: "task-9" },
      });
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function getCalls(fetchMock: ReturnType<typeof stubFetch>) {
  return (fetchMock.mock.calls as unknown as [string, { method?: string } | undefined][]).filter(
    ([url, options]) => url === "/api/channel-monitors" && (options?.method ?? "GET") === "GET",
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});

it("shows the waiting state and the saved campaign and brand kit", async () => {
  stubFetch();
  render(<ChannelMonitorsSection />);

  expect(await screen.findByText("Waiting for the video")).toBeInTheDocument();
  expect(screen.getByText(/Campaign camp-1/)).toBeInTheDocument();
  expect(screen.getByText(/Brand kit kit-9/)).toBeInTheDocument();
});

it("offers retry only on parked and skipped rows without a task", async () => {
  stubFetch();
  render(<ChannelMonitorsSection />);

  await screen.findByText("Waiting for the video");
  // Parked + skipped rows get a Retry button; waiting and queued
  // rows must not (waiting re-checks itself, queued already has one).
  expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(2);
  expect(screen.getByText("Open task")).toBeInTheDocument();
});

it("retries a parked video, shows the answer and reloads", async () => {
  const fetchMock = stubFetch();
  render(<ChannelMonitorsSection />);

  await screen.findByText("Waiting for the video");
  const getsBefore = getCalls(fetchMock).length;

  const [firstRetry] = screen.getAllByRole("button", { name: "Retry" });
  fireEvent.click(firstRetry);

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/channel-monitors/videos/vid-parked/retry",
      { method: "POST" },
    );
  });
  expect(
    await screen.findByText("Task created — it is in the queue now."),
  ).toBeInTheDocument();
  await waitFor(() => {
    expect(getCalls(fetchMock).length).toBeGreaterThan(getsBefore);
  });
});

it("sends campaign and brand kit ids when adding a channel", async () => {
  const fetchMock = stubFetch();
  render(<ChannelMonitorsSection />);

  fireEvent.change(await screen.findByLabelText("Channel URL or @handle"), {
    target: { value: "@speedclips" },
  });
  fireEvent.change(screen.getByLabelText("Campaign ID (optional)"), {
    target: { value: " camp-42 " },
  });
  fireEvent.change(screen.getByLabelText("Brand kit ID (optional)"), {
    target: { value: "kit-7" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add channel" }));

  await waitFor(() => {
    const calls = fetchMock.mock.calls as unknown as [
      string,
      { method?: string; body?: string } | undefined,
    ][];
    const post = calls.find(
      ([url, options]) =>
        url === "/api/channel-monitors" && options?.method === "POST",
    );
    expect(post).toBeDefined();
    const body = JSON.parse(String(post?.[1]?.body));
    expect(body.campaign_id).toBe("camp-42");
    expect(body.brand_kit_id).toBe("kit-7");
  });
});

it("sends null campaign and brand kit when the fields are blank", async () => {
  const fetchMock = stubFetch();
  render(<ChannelMonitorsSection />);

  fireEvent.change(await screen.findByLabelText("Channel URL or @handle"), {
    target: { value: "@speedclips" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add channel" }));

  await waitFor(() => {
    const calls = fetchMock.mock.calls as unknown as [
      string,
      { method?: string; body?: string } | undefined,
    ][];
    const post = calls.find(
      ([url, options]) =>
        url === "/api/channel-monitors" && options?.method === "POST",
    );
    const body = JSON.parse(String(post?.[1]?.body));
    expect(body.campaign_id).toBeNull();
    expect(body.brand_kit_id).toBeNull();
  });
});
