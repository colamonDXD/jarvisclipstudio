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
  vi.stubGlobal("ResizeObserver", class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
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

it("defaults to Auto framing and always sends framing_mode", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  // LayoutPicker renders a radiogroup; the Auto card is selected by default.
  expect(screen.getByRole("radio", { name: "Auto · Picks per clip" })).toHaveAttribute(
    "aria-checked",
    "true"
  );
  expect(screen.getByRole("radio", { name: "Full screen · Whole picture" })).toHaveAttribute(
    "aria-checked",
    "false"
  );

  const body = await submitWithUrl();
  expect(body.framing_mode).toBe("auto");
  // The default template is Plain: the layout control must stay live
  // there (Plain + Full screen is the combination this control exists for).
  expect(body.template_mode).toBe("plain");
}, 30000);

it("keeps the framing control enabled while Plain is picked", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  expect(screen.getByRole("radio", { name: "Plain — just cuts" })).toHaveAttribute(
    "aria-checked",
    "true"
  );
  expect(screen.getByRole("radio", { name: "Full screen · Whole picture" })).toBeEnabled();
  expect(screen.getByRole("radio", { name: "Crop · Zoom on the face" })).toBeEnabled();
}, 30000);

it("sends framing_mode fit when Full screen is picked", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.click(screen.getByRole("radio", { name: "Full screen · Whole picture" }));

  const body = await submitWithUrl();
  expect(body.framing_mode).toBe("fit");
  expect(body.template_mode).toBe("plain");
  // Full screen is not a face-on-top card: no layout_template.
  expect(body).not.toHaveProperty("layout_template");
}, 30000);

it("sends framing_mode crop when Crop is picked", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.click(screen.getByRole("radio", { name: "Crop · Zoom on the face" }));

  const body = await submitWithUrl();
  expect(body.framing_mode).toBe("crop");
  expect(body).not.toHaveProperty("layout_template");
}, 30000);

it("picking Face on top · gold sends layout_template facecam_gold and framing_mode auto", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.click(screen.getByRole("radio", { name: "Face on top · Gold divider" }));

  const body = await submitWithUrl();
  expect(body.layout_template).toBe("facecam_gold");
  expect(body.framing_mode).toBe("auto");
});

it("picking Face on top · red sends layout_template facecam_red and framing_mode auto", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.click(screen.getByRole("radio", { name: "Face on top · Red divider" }));

  const body = await submitWithUrl();
  expect(body.layout_template).toBe("facecam_red");
  expect(body.framing_mode).toBe("auto");
});
