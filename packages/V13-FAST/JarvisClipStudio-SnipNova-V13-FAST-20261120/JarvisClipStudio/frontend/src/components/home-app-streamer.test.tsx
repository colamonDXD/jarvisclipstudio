import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const libraryTracks = [
  { filename: "calm.mp3", display_name: "calm", source: "bundled", duration_sec: 95 },
];

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("billing-summary")) return Response.json(billingSummary);
      if (url.includes("/api/music/library")) return Response.json({ tracks: libraryTracks });
      if (url.includes("/api/tasks/create")) return Response.json({ task_id: "task-1" });
      if (url.includes("/api/tasks")) return Response.json({ tasks: [] });
      if (url.includes("/api/fonts")) return Response.json({ fonts: [] });
      if (url.includes("caption-templates")) return Response.json({ templates: [] });
      if (url.includes("announcements")) return Response.json({ announcements: [] });
      if (url.includes("social")) return Response.json({ enabled: false });
      if (url.includes("service-status")) return Response.json({ status: "operational" });
      return Response.json({});
    })
  );
});

async function submitWithUrl() {
  fireEvent.change(screen.getByLabelText("YouTube, Twitch, or Kick URL"), {
    target: { value: "https://www.twitch.tv/videos/123456" },
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

it("shows the journey card with the parking warning in plain language", () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const card = screen.getByRole("button", { name: /Streamer Reaction/ });
  expect(card).toHaveAttribute("aria-pressed", "false");
  expect(card.textContent).toContain("Pauses and waits for you to pick the clips");
  expect(card.textContent).toContain("facecam");
});

it("applies exactly the preset's existing payload fields on submit", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const card = screen.getByRole("button", { name: /Streamer Reaction/ });
  fireEvent.click(card);
  expect(card).toHaveAttribute("aria-pressed", "true");

  const payload = await submitWithUrl();
  expect(payload.creation_mode).toBe("moments_ai");
  expect(payload.template_mode).toBe("auto");
  expect(payload.framing_mode).toBe("auto");
  expect(payload.niche_override).toBe("gaming");
  expect(payload.output_format).toBe("vertical");
});

it("without the journey, the create payload is not the streamer preset", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const payload = await submitWithUrl();
  expect(payload.creation_mode).toBe("full_auto");
  expect(payload.niche_override).toBeNull();
});
