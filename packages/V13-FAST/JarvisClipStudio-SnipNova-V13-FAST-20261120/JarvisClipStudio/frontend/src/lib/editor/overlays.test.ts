import { describe, expect, it } from "vitest";
import {
  insertOverlay,
  mappedOverlays,
  overlayAt,
  overlayGap,
  overlayPieceAt,
  overlaysOf,
  placeOverlay,
  placeOverlayOnTimeline,
  type EditDocument,
  type OverlayItem,
} from "./document";
import { draft } from "./document.test-fixture";

const ASSET = "overlay-" + "a".repeat(32) + ".png";
const item = (id: string, start: number, end: number, rest: Partial<OverlayItem> = {}): OverlayItem => ({
  id,
  start,
  end,
  asset: ASSET,
  name: "sticker.png",
  x: 0.5,
  y: 0.5,
  scale: 0.4,
  ...rest,
});
const withOverlays = (overlays: OverlayItem[], segments = [{ id: "s", start: 0, end: 20 }]): EditDocument => ({
  ...draft,
  segments,
  overlays,
});

describe("Overlays on the timeline", () => {
  it("treats a document without the field as an empty lane", () => {
    expect(overlaysOf(draft)).toEqual([]);
    expect(mappedOverlays(draft)).toEqual([]);
    expect(overlayAt(draft, 1)).toBeNull();
  });

  it("follows cuts and keeps showing through them, matching the export", () => {
    const doc = withOverlays([item("a", 1, 4)], [
      { id: "one", start: 0, end: 2 },
      { id: "two", start: 3, end: 6 },
    ]);
    const pieces = mappedOverlays(doc).map(({ start, end, sourceStart }) => ({ start, end, sourceStart }));
    expect(pieces).toEqual([
      { start: 1, end: 2, sourceStart: 1 },
      { start: 2, end: 3, sourceStart: 3 },
    ]);
  });

  it("disappears when its window is cut out", () => {
    const doc = withOverlays([item("a", 2, 3)], [
      { id: "one", start: 0, end: 2 },
      { id: "two", start: 3, end: 6 },
    ]);
    expect(mappedOverlays(doc)).toEqual([]);
  });

  it("finds the item on screen at a source time", () => {
    const doc = withOverlays([item("a", 1, 3), item("b", 5, 7)]);
    expect(overlayAt(doc, 2)?.id).toBe("a");
    expect(overlayAt(doc, 3)).toBeNull();
    expect(overlayAt(doc, 6)?.id).toBe("b");
  });

  it("bounds the free gap by its neighbours", () => {
    const doc = withOverlays([item("a", 1, 3), item("b", 6, 8)]);
    expect(overlayGap(doc, 4, 20)).toEqual({ start: 3, end: 6 });
    expect(overlayGap(doc, 2, 20)).toBeNull();
    expect(overlayGap(doc, 0.5, 20)).toEqual({ start: 0, end: 1 });
  });

  it("inserts an upload centred at the default size, inside the gap", () => {
    const doc = withOverlays([item("a", 1, 3)]);
    const placed = insertOverlay(doc, ASSET, "logo.png", 5, 20);
    expect(placed).not.toBeNull();
    const added = overlaysOf(placed!.doc).find((o) => o.id === placed!.id)!;
    expect(added).toMatchObject({ start: 5, end: 8, x: 0.5, y: 0.5, scale: 0.4, name: "logo.png" });
    expect(insertOverlay(doc, ASSET, "logo.png", 2, 20)).toBeNull();
  });

  it("moves and trims without crossing a neighbour or the end", () => {
    const doc = withOverlays([item("a", 1, 3), item("b", 6, 8)]);
    const moved = placeOverlay(doc, "a", 5, 7, 20);
    expect(overlaysOf(moved).find((o) => o.id === "a")).toMatchObject({ start: 4, end: 6 });
    const trimmed = placeOverlay(doc, "a", 1, 9, 20);
    expect(overlaysOf(trimmed).find((o) => o.id === "a")).toMatchObject({ start: 1, end: 6 });
    const pastEnd = placeOverlay(doc, "b", 19, 21, 20);
    expect(overlaysOf(pastEnd).find((o) => o.id === "b")).toMatchObject({ start: 18, end: 20 });
  });

  it("drags a piece across a cut back onto the source timeline", () => {
    const doc = withOverlays(
      [item("a", 1, 4)],
      [
        { id: "one", start: 0, end: 2 },
        { id: "two", start: 3, end: 6 },
      ],
    );
    const piece = mappedOverlays(doc)[1];
    const dragged = placeOverlayOnTimeline(doc, "a", piece, "move", 0.5, 6);
    expect(overlaysOf(dragged)[0]).toMatchObject({ start: 1.5, end: 4.5 });
  });
});

describe("The overlay piece at an output time (on-device export)", () => {
  it("finds the piece covering a time and nothing outside its window", () => {
    const pieces = mappedOverlays(withOverlays([item("a", 2, 5)]));
    expect(overlayPieceAt(pieces, 1.999)).toBeNull();
    expect(overlayPieceAt(pieces, 2)?.item.id).toBe("a");
    expect(overlayPieceAt(pieces, 4.999)?.item.id).toBe("a");
    expect(overlayPieceAt(pieces, 5)).toBeNull();
    expect(overlayPieceAt([], 3)).toBeNull();
  });

  it("follows the piece across a cut, like the server's mapped overlays", () => {
    const doc = withOverlays(
      [item("a", 1, 5)],
      [
        { id: "one", start: 0, end: 2 },
        { id: "two", start: 3, end: 6 },
      ],
    );
    const pieces = mappedOverlays(doc);
    // The item covers source 1-2 of segment one (output 1-2) and
    // source 3-5 of segment two (output 2-4).
    expect(overlayPieceAt(pieces, 0.5)).toBeNull();
    expect(overlayPieceAt(pieces, 1.5)?.item.id).toBe("a");
    expect(overlayPieceAt(pieces, 2.5)?.item.id).toBe("a");
    expect(overlayPieceAt(pieces, 4)).toBeNull();
  });
});
