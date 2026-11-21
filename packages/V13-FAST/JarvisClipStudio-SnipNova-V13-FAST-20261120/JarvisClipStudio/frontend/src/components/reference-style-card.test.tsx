import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ReferenceStyleCard } from "./reference-style-card";
import type {
  ReferenceStyleCreated,
  ReferenceStyleSummary,
} from "@/lib/reference-styles";

const existing: ReferenceStyleSummary[] = [
  {
    name: "user-old-style",
    display_name: "Old Style",
    base_niche: "gaming",
    source_file: "old.mp4",
    headline: { cuts_per_min: 20, caption_frame_fraction: 0.5, music_bed_present: true, tempo_bpm: 120 },
  },
];

const created: ReferenceStyleCreated = {
  name: "user-my-test-style",
  display_name: "My Test Style",
  base_niche: "gaming",
  source_file: "reference.mp4",
  headline: {
    cuts_per_min: 42.4,
    caption_frame_fraction: 0.8,
    music_bed_present: true,
    tempo_bpm: 128,
  },
  inferred: { pacing: "cuts_per_min=42.4 -> band [30, 60]" },
  not_inferred: ["overlays.* (not measurable from a finished short)"],
};

const createdCloseCall: ReferenceStyleCreated = {
  name: "user-my-funk-style",
  display_name: "My Funk Style",
  base_niche: "japan_travel_cinematic",
  base_niche_margin: 0.0789,
  base_niche_runner_up: "travel_vlog",
  base_niche_close_call: true,
  source_file: "reference.mp4",
  headline: {
    cuts_per_min: 6,
    caption_frame_fraction: 0.022,
    music_bed_present: true,
    tempo_bpm: null,
  },
  inferred: {},
  not_inferred: [],
};

let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch(handler: (url: string, init?: RequestInit) => Promise<Response>) {
  fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init)
  );
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  stubFetch(async (url, init) => {
    if (url.includes("/api/reference-styles/upload")) {
      return Response.json(created, { status: 201 });
    }
    if (url.includes("/api/reference-styles/from-url")) {
      return Response.json(
        { detail: "Could not download that URL: Video unavailable" },
        { status: 400 }
      );
    }
    if (init?.method === "DELETE") return Response.json({});
    return Response.json({});
  });
});

function renderCard(overrides: Partial<Parameters<typeof ReferenceStyleCard>[0]> = {}) {
  const props = {
    styles: existing,
    onRefresh: vi.fn(async () => {}),
    onSelect: vi.fn(),
    onDeleted: vi.fn(),
    ...overrides,
  };
  const utils = render(<ReferenceStyleCard {...props} />);
  return { props, ...utils };
}

it("renders the user's existing styles with delete buttons", () => {
  renderCard();
  expect(screen.getByText("Old Style")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Delete Old Style" })
  ).toBeInTheDocument();
});

it("creates a style from an uploaded video and selects it", async () => {
  const { props, container } = renderCard();

  fireEvent.change(screen.getByLabelText("Style name"), {
    target: { value: "My Test Style" },
  });
  const fileInput = container.querySelector(
    'input[type="file"]'
  ) as HTMLInputElement;
  const file = new File(["video"], "reference.mp4", { type: "video/mp4" });
  fireEvent.change(fileInput, { target: { files: [file] } });
  expect(screen.getByText("reference.mp4")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Create style" }));

  expect(await screen.findByText(/Style created/)).toBeInTheDocument();
  expect(screen.getByText(/cuts_per_min=42.4/)).toBeInTheDocument();
  expect(
    screen.getByText(/not measurable from a finished short/)
  ).toBeInTheDocument();
  expect(props.onSelect).toHaveBeenCalledWith("user-my-test-style");
  expect(props.onRefresh).toHaveBeenCalled();

  const uploadCall = fetchMock.mock.calls.find(([url]) =>
    String(url).includes("/api/reference-styles/upload")
  );
  expect(uploadCall).toBeDefined();
  expect((uploadCall![1] as RequestInit).body).toBeInstanceOf(FormData);
});

it("shows the server's real reason when a URL cannot be downloaded", async () => {
  renderCard();

  fireEvent.change(screen.getByLabelText("Style name"), {
    target: { value: "Web Style" },
  });
  fireEvent.change(screen.getByLabelText("Reference video link"), {
    target: { value: "https://www.youtube.com/watch?v=abc" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create style" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Could not download that URL: Video unavailable");
});

it("keeps the plain verdict for a clear win", async () => {
  const { container } = renderCard();

  fireEvent.change(screen.getByLabelText("Style name"), {
    target: { value: "My Test Style" },
  });
  const fileInput = container.querySelector(
    'input[type="file"]'
  ) as HTMLInputElement;
  const file = new File(["video"], "reference.mp4", { type: "video/mp4" });
  fireEvent.change(fileInput, { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Create style" }));

  expect(await screen.findByText(/Style created/)).toBeInTheDocument();
  expect(screen.getByText("Built on the Gaming style")).toBeInTheDocument();
  expect(screen.queryByText(/Closest pacing match/)).not.toBeInTheDocument();
});

it("shows a close call honestly instead of a confident verdict", async () => {
  stubFetch(async (url) => {
    if (url.includes("/api/reference-styles/upload")) {
      return Response.json(createdCloseCall, { status: 201 });
    }
    return Response.json({});
  });
  const { container } = renderCard();

  fireEvent.change(screen.getByLabelText("Style name"), {
    target: { value: "My Funk Style" },
  });
  const fileInput = container.querySelector(
    'input[type="file"]'
  ) as HTMLInputElement;
  const file = new File(["video"], "reference.mp4", { type: "video/mp4" });
  fireEvent.change(fileInput, { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Create style" }));

  expect(await screen.findByText(/Style created/)).toBeInTheDocument();
  // The reproduced Japan-slice margin (0.0789) must not read as a
  // confident genre verdict: the card names the runner-up instead.
  expect(screen.getByText(/Closest pacing match/)).toBeInTheDocument();
  expect(screen.getByText(/Japan travel cinematic/)).toBeInTheDocument();
  expect(screen.getByText(/Travel vlog/)).toBeInTheDocument();
  expect(screen.queryByText(/Built on the/)).not.toBeInTheDocument();
});

it("marks a close-call style in the existing-styles list", () => {
  renderCard({
    styles: [
      {
        ...existing[0],
        base_niche_margin: 0,
        base_niche_runner_up: "true_crime",
        base_niche_close_call: true,
      },
    ],
  });
  expect(screen.getByText(/close pacing match/)).toBeInTheDocument();
});

it("deletes a style and reports it", async () => {
  const { props } = renderCard();

  fireEvent.click(screen.getByRole("button", { name: "Delete Old Style" }));

  await waitFor(() => {
    expect(props.onDeleted).toHaveBeenCalledWith("user-old-style");
  });
  expect(props.onRefresh).toHaveBeenCalled();
  const deleteCall = fetchMock.mock.calls.find(
    ([, init]) => (init as RequestInit | undefined)?.method === "DELETE"
  );
  expect(deleteCall).toBeDefined();
  expect(String(deleteCall![0])).toContain("user-old-style");
});
