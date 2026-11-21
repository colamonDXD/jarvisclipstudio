import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import HomeApp from "./home-app";

// The Layout picker (series B): ONE picker for every niche — Auto,
// Face on top (red / gold divider), Full screen, Crop — replacing the
// old Framing toggle plus Gaming-only layout row. Behaviours: the
// picker renders whatever the niche, a picked face layout sends
// layout_template (for any niche — proven for Gaming and for Podcast
// & news), the credit line travels trimmed and only when non-empty
// (and its input only appears once a face layout is picked), Auto
// (the default) sends no layout fields, and picking a face layout
// after Full screen resets framing to Auto (the layout wins over the
// framing choice).

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
  vi.stubGlobal("ResizeObserver", class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  // Radix Select scrolls the active option into view; jsdom lacks it.
  HTMLElement.prototype.scrollIntoView = vi.fn();
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

async function pickNiche(label: string) {
  fireEvent.click(screen.getByRole("combobox", { name: "Niche" }));
  fireEvent.click(await screen.findByRole("option", { name: label }));
}

async function submitWithUrl() {
  fireEvent.change(screen.getByLabelText("YouTube, Twitch, or Kick URL"), {
    target: { value: "https://www.twitch.tv/videos/2922436506" },
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

it("the Layout picker renders for any niche", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });
  // No niche picked yet: the picker is already there (it used to be
  // Gaming-only).
  expect(screen.getByRole("radiogroup", { name: "Layout" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Auto · Picks per clip" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Face on top · Red divider" })).toBeInTheDocument();

  await pickNiche("Gaming");
  expect(screen.getByRole("radiogroup", { name: "Layout" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Face on top · Gold divider" })).toBeInTheDocument();
}, 30000);

it("Face on top · Red divider sends the template and the credit line", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });
  await pickNiche("Gaming");
  fireEvent.click(await screen.findByRole("radio", { name: "Face on top · Red divider" }));
  fireEvent.change(screen.getByLabelText("Credit, e.g. @iShowSpeed"), {
    target: { value: "  @KaiCenat " },
  });

  const body = await submitWithUrl();
  expect(body.niche_override).toBe("gaming");
  expect(body.layout_template).toBe("facecam_red");
  expect(body.layout_credit).toBe("@KaiCenat");
}, 30000);

it("Face on top · Gold divider without a credit sends no credit field", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });
  await pickNiche("Gaming");
  fireEvent.click(await screen.findByRole("radio", { name: "Face on top · Gold divider" }));

  const body = await submitWithUrl();
  expect(body.layout_template).toBe("facecam_gold");
  expect(body).not.toHaveProperty("layout_credit");
}, 30000);

it("Auto (the default) sends no layout fields", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });
  await pickNiche("Gaming");

  const body = await submitWithUrl();
  expect(body).not.toHaveProperty("layout_template");
  expect(body).not.toHaveProperty("layout_credit");
}, 30000);

it("picking a face layout after Full screen resets framing to Auto", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });
  fireEvent.click(screen.getByRole("radio", { name: "Full screen · Whole picture" }));
  fireEvent.click(screen.getByRole("radio", { name: "Face on top · Red divider" }));

  const body = await submitWithUrl();
  expect(body.framing_mode).toBe("auto");
  expect(body.layout_template).toBe("facecam_red");
}, 30000);

it("layout_template travels for a non-gaming niche when a face-on-top card is picked", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });
  await pickNiche("Podcast & news");
  fireEvent.click(screen.getByRole("radio", { name: "Face on top · Gold divider" }));

  const body = await submitWithUrl();
  expect(body.niche_override).toBe("podcast_news");
  expect(body.layout_template).toBe("facecam_gold");
}, 30000);

it("credit input only appears after a face-on-top card is picked", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });
  // Default is Auto — no credit input.
  expect(screen.queryByLabelText("Credit, e.g. @iShowSpeed")).toBeNull();

  fireEvent.click(screen.getByRole("radio", { name: "Face on top · Red divider" }));
  expect(screen.getByLabelText("Credit, e.g. @iShowSpeed")).toBeInTheDocument();

  // Switch back to Auto — credit input disappears.
  fireEvent.click(screen.getByRole("radio", { name: "Auto · Picks per clip" }));
  expect(screen.queryByLabelText("Credit, e.g. @iShowSpeed")).toBeNull();
}, 30000);
