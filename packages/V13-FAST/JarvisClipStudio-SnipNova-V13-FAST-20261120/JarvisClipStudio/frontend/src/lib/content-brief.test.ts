import { describe, expect, it } from "vitest";

import {
  buildBriefPayload,
  emptyFlagPointDraft,
  emptyKeyTimestampDraft,
  emptyPanelState,
  emptyRangeDraft,
  emptyRequirementDraft,
  formatTimecode,
  parseTimecodeInput,
  pointDraftError,
  rangeDraftError,
  requiresMajorFocus,
  storedBriefToPanelState,
} from "./content-brief";

describe("parseTimecodeInput (backend mirror)", () => {
  it("accepts plain seconds, m:ss and h:mm:ss", () => {
    expect(parseTimecodeInput("90")).toBe(90);
    expect(parseTimecodeInput("1:30")).toBe(90);
    expect(parseTimecodeInput("176:29")).toBe(176 * 60 + 29);
    expect(parseTimecodeInput("2:56:29")).toBe(2 * 3600 + 56 * 60 + 29);
    expect(parseTimecodeInput("1:30.5")).toBeCloseTo(90.5);
    expect(parseTimecodeInput(42)).toBe(42);
  });

  it("rejects out-of-range components and junk", () => {
    expect(parseTimecodeInput("1:75")).toBeNull();
    expect(parseTimecodeInput("1:75:00")).toBeNull();
    expect(parseTimecodeInput("abc")).toBeNull();
    expect(parseTimecodeInput("")).toBeNull();
    expect(parseTimecodeInput("-5")).toBeNull();
    expect(parseTimecodeInput("1:2:3:4")).toBeNull();
  });
});

describe("formatTimecode", () => {
  it("formats like the backend labels", () => {
    expect(formatTimecode(0)).toBe("00:00");
    expect(formatTimecode(65)).toBe("01:05");
    expect(formatTimecode(180)).toBe("03:00");
    expect(formatTimecode(10589)).toBe("2:56:29");
  });
});

describe("requiresMajorFocus (backend mirror)", () => {
  it("matches the server's major-focus wording", () => {
    expect(requiresMajorFocus("Expedia must be a major focus")).toBe(true);
    expect(requiresMajorFocus("Expedia is the main topic")).toBe(true);
    expect(requiresMajorFocus("You must mention Expedia")).toBe(true);
    expect(requiresMajorFocus("Talk about hotels sometimes")).toBe(false);
  });
});

describe("buildBriefPayload", () => {
  it("returns null for an empty panel", () => {
    expect(buildBriefPayload(emptyPanelState())).toBeNull();
  });

  it("skips blank rows and normalises ranges", () => {
    const state = emptyPanelState();
    state.requirements.push(emptyRequirementDraft());
    state.ranges.push(emptyRangeDraft({ start: " 2:00 ", end: "" }));
    const payload = buildBriefPayload(state);
    expect(payload?.requirements).toEqual([]);
    expect(payload?.allowed_ranges).toEqual([{ start: "2:00", end: null }]);
  });
});

describe("rangeDraftError", () => {
  it("flags invalid and reversed ranges only", () => {
    expect(rangeDraftError(emptyRangeDraft())).toBeNull();
    expect(rangeDraftError(emptyRangeDraft({ start: "abc" }))).toBe("invalidStart");
    expect(rangeDraftError(emptyRangeDraft({ start: "1:00", end: "x" }))).toBe(
      "invalidEnd"
    );
    expect(
      rangeDraftError(emptyRangeDraft({ start: "2:00", end: "1:00" }))
    ).toBe("endBeforeStart");
    expect(rangeDraftError(emptyRangeDraft({ start: "1:00", end: "2:00" }))).toBeNull();
  });
});

