import { describe, expect, it } from "vitest";
import {
  captionsInExport,
  exportGain,
  laneHidden,
  laneLocked,
  laneStateOf,
  withLaneState,
  type EditDocument,
  type LaneId,
} from "./document";
import { draft } from "./document.test-fixture";

const LANES: LaneId[] = ["video", "broll", "overlays", "captions", "hook", "audio"];

describe("Lane controls on the document", () => {
  it("treats a document without the field as all lanes visible and unlocked", () => {
    expect(draft.lanes).toBeUndefined();
    for (const lane of LANES) {
      expect(laneStateOf(draft, lane)).toEqual({ hidden: false, locked: false });
      expect(laneHidden(draft, lane)).toBe(false);
      expect(laneLocked(draft, lane)).toBe(false);
    }
  });

  it("reads partial entries the way the backend model fills them", () => {
    const partial = { broll: { hidden: true } } as unknown as EditDocument["lanes"];
    const doc: EditDocument = { ...draft, lanes: partial };
    expect(laneStateOf(doc, "broll")).toEqual({ hidden: true, locked: false });
  });

  it("stores the full pair when a control is set", () => {
    const next = withLaneState(draft, "broll", { hidden: true });
    expect(next.lanes).toEqual({ broll: { hidden: true, locked: false } });
    expect(laneHidden(next, "broll")).toBe(true);
    expect(draft.lanes).toBeUndefined();
  });

  it("drops the entry — and the key — when a lane returns to the defaults", () => {
    const hidden = withLaneState(draft, "overlays", { hidden: true });
    const restored = withLaneState(hidden, "overlays", { hidden: false });
    expect(restored.lanes).toBeUndefined();
    expect(JSON.stringify(restored)).toBe(JSON.stringify(draft));
  });

  it("keeps other lanes when one lane changes", () => {
    const doc = withLaneState(draft, "audio", { locked: true });
    const next = withLaneState(doc, "hook", { hidden: true });
    expect(next.lanes).toEqual({
      audio: { hidden: false, locked: true },
      hook: { hidden: true, locked: false },
    });
    const unlocked = withLaneState(next, "audio", { locked: false });
    expect(unlocked.lanes).toEqual({ hook: { hidden: true, locked: false } });
  });

  it("locking never hides and hiding never locks", () => {
    const locked = withLaneState(draft, "video", { locked: true });
    expect(laneHidden(locked, "video")).toBe(false);
    const hidden = withLaneState(draft, "video", { hidden: true });
    expect(laneLocked(hidden, "video")).toBe(false);
  });
});

describe("Lane state in exports (D7)", () => {
  it("keeps captions out of the export when their lane is hidden", () => {
    expect(captionsInExport(draft)).toBe(draft.captions.enabled);
    const hidden = withLaneState(draft, "captions", { hidden: true });
    expect(captionsInExport(hidden)).toBe(false);
    const disabled: EditDocument = {
      ...draft,
      captions: { ...draft.captions, enabled: false },
    };
    expect(captionsInExport(disabled)).toBe(false);
  });

  it("silences the export when the audio lane is hidden, like mute", () => {
    const quiet: EditDocument = { ...draft, volume: 0.25 };
    expect(exportGain(quiet)).toBe(0.25);
    expect(exportGain(withLaneState(quiet, "audio", { hidden: true }))).toBe(0);
    expect(exportGain({ ...quiet, muted: true })).toBe(0);
  });
});
