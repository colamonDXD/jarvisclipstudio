import { describe, expect, it } from "vitest";

import { createSummaryParts } from "./create-summary";

const base = {
  sourceLabel: null as string | null,
  creationModeLabel: "Full AutoClip",
  templateLabel: "Plain — just cuts",
  framingLabel: "Auto",
  nicheLabel: "Auto-detect",
  outputFormatLabel: "Auto 9:16",
};

describe("createSummaryParts", () => {
  it("lists the choices in form order without a source", () => {
    expect(createSummaryParts(base)).toEqual([
      { label: "Mode", value: "Full AutoClip" },
      { label: "Template", value: "Plain — just cuts" },
      { label: "Framing", value: "Auto" },
      { label: "Style", value: "Auto-detect" },
      { label: "Format", value: "Auto 9:16" },
    ]);
  });

  it("puts the source first when one is set", () => {
    const parts = createSummaryParts({
      ...base,
      sourceLabel: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    });
    expect(parts[0]).toEqual({
      label: "Source",
      value: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    });
    expect(parts).toHaveLength(6);
  });

  it("passes the selected values through unchanged", () => {
    expect(
      createSummaryParts({
        sourceLabel: "vod.mp4",
        creationModeLabel: "Find Moments + AI Editing",
        templateLabel: "Auto template",
        framingLabel: "Fit (whole picture)",
        nicheLabel: "Gaming",
        outputFormatLabel: "Auto 9:16",
      })
    ).toEqual([
      { label: "Source", value: "vod.mp4" },
      { label: "Mode", value: "Find Moments + AI Editing" },
      { label: "Template", value: "Auto template" },
      { label: "Framing", value: "Fit (whole picture)" },
      { label: "Style", value: "Gaming" },
      { label: "Format", value: "Auto 9:16" },
    ]);
  });
});