describe("buildBriefPayload — screenshot brief fields", () => {
  it("returns null when only blank new-family rows exist", () => {
    const state = emptyPanelState();
    state.excludedRanges.push(emptyRangeDraft());
    state.flagPoints.push(emptyFlagPointDraft());
    state.keyTimestamps.push(emptyKeyTimestampDraft());
    expect(buildBriefPayload(state)).toBeNull();
  });

  it("carries excluded ranges, flag points and key timestamps", () => {
    const state = emptyPanelState();
    state.excludedRanges.push(emptyRangeDraft({ start: "0:47", end: "0:53" }));
    state.flagPoints.push(
      emptyFlagPointDraft({ at: "3:13", category: "licence_plate", note: " blur/crop " })
    );
    state.keyTimestamps.push(
      emptyKeyTimestampDraft({ at: "1:30", description: "How diamonds helped" })
    );
    const payload = buildBriefPayload(state);
    expect(payload?.excluded_ranges).toEqual([{ start: "0:47", end: "0:53" }]);
    expect(payload?.flag_points).toEqual([
      { at: "3:13", category: "licence_plate", note: "blur/crop" },
    ]);
    expect(payload?.key_timestamps).toEqual([
      { at: "1:30", description: "How diamonds helped" },
    ]);
  });

  it("omits the new keys entirely when the families are empty", () => {
    const state = emptyPanelState();
    state.ranges.push(emptyRangeDraft({ start: "2:00", end: "" }));
    const payload = buildBriefPayload(state);
    expect(payload).not.toHaveProperty("excluded_ranges");
    expect(payload).not.toHaveProperty("flag_points");
    expect(payload).not.toHaveProperty("key_timestamps");
  });
});

describe("pointDraftError", () => {
  it("flags only unparseable non-blank times", () => {
    expect(pointDraftError("")).toBeNull();
    expect(pointDraftError("3:13")).toBeNull();
    expect(pointDraftError("soon")).toBe("invalidTime");
  });
});

describe("storedBriefToPanelState", () => {
  it("round-trips the screenshot brief fields", () => {
    const state = storedBriefToPanelState({
      version: 2,
      requirements: [],
      allowed_ranges: [],
      excluded_ranges: [
        { start_seconds: 47, end_seconds: 53, start_label: "0:47", end_label: "0:53" },
      ],
      flag_points: [
        { at_seconds: 193, at_label: "3:13", category: "licence_plate", note: "blur/crop" },
        { at_seconds: 26, at_label: "0:26", category: "mystery", note: "war" },
      ],
      key_timestamps: [
        { at_seconds: 90, at_label: "1:30", description: "How diamonds helped" },
      ],
    });
    const payload = buildBriefPayload(state);
    expect(payload?.excluded_ranges).toEqual([{ start: "0:47", end: "0:53" }]);
    expect(payload?.flag_points).toEqual([
      { at: "3:13", category: "licence_plate", note: "blur/crop" },
      // Unknown categories coerce to "other", mirroring the backend.
      { at: "0:26", category: "other", note: "war" },
    ]);
    expect(payload?.key_timestamps).toEqual([
      { at: "1:30", description: "How diamonds helped" },
    ]);
  });

  it("round-trips a stored brief through the payload builder", () => {
    const state = storedBriefToPanelState({
      version: 2,
      requirements: [
        {
          id: "r1",
          type: "topic_focus",
          text: "Expedia must be a major focus",
          kind: "mandatory",
          topics: ["Expedia"],
          requires_major: true,
        },
      ],
      allowed_ranges: [
        {
          start_seconds: 10589,
          end_seconds: null,
          start_label: "2:56:29",
          end_label: null,
        },
      ],
    });
    const payload = buildBriefPayload(state);
    expect(payload?.requirements).toEqual([
      {
        type: "topic_focus",
        text: "Expedia must be a major focus",
        kind: "mandatory",
        topics: ["Expedia"],
        requires_major: true,
      },
    ]);
    expect(payload?.allowed_ranges).toEqual([{ start: "2:56:29", end: null }]);
  });
});
