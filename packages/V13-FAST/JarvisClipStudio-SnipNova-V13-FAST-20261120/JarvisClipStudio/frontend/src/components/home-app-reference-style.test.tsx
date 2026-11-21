/**
 * Reference styles on the create page: the two ways a saved style can end
 * up as the task's niche_override, and the payload each must produce.
 *
 * The regression test is the live Nov 9 failure: after the card created a
 * style it auto-selected it (setNicheOverride("user-funky")), and the
 * niche Select — whose dropdown had never opened — fired
 * onValueChange("") and clobbered the state, so Generate sent
 * niche_override: "" and the backend 422'd with
 * "Invalid niche_override ''". Picking the same style from the opened
 * dropdown always worked, which is why the bug only bit the
 * create-then-generate flow.
 */
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

const styleSummary = {
  name: "user-funky",
  display_name: "funky",
  base_niche: "japan_travel_cinematic",
  source_file: "ref.mp4",
  headline: {},
};

let stylesList: unknown[] = [];

beforeEach(() => {
  HTMLElement.prototype.scrollIntoView = vi.fn();
  stylesList = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("billing-summary")) return Response.json(billingSummary);
      if (url.includes("/api/music/library")) return Response.json({ tracks: [] });
      if (url.includes("/api/reference-styles/from-url")) {
        stylesList = [styleSummary];
        return Response.json({ ...styleSummary, inferred: {}, not_inferred: [] }, { status: 201 });
      }
      if (url.includes("/api/reference-styles")) return Response.json({ profiles: stylesList });
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

it("picking an existing reference style from the niche dropdown sends its user- name", async () => {
  stylesList = [styleSummary];
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.click(screen.getByRole("combobox", { name: "Niche" }));
  fireEvent.click(await screen.findByRole("option", { name: /funky/ }));

  const body = await submitWithUrl();
  expect(body.niche_override).toBe("user-funky");
}, 30000);

it("regression: creating a style in the card then generating sends the style, not an empty override", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  fireEvent.change(screen.getByLabelText("Style name"), { target: { value: "funky" } });
  fireEvent.change(screen.getByLabelText("Reference video link"), {
    target: { value: "https://www.youtube.com/shorts/abc123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create style" }));
  await screen.findByText(/Style created/);

  const body = await submitWithUrl();
  expect(body.niche_override).toBe("user-funky");
}, 30000);
