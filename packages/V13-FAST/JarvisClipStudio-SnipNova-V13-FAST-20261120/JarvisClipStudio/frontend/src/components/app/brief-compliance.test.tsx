import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  BriefBadge,
  BriefDropsBanner,
  BriefNoMatchPanel,
  ClipComplianceSection,
} from "./brief-compliance";
import type { ComplianceReport } from "@/lib/content-brief";

describe("BriefBadge", () => {
  it("renders the three statuses with their honest labels", () => {
    const { rerender } = render(<BriefBadge status="compliant" />);
    expect(screen.getByText("Brief: compliant")).toBeInTheDocument();
    rerender(<BriefBadge status="non_compliant" />);
    expect(screen.getByText("Brief: non-compliant")).toBeInTheDocument();
    rerender(<BriefBadge status="uncertain" />);
    expect(screen.getByText("Brief: uncertain — review")).toBeInTheDocument();
  });

  it("renders nothing without a brief status", () => {
    const { container } = render(<BriefBadge status={null} />);
    expect(container).toBeEmptyDOMElement();
    const { container: container2 } = render(<BriefBadge status={undefined} />);
    expect(container2).toBeEmptyDOMElement();
  });
});

const report: ComplianceReport = {
  version: 1,
  checked_at: "2026-11-09T00:00:00Z",
  status: "non_compliant",
  ranges: {
    allowed: [[10589, null]],
    actual: [[10610, 10645]],
    violations: [],
  },
  source_timestamps: { start: "2:56:50", end: "2:57:25" },
  requirements: [
    {
      id: "r1",
      type: "topic_focus",
      text: "Expedia must be a major focus of the content",
      kind: "mandatory",
      status: "satisfied",
      focus: "major",
      requires_major: true,
      evidence: [
        {
          start_seconds: 10620,
          end_seconds: 10631,
          excerpt: "we booked the whole trip on Expedia",
          match: "exact",
        },
      ],
    },
    {
      id: "r2",
      type: "must_mention",
      text: "The video must mention the code CREATOR",
      kind: "mandatory",
      status: "failed",
      phrase: "CREATOR",
      sources: { transcript: "searched_no_hit", captions: "searched_no_hit", title_copy: "absent" },
      hit_sources: [],
    },
    {
      id: "r3",
      type: "structure_cta",
      text: "Call to action near the end",
      kind: "optional",
      status: "satisfied",
      form: "spoken",
      window_seconds: 10,
      phrases: ["use code CREATOR"],
    },
    {
      id: "r4",
      type: "visual_presence",
      text: "Show the game",
      kind: "mandatory",
      status: "uncertain",
      subject: "the game being played",
      judgement_source: "model_judgement",
      detail: "no visual-presence judgement was available",
    },
  ],
};

describe("ClipComplianceSection", () => {
  it("renders every requirement with status, evidence and range line", () => {
    render(<ClipComplianceSection report={report} />);
    expect(screen.getByText("Campaign brief")).toBeInTheDocument();
    expect(screen.getByText("Brief: non-compliant")).toBeInTheDocument();
    expect(
      screen.getByText(/inside allowed range 2:56:29–end/)
    ).toBeInTheDocument();
    // Focus verdict + verbatim evidence with source timestamp.
    expect(screen.getByText(/Focus: major focus/)).toBeInTheDocument();
    expect(
      screen.getByText(/we booked the whole trip on Expedia/)
    ).toBeInTheDocument();
    expect(screen.getByText(/2:57:00/)).toBeInTheDocument();
    // Failed must-mention explains where it was searched.
    expect(
      screen.getByText(/Not found in the transcript, captions or title copy/)
    ).toBeInTheDocument();
    // CTA form is reported.
    expect(screen.getByText(/Call to action found as: spoken/)).toBeInTheDocument();
    // Uncertain visual presence stays honest.
    expect(
      screen.getByText(/no visual-presence judgement was available/)
    ).toBeInTheDocument();
    expect(screen.getAllByText("Mandatory").length).toBeGreaterThan(0);
  });
});

describe("BriefNoMatchPanel", () => {
  it("shows what was searched and the candidate counts", () => {
    render(
      <BriefNoMatchPanel
        brief={{
          version: 2,
          requirements: [
            {
              id: "r1",
              type: "topic_focus",
              text: "ZQX Travel Club must be a major focus",
              kind: "mandatory",
            },
          ],
          allowed_ranges: [
            {
              start_seconds: 120,
              end_seconds: 480,
              start_label: "2:00",
              end_label: "8:00",
            },
          ],
        }}
        report={{
          searched_ranges: [
            {
              start_seconds: 120,
              end_seconds: 480,
              start_label: "2:00",
              end_label: "8:00",
            },
          ],
          requirements: [{ id: "r1", terms: ["ZQX Travel Club"], term_source: "field" }],
          candidates_total: 12,
          candidates_in_range: 5,
          candidates_dropped_range: 7,
          candidates_dropped_mandatory: 5,
          outcome: "no_compliant_moments",
        }}
      />
    );
    expect(
      screen.getByText("No moment in the video satisfied the campaign brief")
    ).toBeInTheDocument();
    expect(screen.getByText(/Searched: 2:00–8:00/)).toBeInTheDocument();
    expect(
      screen.getByText(/ZQX Travel Club must be a major focus — searched for: ZQX Travel Club/)
    ).toBeInTheDocument();
    expect(screen.getByText(/12 candidate moments examined/)).toBeInTheDocument();
    expect(screen.getByText(/Widen the allowed range/)).toBeInTheDocument();
  });
});

describe("BriefDropsBanner", () => {
  it("appears only when candidates were dropped", () => {
    const { container } = render(
      <BriefDropsBanner report={{ candidates_dropped_range: 0, candidates_dropped_mandatory: 0 }} />
    );
    expect(container).toBeEmptyDOMElement();
    render(
      <BriefDropsBanner report={{ candidates_dropped_range: 3, candidates_dropped_mandatory: 2 }} />
    );
    expect(screen.getByText(/5 candidate moments were dropped/)).toBeInTheDocument();
  });
});
