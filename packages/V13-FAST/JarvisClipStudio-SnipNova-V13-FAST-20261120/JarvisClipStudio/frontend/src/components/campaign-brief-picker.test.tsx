import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { CampaignBriefPicker } from "./campaign-brief-picker";
import type { Campaign } from "@/lib/publishing";
import type { ContentBriefPayload } from "@/lib/content-brief";

const campaigns: Campaign[] = [
  {
    id: "camp-1",
    name: "Expedia x Speed",
    platform: "youtube_shorts",
    campaign_url: null,
    budget: 8000,
    notes: null,
    created_at: null,
    has_brief: true,
    example_url: "https://www.youtube.com/shorts/example",
  },
  {
    id: "camp-2",
    name: "Plain campaign",
    platform: "tiktok",
    campaign_url: null,
    budget: null,
    notes: null,
    created_at: null,
    has_brief: false,
    example_url: null,
  },
];

const storedBrief = {
  version: 2,
  requirements: [
    {
      id: "r1",
      type: "topic_focus",
      text: "Expedia must be a major focus",
      kind: "mandatory",
    },
  ],
  allowed_ranges: [
    { start_seconds: 10589, end_seconds: null, start_label: "2:56:29", end_label: null },
  ],
  example_url: "https://www.youtube.com/shorts/example",
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/publishing/campaigns") && !init?.method) {
      return Response.json({ campaigns });
    }
    if (url.includes("/api/publishing/campaigns/camp-1/brief")) {
      return Response.json({
        campaign_id: "camp-1",
        brief: storedBrief,
        has_brief: true,
        example_url: storedBrief.example_url,
      });
    }
    if (url.includes("/api/publishing/campaigns/camp-1") && init?.method === "PATCH") {
      return Response.json({ campaign: campaigns[0] });
    }
    return Response.json({});
  });
  vi.stubGlobal("fetch", fetchMock);
});

const currentBrief: ContentBriefPayload = {
  version: 2,
  requirements: [
    { type: "topic_focus", text: "Expedia must be a major focus", kind: "mandatory" },
  ],
  allowed_ranges: [{ start: "2:56:29", end: null }],
  example_url: "https://www.youtube.com/shorts/example",
};

function renderPicker(brief: ContentBriefPayload | null = currentBrief) {
  const onCampaignChange = vi.fn();
  render(
    <CampaignBriefPicker currentBrief={brief} onCampaignChange={onCampaignChange} />
  );
  return onCampaignChange;
}

it("renders nothing when the user has no campaigns", async () => {
  fetchMock.mockImplementation(async () => Response.json({ campaigns: [] }));
  const { container } = render(
    <CampaignBriefPicker currentBrief={null} onCampaignChange={vi.fn()} />
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

it("loads a campaign's saved brief into the panel on selection", async () => {
  const onCampaignChange = renderPicker();
  const select = await screen.findByLabelText("Campaign");
  expect(screen.getByText(/Expedia x Speed — has a saved brief/)).toBeInTheDocument();

  fireEvent.change(select, { target: { value: "camp-1" } });

  await waitFor(() => expect(onCampaignChange).toHaveBeenCalled());
  const [campaign, briefState] = onCampaignChange.mock.calls[0];
  expect(campaign.id).toBe("camp-1");
  expect(briefState.requirements).toHaveLength(1);
  expect(briefState.requirements[0].text).toBe("Expedia must be a major focus");
  expect(briefState.ranges).toEqual([
    expect.objectContaining({ start: "2:56:29", end: "" }),
  ]);
  expect(
    await screen.findByText(/Loaded the saved brief from Expedia x Speed/)
  ).toBeInTheDocument();
  expect(screen.getByText(/example video/)).toBeInTheDocument();
});

it("selecting a campaign without a brief does not fetch or load one", async () => {
  const onCampaignChange = renderPicker();
  const select = await screen.findByLabelText("Campaign");
  fireEvent.change(select, { target: { value: "camp-2" } });
  await waitFor(() => expect(onCampaignChange).toHaveBeenCalled());
  expect(onCampaignChange.mock.calls[0][0].id).toBe("camp-2");
  expect(onCampaignChange.mock.calls[0][1]).toBeNull();
  expect(
    fetchMock.mock.calls.some(([url]) => String(url).includes("/brief"))
  ).toBe(false);
});

it("saves the panel's current brief back to the campaign", async () => {
  renderPicker();
  const select = await screen.findByLabelText("Campaign");
  fireEvent.change(select, { target: { value: "camp-1" } });
  fireEvent.click(
    await screen.findByRole("button", { name: "Save this brief to the campaign" })
  );
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          String(url).includes("/api/publishing/campaigns/camp-1") &&
          init?.method === "PATCH"
      )
    ).toBe(true)
  );
  const [, init] = fetchMock.mock.calls.find(
    ([url, init]) =>
      String(url).includes("/api/publishing/campaigns/camp-1") &&
      init?.method === "PATCH"
  )!;
  expect(JSON.parse(String(init?.body))).toEqual({
    brief: currentBrief,
    example_url: currentBrief.example_url,
  });
  expect(await screen.findByText(/Brief saved to Expedia x Speed/)).toBeInTheDocument();
});
