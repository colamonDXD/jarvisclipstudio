import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import HomeApp from "./home-app";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({
    data: { user: { id: "user-1", name: "Ada", email: "ada@example.com" } },
    isPending: false,
  }),
  signOut: vi.fn(),
}));

const billingSummary = {
  monetization_enabled: true,
  plan: "pro",
  subscription_status: "active",
  usage_count: 0,
  usage_limit: null,
  remaining: null,
  can_create_task: true,
  upgrade_required: false,
  reason: null,
  max_upload_bytes: 2_000_000_000,
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("billing-summary")) return Response.json(billingSummary);
      if (url.includes("/api/music/library")) return Response.json({ tracks: [] });
      if (url.includes("/api/tasks/create")) return Response.json({ task_id: "task-1" });
      if (url.includes("/api/tasks")) return Response.json({ tasks: [] });
      if (url.includes("/api/fonts")) return Response.json({ fonts: [] });
      if (url.includes("caption-templates")) return Response.json({ templates: [] });
      if (url.includes("announcements")) return Response.json({ announcements: [] });
      if (url.includes("social")) return Response.json({ enabled: false });
      if (url.includes("service-status")) return Response.json({ status: "operational" });
      if (url.endsWith("/api/publishing/campaigns")) {
        return Response.json({
          campaigns: [
            {
              id: "camp-1",
              name: "Expedia x Speed",
              platform: "youtube_shorts",
              campaign_url: null,
              budget: 8000,
              notes: null,
              created_at: null,
              has_brief: true,
              example_url: null,
            },
          ],
        });
      }
      if (url.includes("/api/publishing/campaigns/camp-1/brief")) {
        return Response.json({
          campaign_id: "camp-1",
          has_brief: true,
          example_url: null,
          brief: {
            version: 2,
            requirements: [
              {
                id: "r1",
                type: "topic_focus",
                text: "Expedia must be a major focus",
                kind: "mandatory",
              },
            ],
            allowed_ranges: [],
          },
        });
      }
      return Response.json({});
    })
  );
});

async function submitWithUrl() {
  fireEvent.change(screen.getByLabelText("YouTube, Twitch, or Kick URL"), {
    target: { value: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" },
  });
  const generate = screen.getByRole("button", { name: "Generate clips" });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  const fetchMock = vi.mocked(fetch);
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/api/tasks/create"))
    ).toBe(true)
  );
  const [, init] = fetchMock.mock.calls.find(([url]) =>
    String(url).includes("/api/tasks/create")
  )!;
  return JSON.parse(String(init?.body));
}

it("omits content_brief when the brief panel is untouched", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const body = await submitWithUrl();
  expect(body).not.toHaveProperty("content_brief");
  expect(body).not.toHaveProperty("campaign_id");
});

it("sends the exact brief built in the panel", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.click(
    await screen.findByRole("button", { name: /Add a campaign brief/ })
  );
  fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
  fireEvent.change(screen.getByLabelText("Requirement"), {
    target: { value: "Expedia must be a major focus of the content" },
  });
  // The panel renders one "Add range" button per ranges section (allowed
  // and never-use); this brief's range belongs to the allowed section, so
  // scope the query to that section via its heading.
  const allowedRangesSection = screen
    .getByRole("heading", { name: "Allowed parts of the video" })
    .closest("div")!;
  fireEvent.click(
    within(allowedRangesSection).getByRole("button", { name: "Add range" })
  );
  fireEvent.change(screen.getByLabelText("Range start"), {
    target: { value: "2:56:29" },
  });

  const body = await submitWithUrl();
  expect(body.content_brief).toEqual({
    version: 2,
    requirements: [
      {
        type: "topic_focus",
        text: "Expedia must be a major focus of the content",
        kind: "mandatory",
      },
    ],
    allowed_ranges: [{ start: "2:56:29", end: null }],
  });
}, 30000);

it("sends campaign_id and the campaign's loaded brief when a campaign is picked", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const select = await screen.findByLabelText("Campaign");
  fireEvent.change(select, { target: { value: "camp-1" } });
  expect(
    await screen.findByText(/Loaded the saved brief from Expedia x Speed/)
  ).toBeInTheDocument();

  const body = await submitWithUrl();
  expect(body.campaign_id).toBe("camp-1");
  expect(body.content_brief).toEqual({
    version: 2,
    requirements: [
      {
        type: "topic_focus",
        text: "Expedia must be a major focus",
        kind: "mandatory",
      },
    ],
    allowed_ranges: [],
  });
});
