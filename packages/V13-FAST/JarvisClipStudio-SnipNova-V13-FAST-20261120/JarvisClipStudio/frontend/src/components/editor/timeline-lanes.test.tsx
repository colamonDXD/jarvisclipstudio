import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Timeline } from "./timeline";
import { draft } from "@/lib/editor/document.test-fixture";
import type { EditDocument, EditorState } from "@/lib/editor/document";

// jsdom has no ResizeObserver; the filmstrip only observes for sizing.
vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

const footage = {
  provider: "pexels" as const,
  id: 42,
  width: 1080,
  height: 1920,
  duration: 10,
  fileUrl: "https://videos.pexels.com/video-files/42/42-hd_1080_1920_25fps.mp4",
  pageUrl: "https://www.pexels.com/video/42/",
  thumbnail: "",
  credit: "Some Filmmaker",
};

const withBroll: EditDocument = {
  ...draft,
  segments: [{ id: "s", start: 0, end: 20 }],
  broll: [{ id: "b1", start: 1, end: 4, offset: 0, query: "ocean", footage }],
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
  addBroll = vi.fn(),
  seek = vi.fn(),
}: {
  initial: EditDocument;
  addBroll?: (time: number) => void;
  seek?: (time: number) => void;
}) {
  const [doc, setDoc] = useState(initial);
  return (
    <TooltipProvider>
      <Timeline
        doc={doc}
        asset={assetFor(doc)}
        endpoint="/test/editor"
        currentTime={0}
        onTextDraft={() => {}}
        index={0}
        select={() => {}}
        pause={() => {}}
        seek={seek}
        update={(change) => setDoc((d) => change(d))}
        brollId={null}
        selectBroll={() => {}}
        overlayId={null}
        selectOverlay={() => {}}
        addBroll={addBroll}
        addOverlay={() => {}}
      />
    </TooltipProvider>
  );
}

it("hides and re-shows the B-roll lane without touching its clips", () => {
  render(<Harness initial={withBroll} />);
  const lane = screen.getByRole("group", { name: "B-roll track" });
  expect(lane.className).not.toContain("opacity-40");
  fireEvent.click(screen.getByRole("button", { name: "Hide B-roll" }));
  expect(screen.getByRole("button", { name: "Show B-roll" })).toHaveAttribute("aria-pressed", "true");
  expect(lane.className).toContain("opacity-40");
  // The clip itself is still there, unaltered.
  expect(screen.getByRole("button", { name: /ocean/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Show B-roll" }));
  expect(screen.getByRole("button", { name: "Hide B-roll" })).toHaveAttribute("aria-pressed", "false");
  expect(lane.className).not.toContain("opacity-40");
}, 30000);

it("hides the hook lane from its header toggle", () => {
  render(<Harness initial={withBroll} />);
  fireEvent.click(screen.getByRole("button", { name: "Hide hook" }));
  expect(screen.getByRole("button", { name: "Show hook" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("group", { name: "Hook title" }).className).toContain("opacity-40");
});

it("locking the video lane disables the segment controls until unlocked", () => {
  render(<Harness initial={withBroll} />);
  fireEvent.click(screen.getByRole("button", { name: "Lock Video" }));
  expect(screen.getByRole("button", { name: "Unlock Video" })).toHaveAttribute("aria-pressed", "true");
  for (const name of ["Split at playhead", "Remove selected segment", "Duplicate selected segment", "Set In", "Set Out"])
    expect(screen.getByRole("button", { name })).toBeDisabled();
  expect(screen.getByLabelText("In point seconds")).toBeDisabled();
  expect(screen.getByLabelText("Out point seconds")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Unlock Video" }));
  expect(screen.getByRole("button", { name: "Duplicate selected segment" })).not.toBeDisabled();
  expect(screen.getByLabelText("In point seconds")).not.toBeDisabled();
});

it("a locked B-roll lane seeks on lane click but never adds", () => {
  const addBroll = vi.fn();
  const seek = vi.fn();
  render(<Harness initial={{ ...withBroll, lanes: { broll: { hidden: false, locked: true } } }} addBroll={addBroll} seek={seek} />);
  fireEvent.click(screen.getByRole("group", { name: "B-roll track" }));
  expect(addBroll).not.toHaveBeenCalled();
  expect(seek).toHaveBeenCalled();
});

it("an unlocked B-roll lane adds on lane click", () => {
  const addBroll = vi.fn();
  render(<Harness initial={withBroll} addBroll={addBroll} />);
  fireEvent.click(screen.getByRole("group", { name: "B-roll track" }));
  expect(addBroll).toHaveBeenCalledTimes(1);
});

it("locked audio and captions lanes disable their header toggles", () => {
  render(
    <Harness
      initial={{ ...withBroll, lanes: { audio: { hidden: false, locked: true }, captions: { hidden: false, locked: true } } }}
    />,
  );
  expect(screen.getByRole("button", { name: "Mute" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Hide captions" })).toBeDisabled();
  // The lock toggles themselves stay usable.
  expect(screen.getByRole("button", { name: "Unlock Audio" })).not.toBeDisabled();
  expect(screen.getByRole("button", { name: "Unlock Captions" })).not.toBeDisabled();
});

it("a locked captions lane shows its text without an edit button", () => {
  const { unmount } = render(<Harness initial={withBroll} />);
  expect(screen.getByRole("button", { name: "Hello world today" })).toBeTruthy();
  unmount();
  render(<Harness initial={{ ...withBroll, lanes: { captions: { hidden: false, locked: true } } }} />);
  expect(screen.queryByRole("button", { name: "Hello world today" })).toBeNull();
  expect(screen.getByText("Hello world today")).toBeTruthy();
});
