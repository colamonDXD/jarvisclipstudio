import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { HookVariantsStrip } from "./hook-variants";
import type { HookPreview } from "@/lib/hook-variants";

const batch: HookPreview[] = [
  {
    id: "pv-current",
    task_id: "task-1",
    clip_id: "clip-1",
    batch_id: "batch-1",
    variant: "current",
    label: "Current opening",
    description: "The opening exactly as this clip starts now.",
    duration: 6.0,
    video_url: "/tasks/task-1/clips/clip-1/hook-variants/pv-current/file",
    created_at: "2026-11-08T00:00:00Z",
    selected: true,
  },
  {
    id: "pv-cold",
    task_id: "task-1",
    clip_id: "clip-1",
    batch_id: "batch-1",
    variant: "cold_open",
    label: "Cold open — strongest moment first",
    description: "Jumps straight to the strongest moment at 0:05, then plays the start.",
    duration: 6.0,
    video_url: "/tasks/task-1/clips/clip-1/hook-variants/pv-cold/file",
    created_at: "2026-11-08T00:00:00Z",
    selected: false,
  },
  {
    id: "pv-punch",
    task_id: "task-1",
    clip_id: "clip-1",
    batch_id: "batch-1",
    variant: "punch",
    label: "Strong punch-in opening",
    description: "Same start as now, but the first line gets a strong zoom punch-in.",
    duration: 6.0,
    video_url: "/tasks/task-1/clips/clip-1/hook-variants/pv-punch/file",
    created_at: "2026-11-08T00:00:00Z",
    selected: false,
  },
];

let generated: boolean;
let generateError: string | null;

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = decodeURIComponent(String(input));
      const method = init?.method ?? "GET";
      if (method === "POST" && url.endsWith("/hook-variants")) {
        if (generateError) {
          return Response.json({ detail: generateError }, { status: 500 });
        }
        generated = true;
        return Response.json(
          { clip_id: "clip-1", job_id: "hook-previews:clip-1:abc", status: "queued" },
          { status: 202 },
        );
      }
      if (method === "GET" && url.includes("/hook-variants/hook-previews:")) {
        return Response.json({
          job_id: "hook-previews:clip-1:abc",
          clip_id: "clip-1",
          status: "completed",
          previews: batch,
        });
      }
      if (method === "POST" && url.endsWith("/select")) {
        return Response.json(
          {
            clip_id: "clip-1",
            job_id: "clip-rerender:clip-1:xyz",
            status: "queued",
            hook_variant: "cold_open",
          },
          { status: 202 },
        );
      }
      if (method === "GET" && url.includes("/re-render/clip-rerender:")) {
        return Response.json({
          job_id: "clip-rerender:clip-1:xyz",
          clip_id: "clip-1",
          status: "completed",
          clip: { id: "clip-1", hook_variant: "cold_open", hook_variant_batch: "batch-1" },
        });
      }
      if (method === "GET" && url.endsWith("/hook-variants")) {
        return Response.json({
          clip_id: "clip-1",
          hook_variant: null,
          previews: generated ? batch : [],
        });
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`);
    }),
  );
}

beforeEach(() => {
  generated = false;
  generateError = null;
  stubFetch();
});

async function generatePreviews() {
  render(
    <HookVariantsStrip
      taskId="task-1"
      clipId="clip-1"
      onApplied={vi.fn()}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: /generate previews/i }));
  return await screen.findByRole(
    "group",
    { name: "Cold open — strongest moment first" },
    { timeout: 10000 },
  );
}

it("generates previews and lists them with labels and players", async () => {
  const coldCard = await generatePreviews();

  expect(
    screen.getByRole("group", { name: "Current opening" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("group", { name: "Strong punch-in opening" }),
  ).toBeInTheDocument();
  expect(
    within(coldCard).getByText(/Jumps straight to the strongest moment/),
  ).toBeInTheDocument();
  const video = coldCard.querySelector("video");
  expect(video).not.toBeNull();
  expect(video?.getAttribute("src")).toContain(
    "/api/tasks/task-1/clips/clip-1/hook-variants/pv-cold/file",
  );
  // The current opening starts out marked as the picked one (badge +
  // disabled pick button both say so).
  const currentCard = screen.getByRole("group", { name: "Current opening" });
  expect(within(currentCard).getAllByText("Picked")).toHaveLength(2);
  expect(
    within(coldCard).getByRole("button", { name: "Use this opening" }),
  ).toBeEnabled();
}, 15000);

it("picking a variant applies it and moves the picked mark", async () => {
  const onApplied = vi.fn();
  render(
    <HookVariantsStrip taskId="task-1" clipId="clip-1" onApplied={onApplied} />,
  );
  fireEvent.click(await screen.findByRole("button", { name: /generate previews/i }));
  const coldCard = await screen.findByRole(
    "group",
    { name: "Cold open — strongest moment first" },
    { timeout: 10000 },
  );

  fireEvent.click(
    within(coldCard).getByRole("button", { name: "Use this opening" }),
  );

  await waitFor(() => expect(onApplied).toHaveBeenCalled(), { timeout: 10000 });
  expect(onApplied.mock.calls[0][0]).toMatchObject({
    id: "clip-1",
    hook_variant: "cold_open",
  });
  await waitFor(
    () =>
      expect(within(coldCard).getAllByText("Picked")).toHaveLength(2),
    { timeout: 5000 },
  );
  const currentCard = screen.getByRole("group", { name: "Current opening" });
  expect(within(currentCard).queryAllByText("Picked")).toHaveLength(0);
}, 20000);

it("surfaces the server error detail when generation fails", async () => {
  generateError = "The source video is no longer available. Upload it again to build hook previews for this clip.";
  render(<HookVariantsStrip taskId="task-1" clipId="clip-1" />);

  fireEvent.click(await screen.findByRole("button", { name: /generate previews/i }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("source video is no longer available");
}, 15000);
