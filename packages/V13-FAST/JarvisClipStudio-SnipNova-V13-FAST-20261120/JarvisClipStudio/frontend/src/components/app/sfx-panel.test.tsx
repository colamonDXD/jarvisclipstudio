import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { SfxPanel } from "./sfx-panel";
import type { SfxState } from "@/lib/clip-edit";

const initial: SfxState = {
  placements: [],
  library: [
    { filename: "pop.wav", display_name: "pop", duration: 0.4 },
    { filename: "whoosh.mp3", display_name: "whoosh", duration: 1.2 },
  ],
};

let postedBody: unknown;

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = decodeURIComponent(String(input));
      const method = init?.method ?? "GET";
      if (method === "POST" && url.endsWith("/api/sfx/upload")) {
        return Response.json(
          {
            filename: "riser.wav",
            display_name: "riser",
            duration: 0.9,
            source: "user",
          },
          { status: 201 },
        );
      }
      if (method === "POST" && url.endsWith("/edit")) {
        postedBody = JSON.parse(String(init?.body ?? "{}"));
        return Response.json(
          {
            job_id: "clip-edit:clip-1:abc",
            status: "completed",
            clip: { id: "clip-1" },
          },
          { status: 202 },
        );
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`);
    }),
  );
}

function renderPanel(state: SfxState = initial) {
  const onRefreshed = vi.fn();
  const onPlanReload = vi.fn();
  render(
    <SfxPanel
      taskId="task-1"
      clipId="clip-1"
      initial={state}
      duration={20}
      busy={false}
      onApply={(apply) => apply()}
      onRefreshed={onRefreshed}
      onPlanReload={onPlanReload}
    />,
  );
  return { onRefreshed, onPlanReload };
}

beforeEach(() => {
  postedBody = undefined;
  stubFetch();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

it("lists the library with durations and a disabled apply until a placement changes", () => {
  renderPanel();
  expect(screen.getByText("pop")).toBeInTheDocument();
  expect(screen.getByText("whoosh")).toBeInTheDocument();
  expect(screen.getByText(/0\.4s/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Re-render with these effects" }),
  ).toBeDisabled();
});

it("uploads an own effect and lists it as a user effect", async () => {
  renderPanel();
  const input = screen.getByLabelText("Upload effect");
  const file = new File([new Uint8Array([1, 2, 3])], "riser.wav", {
    type: "audio/wav",
  });
  fireEvent.change(input, { target: { files: [file] } });
  expect(await screen.findByText("riser")).toBeInTheDocument();
  expect(screen.getByText(/Your effects/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Place riser on the timeline" }),
  ).toBeInTheDocument();
});

it("places an effect, edits its time and volume, and posts the full list", async () => {
  const { onRefreshed, onPlanReload } = renderPanel();
  fireEvent.click(
    screen.getByRole("button", { name: "Place pop on the timeline" }),
  );

  const atInput = screen.getByLabelText("Start second for pop.wav");
  fireEvent.change(atInput, { target: { value: "2.5" } });

  const volume = screen.getByRole("slider", { name: "Volume for pop.wav" });
  volume.focus();
  fireEvent.keyDown(volume, { key: "ArrowLeft" });
  fireEvent.keyDown(volume, { key: "ArrowLeft" });
  fireEvent.keyDown(volume, { key: "ArrowLeft" });

  const apply = screen.getByRole("button", {
    name: "Re-render with these effects",
  });
  expect(apply).toBeEnabled();
  fireEvent.click(apply);

  await waitFor(() => expect(onRefreshed).toHaveBeenCalled());
  expect(postedBody).toEqual({
    sfx: [{ name: "pop.wav", at_sec: 2.5, gain_db: -3 }],
  });
  expect(onPlanReload).toHaveBeenCalled();
});

it("removing the last stored placement posts an explicit empty list", async () => {
  const { onRefreshed } = renderPanel({
    placements: [{ name: "pop.wav", at_sec: 1, gain_db: 0 }],
    library: initial.library,
  });
  fireEvent.click(screen.getByRole("button", { name: "Remove pop.wav" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Re-render with these effects" }),
  );
  await waitFor(() => expect(onRefreshed).toHaveBeenCalled());
  expect(postedBody).toEqual({ sfx: [] });
});

it("previews a library file through the sfx file route", async () => {
  const play = vi.fn().mockResolvedValue(undefined);
  const pause = vi.fn();
  class FakeAudio {
    onended: (() => void) | null = null;
    src: string;
    constructor(src: string) {
      this.src = src;
    }
    play = play;
    pause = pause;
  }
  vi.stubGlobal("Audio", FakeAudio);

  renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "Preview pop" }));
  expect(play).toHaveBeenCalled();
  // Toggling the same row stops playback instead of stacking a second one.
  fireEvent.click(screen.getByRole("button", { name: "Preview pop" }));
  expect(pause).toHaveBeenCalled();
});

it("shows an honest empty state when the library has no files", () => {
  renderPanel({ placements: [], library: [] });
  expect(screen.getByText(/No SFX files yet/)).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /Place .* on the timeline/ }),
  ).not.toBeInTheDocument();
});
