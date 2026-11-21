import { describe, expect, it } from "vitest";

import {
  CREATION_MODE_OPTIONS,
  MANUAL_CUTTER_MODE,
  parseManualRanges,
} from "./creation-modes";

describe("creation mode options", () => {
  it("lists exactly the four named workflows", () => {
    expect(CREATION_MODE_OPTIONS.map((option) => option.value)).toEqual([
      "full_auto",
      "moments_manual",
      "moments_ai",
      "manual_cutter_ai",
    ]);
    expect(MANUAL_CUTTER_MODE).toBe("manual_cutter_ai");
  });

  it("gives every mode an honest one-line description", () => {
    for (const option of CREATION_MODE_OPTIONS) {
      expect(option.label.length).toBeGreaterThan(0);
      expect(option.blurb.length).toBeGreaterThan(20);
    }
  });

  it("warns on the Find Moments cards that the wait is time-boxed", () => {
    // A parked pick is not forever: the card must say up front that
    // the task continues with the top picks if nobody picks in time.
    const note =
      "Pauses and waits for you to pick the clips — if you don't pick within 15 minutes, it continues with the top picks automatically.";
    for (const value of ["moments_manual", "moments_ai"]) {
      const option = CREATION_MODE_OPTIONS.find(
        (candidate) => candidate.value === value,
      );
      expect(option?.blurb).toContain(note);
    }
    for (const value of ["full_auto", "manual_cutter_ai"]) {
      const option = CREATION_MODE_OPTIONS.find(
        (candidate) => candidate.value === value,
      );
      expect(option?.blurb).not.toContain("top picks automatically");
    }
  });
});

describe("parseManualRanges", () => {
  it("parses MM:SS ranges and sorts them", () => {
    const parsed = parseManualRanges("1:30-2:00\n0:10-0:25");
    expect(parsed.error).toBeNull();
    expect(parsed.ranges).toEqual([
      { start: 10, end: 25 },
      { start: 90, end: 120 },
    ]);
  });

  it("parses plain seconds and HH:MM:SS", () => {
    expect(parseManualRanges("83-112").ranges).toEqual([
      { start: 83, end: 112 },
    ]);
    expect(parseManualRanges("1:00:00 - 1:00:30").ranges).toEqual([
      { start: 3600, end: 3630 },
    ]);
  });

  it("rejects empty input for the cutter contract", () => {
    expect(parseManualRanges("  \n").error).toMatch(/at least one/);
  });

  it("rejects unreadable, too-short and overlapping ranges", () => {
    expect(parseManualRanges("soon-later").error).toMatch(/Could not read/);
    expect(parseManualRanges("0:10-0:10").error).toMatch(/at least 1 second/);
    expect(parseManualRanges("0:10-0:30\n0:25-0:40").error).toMatch(
      /must not overlap/,
    );
  });

  it("rejects more than 20 ranges", () => {
    const text = Array.from({ length: 21 }, (_, i) => `${i * 10}-${i * 10 + 5}`).join("\n");
    expect(parseManualRanges(text).error).toMatch(/At most 20/);
  });
});
