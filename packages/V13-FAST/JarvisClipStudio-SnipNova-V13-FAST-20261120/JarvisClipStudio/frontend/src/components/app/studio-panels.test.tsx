import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { StudioPanels } from "./studio-panels";
import type {
  GradePreset,
  LookState,
  TransitionOption,
} from "@/lib/clip-edit";

const initial: LookState = {
  transition: "fade",
  grade: {
    exposure: 0,
    contrast: 14,
    saturation: 22,
    temperature: 4,
    tint: 0,
    highlights: -12,
    shadows: -10,
  },
  vfx: { grain: 0, vignette: 0, glow: 0, sharpen: 0, letterbox: 0, shake: 0 },
  profile_lut: "punchy",
  stored: false,
};

const transitions: TransitionOption[] = [
  { name: "hard_cut", effect: null, group: "fades" },
  { name: "fade", effect: "fade", group: "fades" },
  { name: "wipeleft", effect: "wipeleft", group: "wipes" },
  { name: "zoomin", effect: "zoomin", group: "zoom_blur" },
];

const presets: GradePreset[] = [
  {
    name: "neutral",
    grade: {
      exposure: 0, contrast: 0, saturation: 0, temperature: 0,
      tint: 0, highlights: 0, shadows: 0,
    },
  },
  {
    name: "cinematic",
    grade: {
      exposure: -4, contrast: 16, saturation: -12, temperature: -6,
      tint: 0, highlights: -18, shadows: -14,
    },
  },
];

let postedBody: unknown;

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = decodeURIComponent(String(input));
      const method = init?.method ?? "GET";
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

function renderPanels(handlers?: Partial<{
  onApply: (apply: () => Promise<void>) => void;
  onRefreshed: (clip: Record<string, unknown>) => void;
  onPlanReload: () => void;
}>) {
  const onApply =
    handlers?.onApply ?? vi.fn((apply: () => Promise<void>) => apply());
  const onRefreshed = handlers?.onRefreshed ?? vi.fn();
  const onPlanReload = handlers?.onPlanReload ?? vi.fn();
  render(
    <StudioPanels
      taskId="task-1"
      clipId="clip-1"
      initial={initial}
      transitions={transitions}
      presets={presets}
      busy={false}
      onApply={onApply}
      onRefreshed={onRefreshed}
      onPlanReload={onPlanReload}
    />,
  );
  return { onApply, onRefreshed, onPlanReload };
}

function pressArrow(slider: HTMLElement, key: "ArrowRight" | "ArrowLeft", times = 1) {
  slider.focus();
  for (let i = 0; i < times; i += 1) {
    fireEvent.keyDown(slider, { key });
  }
}

beforeEach(() => {
  postedBody = undefined;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  stubFetch();
});

it("renders the three studio buttons and a disabled apply until something changes", () => {
  renderPanels();
  expect(screen.getByRole("button", { name: "Transitions" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Colour" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "VFX" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Re-render with these settings" }),
  ).toBeDisabled();
});

it("shows the transition picker with the clip's current transition", () => {
  renderPanels();
  fireEvent.click(screen.getByRole("button", { name: "Transitions" }));
  expect(screen.getByRole("combobox", { name: "Transitions" })).toBeInTheDocument();
});

it("posts only the moved grade slider as a look override and refreshes the clip", async () => {
  const { onRefreshed, onPlanReload } = renderPanels();
  fireEvent.click(screen.getByRole("button", { name: "Colour" }));

  const saturation = screen.getByRole("slider", { name: "Saturation" });
  expect(saturation).toHaveAttribute("aria-valuenow", "22");
  pressArrow(saturation, "ArrowRight", 3);
  expect(saturation).toHaveAttribute("aria-valuenow", "25");

  const apply = screen.getByRole("button", {
    name: "Re-render with these settings",
  });
  expect(apply).toBeEnabled();
  fireEvent.click(apply);

  await waitFor(() => expect(onRefreshed).toHaveBeenCalled());
  expect(postedBody).toEqual({ look: { grade: { saturation: 25 } } });
  expect(onRefreshed).toHaveBeenCalledWith({ id: "clip-1" });
  expect(onPlanReload).toHaveBeenCalled();
});

it("caps the letterbox slider at 40 and posts VFX changes", async () => {
  const { onRefreshed } = renderPanels();
  fireEvent.click(screen.getByRole("button", { name: "VFX" }));

  const letterbox = screen.getByRole("slider", { name: "Letterbox bars" });
  expect(letterbox).toHaveAttribute("aria-valuemax", "40");

  const grain = screen.getByRole("slider", { name: "Film grain" });
  pressArrow(grain, "ArrowRight", 5);
  fireEvent.click(
    screen.getByRole("button", { name: "Re-render with these settings" }),
  );

  await waitFor(() => expect(onRefreshed).toHaveBeenCalled());
  expect(postedBody).toEqual({ look: { vfx: { grain: 5 } } });
});

it("shows the LUT picker in the Colour panel with library entries", () => {
  render(
    <StudioPanels
      taskId="task-1"
      clipId="clip-1"
      initial={initial}
      transitions={transitions}
      presets={presets}
      luts={[
        { filename: "teal.cube", display_name: "Teal", size: 33, title: "Teal" },
      ]}
      busy={false}
      onApply={(apply) => apply()}
      onRefreshed={vi.fn()}
      onPlanReload={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Colour" }));
  expect(
    screen.getByRole("combobox", { name: "Imported LUT (.cube)" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Import .cube…" }),
  ).toBeInTheDocument();
});

it("imports a .cube upload and applies it as the grade LUT", async () => {
  const onRefreshed = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = decodeURIComponent(String(input));
      const method = init?.method ?? "GET";
      if (method === "POST" && url.endsWith("/api/luts/upload")) {
        expect(init?.body).toBeInstanceOf(FormData);
        return Response.json({
          filename: "teal.cube",
          display_name: "Teal",
          size: 33,
          title: "Teal",
        });
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
  render(
    <StudioPanels
      taskId="task-1"
      clipId="clip-1"
      initial={initial}
      transitions={transitions}
      presets={presets}
      busy={false}
      onApply={(apply) => apply()}
      onRefreshed={onRefreshed}
      onPlanReload={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Colour" }));

  const input = screen.getByLabelText("Import .cube…");
  const file = new File(["LUT_3D_SIZE 2"], "teal.cube", { type: "text/plain" });
  fireEvent.change(input, { target: { files: [file] } });

  const apply = screen.getByRole("button", {
    name: "Re-render with these settings",
  });
  await waitFor(() => expect(apply).toBeEnabled());
  fireEvent.click(apply);

  await waitFor(() => expect(onRefreshed).toHaveBeenCalled());
  expect(postedBody).toEqual({ look: { grade: { lut: "teal.cube" } } });
});
