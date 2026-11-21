import {
  buildEditRequest,
  buildLookOverrides,
  captionsChanged,
  moveSegmentDown,
  moveSegmentUp,
  splitAtMidpoint,
  trimSegment,
  type EditorSegment,
  type GradeValues,
  type LookState,
  type VfxValues,
} from "./clip-edit";

function segment(index: number, start: number, end: number): EditorSegment {
  return {
    index,
    start,
    end,
    duration: end - start,
    transition_in: index === 0 ? "none" : "fade",
    transition_duration: index === 0 ? 0 : 0.4,
  };
}

const plan = () => [segment(0, 0, 4), segment(1, 4, 8), segment(2, 8, 12)];

describe("clip edit request building", () => {
  it("returns null when nothing changed", () => {
    expect(buildEditRequest(plan(), plan(), null)).toBeNull();
    expect(buildEditRequest(plan(), plan(), {})).toBeNull();
  });

  it("emits trims for edited rows and deletes in descending index order", () => {
    const rows = [segment(0, 0, 3), segment(2, 8, 12)];
    const request = buildEditRequest(plan(), rows, null);
    expect(request?.operations).toEqual([
      { type: "trim", index: 0, start: 0, end: 3 },
      { type: "delete", index: 1 },
    ]);
  });

  it("retimes rows to the new layout and appends a reorder operation", () => {
    // Rows carry their retimed windows (the panel retiles after a move), so
    // changed windows arrive as trims plus the final reorder.
    const rows = [segment(2, 0, 4), segment(0, 4, 8), segment(1, 8, 12)];
    const request = buildEditRequest(plan(), rows, null);
    expect(request?.operations).toEqual([
      { type: "trim", index: 0, start: 4, end: 8 },
      { type: "trim", index: 1, start: 8, end: 12 },
      { type: "trim", index: 2, start: 0, end: 4 },
      { type: "reorder", order: [2, 0, 1] },
    ]);
  });

  it("includes caption overrides when present", () => {
    const request = buildEditRequest(plan(), plan(), { template: "hormozi" });
    expect(request).toEqual({ captions: { template: "hormozi" } });
  });

  it("carries a words-per-line the user set in the caption studio", () => {
    // N4: the quick editor's new grouping control reaches the request
    // the backend validates — the value travels untouched here, the
    // 1-8 clamp lives in normalize_caption_overrides.
    const request = buildEditRequest(plan(), plan(), { words_per_line: 2 });
    expect(request).toEqual({ captions: { words_per_line: 2 } });
  });
});

describe("segment operation helpers", () => {
  it("builds reorder operations for moves and rejects edge moves", () => {
    const segments = plan();
    expect(moveSegmentUp(segments, 1)).toEqual({ type: "reorder", order: [1, 0, 2] });
    expect(moveSegmentDown(segments, 1)).toEqual({ type: "reorder", order: [0, 2, 1] });
    expect(moveSegmentUp(segments, 0)).toBeNull();
    expect(moveSegmentDown(segments, 2)).toBeNull();
  });

  it("splits at the midpoint", () => {
    expect(splitAtMidpoint(segment(1, 4, 8))).toEqual({
      type: "split",
      index: 1,
      at: 6,
    });
  });

  it("clamps trims to the segment window and to the minimum length", () => {
    const target = segment(0, 2, 6);
    expect(trimSegment(target, 0, 10)).toBeNull(); // no change after clamping
    expect(trimSegment(target, 2.5, 5)).toEqual({
      type: "trim",
      index: 0,
      start: 2.5,
      end: 5,
    });
    expect(trimSegment(target, 5.8, 6)).toBeNull(); // below 0.5s minimum
  });
});

describe("caption change detection", () => {
  it("compares override fields", () => {
    expect(captionsChanged(null, {})).toBe(false);
    expect(captionsChanged({ template: "default" }, { template: "default" })).toBe(false);
    expect(captionsChanged({ template: "default" }, { template: "hormozi" })).toBe(true);
    expect(captionsChanged({ font_size: 32 }, {})).toBe(true);
  });

  it("notices a words-per-line change and ignores an unchanged one", () => {
    // N4: the panel's Apply button only fires when the draft differs —
    // a grouping change must count, and an identical grouping must not.
    expect(captionsChanged({ words_per_line: 3 }, { words_per_line: 2 })).toBe(
      true,
    );
    expect(captionsChanged({ words_per_line: 2 }, { words_per_line: 2 })).toBe(
      false,
    );
  });
});

describe("studio look diffing", () => {
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

  const draftOf = (
    patch: {
      transition?: string;
      grade?: Partial<GradeValues>;
      vfx?: Partial<VfxValues>;
    } = {},
  ) => ({
    transition: patch.transition ?? initial.transition,
    grade: { ...initial.grade, ...(patch.grade ?? {}) },
    vfx: { ...initial.vfx, ...(patch.vfx ?? {}) },
  });

  it("returns null when the draft matches the current look", () => {
    expect(buildLookOverrides(initial, draftOf())).toBeNull();
  });

  it("sends only the grade fields the user actually moved", () => {
    const look = buildLookOverrides(initial, draftOf({ grade: { saturation: 60 } }));
    expect(look).toEqual({ grade: { saturation: 60 } });
  });

  it("keeps an explicit zero (overriding the profile base back to neutral)", () => {
    const look = buildLookOverrides(initial, draftOf({ grade: { saturation: 0 } }));
    expect(look).toEqual({ grade: { saturation: 0 } });
  });

  it("sends a changed transition and changed effects together", () => {
    const look = buildLookOverrides(
      initial,
      draftOf({ transition: "wipeleft", vfx: { grain: 30, letterbox: 0 } }),
    );
    expect(look).toEqual({ transition: "wipeleft", vfx: { grain: 30 } });
  });

  it("sends an effect turned back to zero so it is removed", () => {
    const withGrain: LookState = {
      ...initial,
      vfx: { ...initial.vfx, grain: 30 },
      stored: true,
    };
    const look = buildLookOverrides(withGrain, {
      transition: withGrain.transition,
      grade: { ...withGrain.grade },
      vfx: { ...withGrain.vfx, grain: 0 },
    });
    expect(look).toEqual({ vfx: { grain: 0 } });
  });

  it("sends a picked LUT as the grade's lut field", () => {
    const look = buildLookOverrides(initial, { ...draftOf(), lut: "teal.cube" });
    expect(look).toEqual({ grade: { lut: "teal.cube" } });
  });

  it("sends lut: null when the user clears an active LUT", () => {
    const withLut: LookState = { ...initial, lut: "teal.cube", stored: true };
    const look = buildLookOverrides(withLut, { ...draftOf(), lut: null });
    expect(look).toEqual({ grade: { lut: null } });
  });

  it("keeps the active LUT when only a slider moves", () => {
    const withLut: LookState = { ...initial, lut: "teal.cube", stored: true };
    const look = buildLookOverrides(withLut, {
      ...draftOf({ grade: { saturation: 60 } }),
      lut: "teal.cube",
    });
    expect(look).toEqual({ grade: { saturation: 60, lut: "teal.cube" } });
  });

  it("returns null when the draft keeps the same LUT and nothing else changes", () => {
    const withLut: LookState = { ...initial, lut: "teal.cube", stored: true };
    expect(
      buildLookOverrides(withLut, { ...draftOf(), lut: "teal.cube" }),
    ).toBeNull();
  });
});
