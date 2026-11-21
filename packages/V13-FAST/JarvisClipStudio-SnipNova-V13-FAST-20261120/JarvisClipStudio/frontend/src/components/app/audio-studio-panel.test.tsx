import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { AudioStudioPanel } from "./audio-studio-panel";
import type { AudioState } from "@/lib/clip-edit";

const baseAudio: AudioState = {
  music_track: "auto",
  music_volume: 25,
  ducking_db: 12,
  noise_reduction: 0,
  voice_clarity: 0,
  loudness: "auto",
  fade_in: 0,
  fade_out: 0,
  stored: false,
  tracks: [
    { filename: "calm.mp3", display_name: "Calm", source: "library" },
  ],
  voiceover_enabled: false,
  voiceover: null,
  voices: [],
};

const voAudio: AudioState = {
  ...baseAudio,
  voiceover_enabled: true,
  voices: [
    { name: "af_heart", label: "Heart — female (US)" },
    { name: "am_michael", label: "Michael — male (US)" },
  ],
};

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

function renderPanel(audio: AudioState) {
  const onApply = vi.fn((apply: () => Promise<void>) => apply());
  const onRefreshed = vi.fn();
  const onPlanReload = vi.fn();
  render(
    <AudioStudioPanel
      taskId="task-1"
      clipId="clip-1"
      audio={audio}
      busy={false}
      onApply={onApply}
      onRefreshed={onRefreshed}
      onPlanReload={onPlanReload}
    />,
  );
  return { onApply, onRefreshed, onPlanReload };
}

function pressArrow(slider: HTMLElement, times = 1) {
  slider.focus();
  for (let i = 0; i < times; i += 1) {
    fireEvent.keyDown(slider, { key: "ArrowRight" });
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

it("renders the audio controls with apply disabled until something changes", () => {
  renderPanel(baseAudio);
  expect(screen.getByText("Audio studio")).toBeInTheDocument();
  expect(screen.getByLabelText("Music track")).toBeInTheDocument();
  expect(screen.getByLabelText("Loudness")).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: /Noise reduction/ })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Re-render with these settings" }),
  ).toBeDisabled();
  // Voiceover is off: no narration controls at all.
  expect(screen.queryByLabelText("Narration script")).not.toBeInTheDocument();
});

it("posts only the changed audio fields", async () => {
  renderPanel(baseAudio);
  pressArrow(screen.getByRole("slider", { name: /Noise reduction/ }), 30);
  const apply = screen.getByRole("button", {
    name: "Re-render with these settings",
  });
  expect(apply).toBeEnabled();
  fireEvent.click(apply);
  await waitFor(() => expect(postedBody).toBeDefined());
  expect(postedBody).toEqual({ audio: { noise_reduction: 30 } });
});

it("shows the voiceover controls only when the feature is enabled", () => {
  renderPanel(voAudio);
  expect(screen.getByLabelText("Narration script")).toBeInTheDocument();
  expect(screen.getByLabelText("Voice")).toBeInTheDocument();
  expect(screen.getByText("AI voiceover")).toBeInTheDocument();
});

it("posts a typed voiceover script with its voice and mode", async () => {
  renderPanel(voAudio);
  fireEvent.change(screen.getByLabelText("Narration script"), {
    target: { value: "You will not believe this." },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Re-render with these settings" }),
  );
  await waitFor(() => expect(postedBody).toBeDefined());
  expect(postedBody).toEqual({
    voiceover: {
      script: "You will not believe this.",
      voice: "af_heart",
      mode: "hook",
    },
  });
});

it("removing a stored voiceover posts the clear request", async () => {
  renderPanel({
    ...voAudio,
    voiceover: { script: "Old line", voice: "af_heart", mode: "hook" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Remove voiceover" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Re-render with these settings" }),
  );
  await waitFor(() => expect(postedBody).toBeDefined());
  expect(postedBody).toEqual({ voiceover: { clear: true } });
});
