// home-app-template.test.tsx — updated for round 1 UI.
// Adds the "Title on clips" switch assertions (hook_title_enabled field).
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

it("defaults to Plain — just cuts and always sends template_mode", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  expect(screen.getByRole("radio", { name: "Plain — just cuts" })).toHaveAttribute(
    "aria-checked",
    "true"
  );

  const body = await submitWithUrl();
  expect(body.template_mode).toBe("plain");
}, 30000);

it("disables the music controls while Plain is picked", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  // Plain treats every music control as Off in the pipeline, so the
  // controls themselves are inert: the mode group and the track picker.
  expect(screen.getByRole("radio", { name: "Off" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Music" })).toBeDisabled();

  fireEvent.click(screen.getByRole("radio", { name: "Auto template" }));
  expect(screen.getByRole("radio", { name: "Off" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Music" })).toBeEnabled();
});

it("sends template_mode auto when Auto template is picked", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.click(screen.getByRole("radio", { name: "Auto template" }));

  const body = await submitWithUrl();
  expect(body.template_mode).toBe("auto");
});

it("Title on clips switch is ON by default and sends hook_title_enabled true", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const titleSwitch = screen.getByRole("switch", { name: "Title on clips" });
  expect(titleSwitch).toBeChecked();

  const body = await submitWithUrl();
  expect(body.hook_title_enabled).toBe(true);
});

it("Title on clips switch OFF sends hook_title_enabled false", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const titleSwitch = screen.getByRole("switch", { name: "Title on clips" });
  expect(titleSwitch).toBeChecked();
  fireEvent.click(titleSwitch);
  expect(titleSwitch).not.toBeChecked();

  const body = await submitWithUrl();
  expect(body.hook_title_enabled).toBe(false);
});
