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
  last_polled_at: null,
  last_error: null,
  consecutive_failures: 0,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(monitors: unknown[]) {
  const fetchMock = vi.fn(async (url: string, options?: { method?: string }) => {
    const method = options?.method ?? "GET";
    if (url === "/api/channel-monitors" && method === "GET") {
      return jsonResponse({
        settings: { enabled: true },
        monitors,
        videos: [
          {
            id: "vid-1",
            title: "Old stream VOD",
            channel_title: "Speed Clips",
            state: "baseline",
            skip_reason: "baseline",
            task_id: null,
            attempts: 0,
            last_error: null,
          },
        ],
      });
    }
    if (url === "/api/channel-monitors" && method === "POST") {
      return jsonResponse({ monitor: MONITOR }, 201);
    }
    if (url === "/api/channel-monitors/mon-1" && method === "DELETE") {
      monitors = [];
      return jsonResponse({ deleted: true });
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});

it("renders the baseline copy and existing activity", async () => {
  stubFetch([MONITOR]);
  render(<ChannelMonitorsSection />);

  // Once in the channel list, once as the activity row's channel name.
  expect((await screen.findAllByText("Speed Clips")).length).toBe(2);
  // The add form spells out the no-backfill rule before he commits.
  expect(
    screen.getByText(/only marked as seen — they are never clipped/),
  ).toBeInTheDocument();
  expect(screen.getByText("Also clip the newest video now")).toBeInTheDocument();
  // Baseline activity is explained in plain words.
  expect(
    await screen.findByText("Already on the channel when monitoring started."),
  ).toBeInTheDocument();
});

it("adds a channel without clipping the newest video by default", async () => {
  const fetchMock = stubFetch([]);
  render(<ChannelMonitorsSection />);

  const input = await screen.findByLabelText("Channel URL or @handle");
  fireEvent.change(input, { target: { value: "@speedclips" } });
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
    expect(body.source_input).toBe("@speedclips");
    expect(body.clip_newest_now).toBe(false);
    expect(body.template_mode).toBe("plain");
    expect(body.creation_mode).toBe("full_auto");
  });
});

it("removes a channel through the BFF delete route", async () => {
  const fetchMock = stubFetch([MONITOR]);
  render(<ChannelMonitorsSection />);

  fireEvent.click(await screen.findByRole("button", { name: "Remove" }));

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith("/api/channel-monitors/mon-1", {
      method: "DELETE",
    });
  });
  // The channel list empties (its past activity rows stay, by design).
  expect(
    await screen.findByText(/No channels yet\. Add one above/),
  ).toBeInTheDocument();
});
