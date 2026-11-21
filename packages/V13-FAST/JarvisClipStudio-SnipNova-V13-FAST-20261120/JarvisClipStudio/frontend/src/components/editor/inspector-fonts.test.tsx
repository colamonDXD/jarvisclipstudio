import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { Inspector } from "./inspector";
import type { EditDocument, EditorState } from "@/lib/editor/document";
import { draft } from "@/lib/editor/document.test-fixture";

// jsdom has no ResizeObserver and the Radix select observes it for sizing.
vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

beforeEach(() => {
  // Radix Select scrolls the active option into view (jsdom lacks scrollIntoView).
  HTMLElement.prototype.scrollIntoView = vi.fn();
});

const asset: EditorState = {
  status: "ready",
  basis: "clip",
  duration: 3,
  width: 1080,
  height: 1920,
  fps: 30,
  hasAudio: true,
  waveform: [],
  draft: { revision: 1, document: draft },
  original: draft,
  jobs: [],
};

function renderInspector(initialDoc: EditDocument = draft) {
  function Harness() {
    const [doc, setDoc] = useState(initialDoc);
    return (
      <Inspector
        doc={doc}
        asset={asset}
        tab="captions"
        setTab={() => {}}
        time={0}
        editTime={0}
        seek={() => {}}
        update={(change) => setDoc((current) => change(current))}
        applyAll={vi.fn(async () => {})}
        endpoint="/api/test"
        brollId={null}
        selectBroll={() => {}}
        overlayId={null}
        selectOverlay={() => {}}
      />
    );
  }
  return render(<Harness />);
}

describe("Inspector caption font select", () => {
  it("offers all six model fonts, including both Montserrat weights", async () => {
    // N6: CaptionStyle allows Montserrat-Black/ExtraBold, but the
    // inspector used to offer only four fonts — a draft seeded with a
    // Montserrat font kept rendering, yet the user could never pick it
    // again once they changed away.
    // fireEvent.click (not userEvent): jsdom has no hasPointerCapture,
    // which Radix Select calls on pointerdown. A plain click opens the
    // menu through its onClick path, as in home-app-niche.test.tsx.
    renderInspector();

    fireEvent.click(screen.getByRole("combobox", { name: "Caption font" }));
    // The Radix select content mounts in a portal after the open click,
    // so wait for the first option before asserting the full list.
    await screen.findByRole("option", { name: "TikTok Sans" });
    for (const label of [
      "TikTok Sans",
      "TikTok Sans Bold",
      "Poppins Extra Bold",
      "The Bold Font",
      "Montserrat Black",
      "Montserrat Extra Bold",
    ]) {
      expect(screen.getByRole("option", { name: label })).toBeInTheDocument();
    }
  });

  it("lets the user change to a Montserrat font and back again", async () => {
    renderInspector();

    fireEvent.click(screen.getByRole("combobox", { name: "Caption font" }));
    fireEvent.click(
      await screen.findByRole("option", { name: "Montserrat Black" }),
    );
    expect(
      screen.getByRole("combobox", { name: "Caption font" }),
    ).toHaveTextContent("Montserrat Black");

    fireEvent.click(screen.getByRole("combobox", { name: "Caption font" }));
    fireEvent.click(
      await screen.findByRole("option", { name: "TikTok Sans" }),
    );
    expect(
      screen.getByRole("combobox", { name: "Caption font" }),
    ).toHaveTextContent("TikTok Sans");
  });

  it("keeps a Montserrat-seeded draft rendering its font", () => {
    // A draft seeded with Montserrat-ExtraBold (e.g. by a template)
    // shows that font selected; the select value is the model name,
    // which both renderers load by name.
    const seeded: EditDocument = {
      ...draft,
      captions: { ...draft.captions, font: "Montserrat-ExtraBold" },
    };
    renderInspector(seeded);
    expect(
      screen.getByRole("combobox", { name: "Caption font" }),
    ).toHaveTextContent("Montserrat Extra Bold");
  });
});
