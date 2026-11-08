import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { MusicSelect, type MusicTrack } from "./music-select";

const tracks: MusicTrack[] = [
  { filename: "calm.mp3", display_name: "calm", source: "bundled", duration_sec: 125 },
  { filename: "my song.wav", display_name: "my song", source: "user", duration_sec: null },
];

const playMock = vi.fn().mockResolvedValue(undefined);
const pauseMock = vi.fn();

beforeEach(() => {
  playMock.mockClear();
  pauseMock.mockClear();
  Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
    configurable: true,
    value: playMock,
  });
  Object.defineProperty(window.HTMLMediaElement.prototype, "pause", {
    configurable: true,
    value: pauseMock,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/music/library")) return Response.json({ tracks });
      return Response.json({});
    })
  );
});

function Harness({ onChange }: { onChange?: (value: string | null) => void }) {
  const [value, setValue] = useState<string | null>(null);
  return (
    <MusicSelect
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

async function openMenu() {
  fireEvent.click(screen.getByRole("button", { name: "Music" }));
  return await screen.findByRole("button", { name: "Auto" });
}

it("renders Auto plus every library track", async () => {
  render(<Harness />);
  await openMenu();

  expect(screen.getByRole("button", { name: "Auto" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^calm/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "my song" })).toBeInTheDocument();
  expect(screen.getByText("2:05")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Custom music/ })).toBeInTheDocument();
});

it("selects a track and reports it through onChange", async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  await openMenu();

  fireEvent.click(screen.getByRole("button", { name: /^calm/ }));
  expect(onChange).toHaveBeenCalledWith("calm.mp3");
});

it("preview toggles play and pause on the shared audio element", async () => {
  render(<Harness />);
  await openMenu();

  fireEvent.click(screen.getByRole("button", { name: "Preview calm" }));
  expect(playMock).toHaveBeenCalledTimes(1);

  // Starting another track's preview replaces the first one's playback.
  fireEvent.click(screen.getByRole("button", { name: "Preview my song" }));
  expect(playMock).toHaveBeenCalledTimes(2);

  fireEvent.click(screen.getByRole("button", { name: "Stop preview" }));
  expect(pauseMock).toHaveBeenCalled();
});

it("upload flow saves the track and selects it", async () => {
  const onChange = vi.fn();
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/api/music/upload")) {
      return Response.json(
        { filename: "fresh beat.mp3", display_name: "fresh beat", source: "user", duration_sec: 30 },
        { status: 201 }
      );
    }
    if (url.includes("/api/music/library")) return Response.json({ tracks });
    return Response.json({});
  });
  render(<Harness onChange={onChange} />);
  await openMenu();

  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["audio"], "fresh beat.mp3", { type: "audio/mpeg" });
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => expect(onChange).toHaveBeenCalledWith("fresh beat.mp3"));
  const uploadCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/music/upload"));
  expect(uploadCall).toBeDefined();
  expect((uploadCall![1] as RequestInit).body).toBeInstanceOf(FormData);
});

it("stays usable with Auto only when the library fetch fails", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("offline"));
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  await openMenu();

  expect(screen.queryByRole("button", { name: /^calm/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Auto" }));
  expect(onChange).toHaveBeenCalledWith(null);
});
