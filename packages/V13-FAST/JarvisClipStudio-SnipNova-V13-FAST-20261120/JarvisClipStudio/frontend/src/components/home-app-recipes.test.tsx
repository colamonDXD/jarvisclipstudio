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

const savedRecipes = [
  { id: "recipe-1", name: "Podcast polish", settings: {} },
  { id: "recipe-2", name: "Gaming loud", settings: {} },
];

function stubFetch(recipes: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("billing-summary")) return Response.json(billingSummary);
      if (url.includes("/api/music/library")) return Response.json({ tracks: [] });
      if (url.includes("/api/recipes")) return Response.json({ recipes });
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
}

beforeEach(() => {
  // Radix Select scrolls the active option into view; jsdom lacks it.
  HTMLElement.prototype.scrollIntoView = vi.fn();
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

it("shows no recipe picker when the user has no saved recipes", async () => {
  stubFetch([]);
  render(<HomeApp />, { wrapper: TooltipProvider });

  await waitFor(() =>
    expect(
      vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/api/recipes"))
    ).toBe(true)
  );
  expect(screen.queryByRole("combobox", { name: "Recipe" })).not.toBeInTheDocument();

  const body = await submitWithUrl();
  expect(body).not.toHaveProperty("recipe_id");
});

it("sends the picked recipe's id as recipe_id, and omits it for No recipe", async () => {
  stubFetch(savedRecipes);
  render(<HomeApp />, { wrapper: TooltipProvider });

  const picker = await screen.findByRole("combobox", { name: "Recipe" });
  fireEvent.click(picker);
  fireEvent.click(await screen.findByRole("option", { name: "Podcast polish" }));

  const body = await submitWithUrl();
  expect(body.recipe_id).toBe("recipe-1");
});

it("clears the pick back to no recipe", async () => {
  stubFetch(savedRecipes);
  render(<HomeApp />, { wrapper: TooltipProvider });

  const picker = await screen.findByRole("combobox", { name: "Recipe" });
  fireEvent.click(picker);
  fireEvent.click(await screen.findByRole("option", { name: "Gaming loud" }));
  fireEvent.click(picker);
  fireEvent.click(await screen.findByRole("option", { name: "No recipe" }));

  const body = await submitWithUrl();
  expect(body).not.toHaveProperty("recipe_id");
});
