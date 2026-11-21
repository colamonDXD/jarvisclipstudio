import { describe, expect, it } from "vitest";

import { runSummaryParts } from "./run-summary";

const fmt = (seconds: number) => `${Math.floor(seconds / 60)}:${String(
  Math.floor(seconds % 60)
).padStart(2, "0")}`;

const clips = [{ duration: 30 }, { duration: 65.9 }, { duration: null }];

describe("runSummaryParts", () => {
  it("summarises a completed run from the stored choices", () => {
    expect(
      runSummaryParts(
        { status: "completed", template_mode: "auto", creation_mode: "moments_ai" },
        clips,
        fmt
      )
    ).toEqual([
      { label: "Clips", value: "3" },
      { label: "Total length", value: "1:35" },
      { label: "Template", value: "Auto template" },
      { label: "Mode", value: "Find Moments + AI Editing" },
    ]);
  });

  it("labels the Plain template", () => {
    const parts = runSummaryParts(
      { status: "completed", template_mode: "plain", creation_mode: "full_auto" },
      clips,
      fmt
    );
    expect(parts).toContainEqual({ label: "Template", value: "Plain — just cuts" });
    expect(parts).toContainEqual({ label: "Mode", value: "Full AutoClip" });
  });

  it("omits parts whose fields the payload does not carry", () => {
    expect(
      runSummaryParts({ status: "completed", template_mode: null, creation_mode: null }, clips, fmt)
    ).toEqual([
      { label: "Clips", value: "3" },
      { label: "Total length", value: "1:35" },
    ]);
  });

  it("omits a part whose value this build does not know", () => {
    const parts = runSummaryParts(
      { status: "completed", template_mode: "future", creation_mode: "future_mode" },
      clips,
      fmt
    );
    expect(parts).toEqual([
      { label: "Clips", value: "3" },
      { label: "Total length", value: "1:35" },
    ]);
  });

  it("returns null unless the task completed with clips", () => {
    expect(runSummaryParts({ status: "processing" }, clips, fmt)).toBeNull();
    expect(runSummaryParts({ status: "error" }, clips, fmt)).toBeNull();
    expect(runSummaryParts({ status: "completed" }, [], fmt)).toBeNull();
    expect(runSummaryParts({ status: "completed" }, null, fmt)).toBeNull();
    expect(runSummaryParts(null, clips, fmt)).toBeNull();
    expect(runSummaryParts(undefined, clips, fmt)).toBeNull();
  });
});
