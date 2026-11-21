import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BRollPanel } from "./broll-panel";
import type { BRollClip, EditDocument, EditorState } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";

afterEach(() => vi.unstubAllGlobals());

// jsdom has no ResizeObserver; the panel's thumbnail sizing observes it.
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
});

const pixabayFootage = {
  provider: "pixabay" as const,
  id: 44,
  width: 1920,
  height: 1080,
  duration: 12,
  fileUrl: "https://cdn.pixabay.com/44.mp4",
  pageUrl: "https://pixabay.com/videos/44/",
  thumbnail: "https://cdn.pixabay.com/44.jpg",
  credit: "",
};

const pixabayClip: BRollClip = {
  id: "b1",
  start: 13.54,
  end: 16.54,
  offset: 0,
  query: "market",
  concept: "Candlestick charts and a profit/loss graph on a trader's monitors.",
  searchQuery: "stock market chart screen",
  searchQueries: ["stock market chart screen", "trading desk monitors", "candlestick graph"],
  avoid: ["street market", "farmers market stalls", "shopping crowd"],
  media: "either",
  footage: pixabayFootage,
};

const docWithConcept: EditDocument = {
  ...draft,
  segments: [{ id: "s", start: 0, end: 20 }],
  broll: [pixabayClip],
  hookTitle: "A hook",
};

const assetFor = (doc: EditDocument): EditorState => ({
  status: "ready",
  basis: "clip.mp4",
  duration: 20,
  width: 320,
  height: 180,
  fps: 30,
  hasAudio: true,
  waveform: [],
  draft: { revision: 0, document: doc },
  original: doc,
  jobs: [],
});

function Harness({
  initial,
  onDoc,
}: {
  initial: EditDocument;
  onDoc?: (doc: EditDocument) => void;
}) {
  const [doc, setDoc] = useState(initial);
  onDoc?.(doc);
  return (
    <BRollPanel
      doc={doc}
      asset={assetFor(doc)}
      endpoint="/test/editor"
      time={14}
      seek={vi.fn()}
      update={(change) => setDoc((d) => change(d))}
      selected="b1"
      select={vi.fn()}
    />
  );
}

const stubFetch = (impl: (url: string, init?: RequestInit) => Promise<Response>) => {
  vi.stubGlobal("fetch", vi.fn(impl));
};

const statusOk = async (url: string) => {
  if (url.includes("/broll/status")) return new Response(JSON.stringify({ configured: true }));
  throw new Error(`unexpected fetch ${url}`);
};

it("shows the planner concept and the stock provider for a Pixabay cue", async () => {
  stubFetch(statusOk);
  render(<Harness initial={docWithConcept} />);
  expect(await screen.findByText("Candlestick charts and a profit/loss graph on a trader's monitors.")).toBeInTheDocument();
  expect(screen.getByText("What the AI asked for", { exact: false })).toBeInTheDocument();
  // The provider name is a link inside the credit line, so the text is
  // split across elements; match the paragraph by its full text.
  expect(
    screen.getByText((_, el) => el?.textContent === "Stock footage from Pixabay"),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Pixabay" })).toHaveAttribute("href", "https://pixabay.com/videos/44/");
  expect(screen.getByRole("button", { name: "Next candidate" })).toBeInTheDocument();
});

it("next candidate swaps the footage and keeps the cue timing", async () => {
  const replacement = {
    ...pixabayClip,
    footage: { ...pixabayFootage, id: 45, fileUrl: "https://cdn.pixabay.com/45.mp4", thumbnail: "https://cdn.pixabay.com/45.jpg" },
  };
  const seen: { doc: EditDocument | null } = { doc: null };
  stubFetch(async (url, init) => {
    if (url.includes("/broll/status")) return new Response(JSON.stringify({ configured: true }));
    if (url.includes("next-candidate")) {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(init?.body as string)).toEqual({ seen: [] });
      return new Response(JSON.stringify({ broll: replacement }));
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  render(<Harness initial={docWithConcept} onDoc={(doc) => { seen.doc = doc; }} />);
  fireEvent.click(await screen.findByRole("button", { name: "Next candidate" }));
  await waitFor(() => expect(seen.doc?.broll?.[0].footage.id).toBe(45));
  expect(seen.doc?.broll?.[0].start).toBe(13.54);
  expect(seen.doc?.broll?.[0].end).toBe(16.54);
  expect(seen.doc?.broll?.[0].concept).toBe(pixabayClip.concept);
});

it("an exhausted pool keeps the current footage and says so", async () => {
  stubFetch(async (url) => {
    if (url.includes("/broll/status")) return new Response(JSON.stringify({ configured: true }));
    if (url.includes("next-candidate"))
      return new Response(
        JSON.stringify({ detail: { code: "NO_MORE_CANDIDATES", message: "No more candidates for this B-roll cue" } }),
        { status: 404 },
      );
    throw new Error(`unexpected fetch ${url}`);
  });
  const seen: { doc: EditDocument | null } = { doc: null };
  render(<Harness initial={docWithConcept} onDoc={(doc) => { seen.doc = doc; }} />);
  fireEvent.click(await screen.findByRole("button", { name: "Next candidate" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Next candidate" })).not.toBeDisabled());
  expect(seen.doc?.broll?.[0].footage.id).toBe(44);
});
