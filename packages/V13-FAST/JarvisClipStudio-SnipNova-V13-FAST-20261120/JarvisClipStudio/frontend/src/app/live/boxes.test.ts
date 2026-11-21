import { describe, expect, it } from "vitest";

import {
  boxesPayload,
  channelKeyForUrl,
  coverCropPx,
  dragBox,
  isUsableBox,
  loadSavedBoxes,
  pointerToFraction,
  saveBoxes,
  storageKeyForUrl,
  type ManualBoxes,
} from "./boxes";

const CAM = { x: 0.7, y: 0, w: 0.3, h: 0.35 };
const GAME = { x: 0, y: 0, w: 1, h: 1 };

describe("pointerToFraction", () => {
  const rect = { left: 100, top: 50, width: 640, height: 360 };
  it("converts pointer positions to frame fractions", () => {
    expect(pointerToFraction(100, 50, rect)).toEqual({ fx: 0, fy: 0 });
    expect(pointerToFraction(420, 230, rect)).toEqual({ fx: 0.5, fy: 0.5 });
    expect(pointerToFraction(740, 410, rect)).toEqual({ fx: 1, fy: 1 });
  });
  it("clamps outside the element", () => {
    expect(pointerToFraction(0, 0, rect)).toEqual({ fx: 0, fy: 0 });
    expect(pointerToFraction(9999, 9999, rect)).toEqual({ fx: 1, fy: 1 });
  });
});

describe("dragBox", () => {
  it("normalizes a drag in any direction", () => {
    expect(dragBox({ fx: 0.7, fy: 0.1 }, { fx: 0.95, fy: 0.4 })).toEqual({ x: 0.7, y: 0.1, w: 0.25, h: 0.3 });
    expect(dragBox({ fx: 0.95, fy: 0.4 }, { fx: 0.7, fy: 0.1 })).toEqual({ x: 0.7, y: 0.1, w: 0.25, h: 0.3 });
  });
});

describe("isUsableBox / boxesPayload", () => {
  it("uses the backend's 0.03 minimum edge", () => {
    expect(isUsableBox({ x: 0, y: 0, w: 0.03, h: 0.03 })).toBe(true);
    expect(isUsableBox({ x: 0, y: 0, w: 0.029, h: 0.5 })).toBe(false);
    expect(isUsableBox(null)).toBe(false);
  });
  it("payload is null until both boxes are drawn", () => {
    expect(boxesPayload(null, GAME, "top")).toBeNull();
    expect(boxesPayload(CAM, null, "top")).toBeNull();
    expect(boxesPayload(CAM, { ...GAME, w: 0.01 }, "top")).toBeNull();
    expect(boxesPayload(CAM, GAME, "bottom")).toEqual({ cam: CAM, game: GAME, arrange: "bottom" });
  });
});

describe("channel keys + saved boxes", () => {
  it("keys by channel for twitch and youtube", () => {
    expect(channelKeyForUrl("https://www.twitch.tv/KaiCenat")).toBe("twitch:kaicenat");
    expect(channelKeyForUrl("https://www.youtube.com/@KaiCenat/streams")).toBe("youtube:@kaicenat");
    expect(channelKeyForUrl("https://www.youtube.com/watch?v=abc123")).toBe("youtube:abc123");
    expect(channelKeyForUrl("https://youtu.be/abc123")).toBe("youtube:abc123");
  });

  function fakeStorage() {
    const map = new Map<string, string>();
    return {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => void map.set(k, v),
      map,
    };
  }

  it("round-trips through storage under the channel key", () => {
    const storage = fakeStorage();
    const boxes: ManualBoxes = { cam: CAM, game: GAME, arrange: "bottom" };
    saveBoxes(storage, "https://www.twitch.tv/kaicenat", boxes);
    expect(storage.map.has(storageKeyForUrl("https://www.twitch.tv/KaiCenat"))).toBe(true);
    expect(loadSavedBoxes(storage, "https://www.twitch.tv/KaiCenat")).toEqual(boxes);
    expect(loadSavedBoxes(storage, "https://www.twitch.tv/someoneelse")).toBeNull();
  });

  it("ignores corrupt or undersized saved values", () => {
    const storage = fakeStorage();
    storage.setItem(storageKeyForUrl("https://www.twitch.tv/x"), "{not json");
    expect(loadSavedBoxes(storage, "https://www.twitch.tv/x")).toBeNull();
    storage.setItem(storageKeyForUrl("https://www.twitch.tv/x"),
      JSON.stringify({ cam: { x: 0, y: 0, w: 0.01, h: 0.5 }, game: GAME, arrange: "top" }));
    expect(loadSavedBoxes(storage, "https://www.twitch.tv/x")).toBeNull();
  });
});

describe("coverCropPx (mirrors the backend's _cover_crop)", () => {
  it("matches the backend's crops for the render-test boxes on 1280x720", () => {
    // Face panel aspect 1080/864, game panel aspect 1080/1056 — the same
    // numbers backend/tests/test_live_boxes.py asserts from Python.
    expect(coverCropPx(CAM, 1280, 720, 1080 / 864)).toEqual({ x: 930, y: 0, w: 314, h: 250 });
    expect(coverCropPx(GAME, 1280, 720, 1080 / 1056)).toEqual({ x: 272, y: 0, w: 736, h: 720 });
  });
  it("keeps the crop inside the frame with even sizes", () => {
    const crop = coverCropPx({ x: 0.9, y: 0.9, w: 0.1, h: 0.1 }, 1280, 720, 1.25);
    expect(crop.x + crop.w).toBeLessThanOrEqual(1280);
    expect(crop.y + crop.h).toBeLessThanOrEqual(720);
    expect(crop.w % 2).toBe(0);
    expect(crop.h % 2).toBe(0);
  });
});
