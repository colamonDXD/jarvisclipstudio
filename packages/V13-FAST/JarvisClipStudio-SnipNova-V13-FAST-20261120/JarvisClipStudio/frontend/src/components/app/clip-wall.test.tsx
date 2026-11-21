import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import messages from "../../../messages/en/task.json";

import { ClipTile, ProcessingPanel, type WallClip } from "./clip-wall";

function wallClip(overrides: Partial<WallClip> = {}): WallClip {
  return {
    id: "clip-1",
    filename: "clip-1.mp4",
    start_time: "00:10",
    end_time: "00:40",
    duration: 30,
    text: "Nobody believes this part.",
    relevance_score: 0.9,
    clip_order: 1,
    video_url: "/clips/clip-1.mp4",
    virality_score: 82,
    hook_score: 20,
    engagement_score: 21,
    value_score: 20,
    shareability_score: 21,
    hook_type: "punchline",
    hook_title: "The answer nobody expected",
    ...overrides,
  };
}

describe("clip tile hook report", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the hook type and reason from the clip's hook report", () => {
    render(
      <ClipTile
        clip={wallClip({
          hook_report: {
            type: "punchline",
            timestamp: 4.5,
            why: "The deadpan answer lands out of nowhere.",
            opening_strategy: "subtle punch zoom on the hook",
            confidence: 0.8,
          },
        })}
        taskId="task-1"
        busy={false}
        best={false}
        editable={false}
        onOpen={() => undefined}
        onDownload={() => undefined}
      />
    );
    expect(
      screen.getByText("Hook: Punchline — The deadpan answer lands out of nowhere.")
    ).toBeVisible();
  });

  it("shows the editorial aggregate as the tile headline when reported", () => {
    render(
      <ClipTile
        clip={wallClip({
          editorial_quality: {
            label: "Editorial quality",
            note: "Editorial quality assessment only, based on the evidence cited for each dimension.",
            aggregate: 64,
            dimensions: {
              hook_strength: { label: "Hook strength", score: 80, reason: "transcript-analysis hook sub-score 20/25", source: "llm" },
              visual_quality: { label: "Visual quality", score: null, reason: "not measured", source: null },
            },
            scored_dimensions: 1,
            legacy_selection_total: 82,
          },
        })}
        taskId="task-1"
        busy={false}
        best={false}
        editable={false}
        onOpen={() => undefined}
        onDownload={() => undefined}
      />
    );
    expect(screen.getByLabelText("Score 64 out of 100")).toBeVisible();
    expect(screen.queryByLabelText("Score 82 out of 100")).not.toBeInTheDocument();
  });

  it("hides the hook line when there is no strong hook or no report", () => {
    const { rerender } = render(
      <ClipTile
        clip={wallClip({
          hook_report: {
            type: "none_strong",
            timestamp: 0,
            why: "No per-clip plan was generated.",
            opening_strategy: "Clean open — no added effects",
            confidence: 0,
          },
        })}
        taskId="task-1"
        busy={false}
        best={false}
        editable={false}
        onOpen={() => undefined}
        onDownload={() => undefined}
      />
    );
    expect(screen.queryByText(/Hook:/)).not.toBeInTheDocument();

    rerender(
      <ClipTile
        clip={wallClip({ hook_report: null })}
        taskId="task-1"
        busy={false}
        best={false}
        editable={false}
        onOpen={() => undefined}
        onDownload={() => undefined}
      />
    );
    expect(screen.queryByText(/Hook:/)).not.toBeInTheDocument();
  });
});

describe("download recovery progress", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a casual delay notice without an ETA, extra details, or increased progress", () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { rerender } = render(<ProcessingPanel status="processing" progress={65}
      message="Using a backup video download option..." elapsedSeconds={90} />);

    expect(screen.getByText("Hang tight")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(messages.recovery.messages.snags);
    expect(screen.getByRole("status")).not.toHaveTextContent(/download|backup|connection|recovery/i);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "65");
    expect(screen.queryByText(/min left|minute left/)).not.toBeInTheDocument();

    rerender(<ProcessingPanel status="processing" progress={70}
      message="Creating clip 1/4..." elapsedSeconds={90} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("Hang tight")).not.toBeInTheDocument();
    expect(screen.getByText("Less than a minute left")).toBeVisible();
  });

  it.each([
    "Retrying video download with another connection...",
    "Recovering video audio...",
    "Recovering the full video download...",
    "Trying another video download recovery option...",
  ])("shows a generic delay notice for persisted recovery state: %s", (message) => {
    render(<ProcessingPanel status="processing" progress={10} message={message} elapsedSeconds={30} />);
    expect(Object.values(messages.recovery.messages)).toContain(screen.getByRole("status").textContent);
  });

  it("chooses a random sentence once and keeps it through progress and recovery changes", () => {
    const random = vi.spyOn(Math, "random").mockReturnValue(0.4);
    const { rerender } = render(<ProcessingPanel status="processing" progress={10}
      message="Using a backup video download option..." elapsedSeconds={30} />);
    expect(screen.getByRole("status")).toHaveTextContent(messages.recovery.messages.scenicRoute);

    random.mockReturnValue(0.9);
    rerender(<ProcessingPanel status="processing" progress={65}
      message="Trying another video download recovery option..." elapsedSeconds={90} />);
    expect(screen.getByRole("status")).toHaveTextContent(messages.recovery.messages.scenicRoute);
  });

  it("does not expose unknown provider errors or stale recovery while queued", () => {
    const { rerender } = render(<ProcessingPanel status="processing" progress={10}
      message="private provider error" elapsedSeconds={30} />);
    expect(screen.queryByText("private provider error")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    rerender(<ProcessingPanel status="queued" progress={0}
      message="Using a backup video download option..." elapsedSeconds={30} />);
    expect(screen.getByText("Waiting for a worker")).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("clip tile brief badge", () => {
  const tileProps = {
    taskId: "task-1",
    busy: false,
    best: false,
    editable: false,
    onOpen: () => undefined,
    onDownload: () => undefined,
  };

  it("shows the brief badge from the compliance summary", () => {
    render(
      <ClipTile
        clip={wallClip({
          compliance_status: "compliant",
          compliance_summary: {
            status: "compliant",
            mandatory_failed: [],
            mandatory_uncertain: [],
          },
        })}
        {...tileProps}
      />
    );
    expect(screen.getByText("Brief: compliant")).toBeVisible();
  });

  it("shows non-compliant and uncertain badges", () => {
    const { rerender } = render(
      <ClipTile clip={wallClip({ compliance_status: "non_compliant" })} {...tileProps} />
    );
    expect(screen.getByText("Brief: non-compliant")).toBeVisible();
    rerender(<ClipTile clip={wallClip({ compliance_status: "uncertain" })} {...tileProps} />);
    expect(screen.getByText("Brief: uncertain — review")).toBeVisible();
  });

  it("shows no badge for a brief-less clip", () => {
    render(<ClipTile clip={wallClip()} {...tileProps} />);
    expect(screen.queryByText(/Brief:/)).not.toBeInTheDocument();
  });
});
