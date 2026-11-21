import { fireEvent, render, screen } from "@testing-library/react";
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

function summaryText() {
  return screen.getByLabelText("Run summary").textContent ?? "";
}

it("summarises the default choices before anything is picked", () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  const text = summaryText();
  expect(text).toContain("Before you generate:");
  expect(text).toContain("Mode: Full AutoClip");
  expect(text).toContain("Template: Plain — just cuts");
  expect(text).toContain("Framing: Auto");
  expect(text).toContain("Niche: Auto-detect");
  expect(text).toContain("Output: Auto 9:16");
  expect(text).not.toContain("Source:");
});

it("adds the source and follows control changes", () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.change(screen.getByLabelText("YouTube, Twitch, or Kick URL"), {
    target: { value: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" },
  });
  expect(summaryText()).toContain(
    "Source: https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  );

  fireEvent.click(screen.getByRole("button", { name: /Streamer Reaction/ }));
  const text = summaryText();
  expect(text).toContain("Mode: Find Moments + AI Editing");
  expect(text).toContain("Template: Auto template");
  expect(text).toContain("Niche: Gaming");
});
