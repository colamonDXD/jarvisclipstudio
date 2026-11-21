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

it("renders the clip-count selector on Auto by default", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const chip = await screen.findByRole("button", { name: "Number of clips" });
  expect(chip).toHaveTextContent("Auto clips");
}, 30000);

it("omits target_clip_count when the count stays on Auto", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const body = await submitWithUrl();
  expect(body).not.toHaveProperty("target_clip_count");
}, 30000);

it("sends target_clip_count when 30 clips are picked", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.click(await screen.findByRole("button", { name: "Number of clips" }));
  fireEvent.click(await screen.findByRole("radio", { name: "30 clips" }));

  const body = await submitWithUrl();
  expect(body.target_clip_count).toBe(30);
}, 30000);
