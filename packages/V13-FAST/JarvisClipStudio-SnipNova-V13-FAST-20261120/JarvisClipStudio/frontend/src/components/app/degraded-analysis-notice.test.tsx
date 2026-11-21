import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import taskMessages from "../../../messages/en/task.json";
import {
  DegradedAnalysisNotice,
  clipsPickedWithoutAiAnalysis,
} from "./degraded-analysis-notice";

// The exact reasoning shapes the backend floor stamps onto its clips
// (visual_moments: "Visual selection (<context>): ...").
const DEGRADED = {
  reasoning:
    "Visual selection (AI analysis unavailable): audio energy peak stands out from its surroundings; window 22:04\u201322:29.",
};
const NO_TRANSCRIPT = {
  reasoning:
    "Visual selection (no usable transcript): scene cut followed by an audio energy peak; window 22:04\u201322:29.",
};
const AI_PICKED = {
  reasoning: "The speaker lands the hook, then proves it with a chart.",
};

function renderNotice(status: string, clips: { reasoning?: string | null }[]) {
  return render(
    <NextIntlClientProvider locale="en" messages={{ task: taskMessages }}>
      <DegradedAnalysisNotice status={status} clips={clips} />
    </NextIntlClientProvider>,
  );
}

describe("DegradedAnalysisNotice", () => {
  it("shows the amber notice on a completed task whose clips the floor picked during an AI outage", () => {
    renderNotice("completed", [AI_PICKED, DEGRADED]);

    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("Picked without AI analysis");
    expect(notice).toHaveTextContent("AI analysis was unavailable");
    expect(notice).toHaveTextContent("not saved as an analysis");
  });

  it("renders nothing for a completed task with normal AI-picked clips", () => {
    const { container } = renderNotice("completed", [AI_PICKED]);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("renders nothing for the no-usable-transcript floor \u2014 that path is by design, not a degradation", () => {
    const { container } = renderNotice("completed", [NO_TRANSCRIPT]);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing before the task completes, even with degraded clips", () => {
    const { container } = renderNotice("processing", [DEGRADED]);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("clipsPickedWithoutAiAnalysis", () => {
  it("matches only the AI-unavailable marker, never the no-transcript context", () => {
    expect(clipsPickedWithoutAiAnalysis([DEGRADED])).toBe(true);
    expect(clipsPickedWithoutAiAnalysis([NO_TRANSCRIPT])).toBe(false);
    expect(clipsPickedWithoutAiAnalysis([AI_PICKED])).toBe(false);
  });

  it("tolerates missing reasoning and empty clip lists", () => {
    expect(clipsPickedWithoutAiAnalysis([])).toBe(false);
    expect(clipsPickedWithoutAiAnalysis([{ reasoning: null }, {}])).toBe(false);
  });
});
