import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ContentBriefPanel } from "./content-brief-panel";
import type { ContentBriefPayload } from "@/lib/content-brief";

function renderPanel() {
  const onChange = vi.fn<(payload: ContentBriefPayload | null) => void>();
  render(<ContentBriefPanel onChange={onChange} />);
  return onChange;
}

function expand() {
  fireEvent.click(screen.getByRole("button", { name: /Add a campaign brief/ }));
}

function lastPayload(
  onChange: ReturnType<typeof renderPanel>
): ContentBriefPayload | null {
  const calls = onChange.mock.calls;
  if (!calls.length) return null;
  return calls[calls.length - 1][0];
}

it("starts collapsed and reports no brief", () => {
  const onChange = renderPanel();
  expect(screen.getByText("Campaign brief")).toBeInTheDocument();
  expect(screen.queryByText("Requirements")).not.toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalledWith(
    expect.objectContaining({ version: 2 })
  );
});

it("builds a mandatory topic-focus requirement with a major-focus bar", () => {
  const onChange = renderPanel();
  expand();
  fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
  fireEvent.change(screen.getByLabelText("Requirement"), {
    target: { value: "Expedia must be a major focus of the content" },
  });
  const payload = lastPayload(onChange);
  expect(payload).toEqual({
    version: 2,
    requirements: [
      {
        type: "topic_focus",
        text: "Expedia must be a major focus of the content",
        kind: "mandatory",
      },
    ],
    allowed_ranges: [],
  });
});

it("toggles a requirement to optional and respects the type dropdown", () => {
  const onChange = renderPanel();
  expand();
  fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
  fireEvent.change(screen.getByLabelText("Requirement"), {
    target: { value: "The video must mention the code CREATOR" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Optional" }));
  fireEvent.change(screen.getByLabelText("Type"), {
    target: { value: "must_mention" },
  });
  fireEvent.change(screen.getByLabelText(/Exact phrase/), {
    target: { value: "CREATOR" },
  });
  const payload = lastPayload(onChange);
  expect(payload?.requirements).toEqual([
    {
      type: "must_mention",
      text: "The video must mention the code CREATOR",
      kind: "optional",
      phrase: "CREATOR",
    },
  ]);
});

it("info-only requirements carry no kind", () => {
  const onChange = renderPanel();
  expand();
  fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
  fireEvent.change(screen.getByLabelText("Type"), {
    target: { value: "info_only" },
  });
  fireEvent.change(screen.getByLabelText("Requirement"), {
    target: { value: "Payouts start at 10k views" },
  });
  const payload = lastPayload(onChange);
  expect(payload?.requirements).toEqual([
    { type: "info_only", text: "Payouts start at 10k views" },
  ]);
  expect(payload?.requirements[0]).not.toHaveProperty("kind");
});

it("adds an allowed range with an open end and validates timecodes", () => {
  const onChange = renderPanel();
  expand();
  // The first "Add range" / range inputs belong to the allowed section;
  // the never-use section below reuses the same row controls.
  fireEvent.click(screen.getAllByRole("button", { name: "Add range" })[0]);
  fireEvent.change(screen.getAllByLabelText("Range start")[0], {
    target: { value: "2:56:29" },
  });
  expect(lastPayload(onChange)?.allowed_ranges).toEqual([
    { start: "2:56:29", end: null },
  ]);

  fireEvent.change(screen.getAllByLabelText("Range end")[0], {
    target: { value: "not-a-time" },
  });
  expect(screen.getByRole("alert")).toHaveTextContent(/end time/);

  fireEvent.change(screen.getAllByLabelText("Range end")[0], {
    target: { value: "3:10:00" },
  });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(lastPayload(onChange)?.allowed_ranges).toEqual([
    { start: "2:56:29", end: "3:10:00" },
  ]);
});

it("removing the only requirement returns the panel to no brief", () => {
  const onChange = renderPanel();
  expand();
  fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
  fireEvent.change(screen.getByLabelText("Requirement"), {
    target: { value: "Must mention Expedia" },
  });
  expect(lastPayload(onChange)).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Remove requirement" }));
  expect(lastPayload(onChange)).toBeNull();
});

const extractedDraft = {
  content_brief: {
    version: 2,
    requirements: [
      {
        id: "r1",
        type: "topic_focus",
        text: "Expedia must be a major focus of the content",
        kind: "mandatory",
        topics: ["Expedia"],
        requires_major: true,
      },
    ],
    allowed_ranges: [
      { start_seconds: 10589, end_seconds: null, start_label: "2:56:29", end_label: null },
    ],
  },
  extraction: { source: "llm", model: "some-model", warnings: [] },
};

it("extracts a draft but applies nothing until the user confirms", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/briefs/extract")) {
        return Response.json(extractedDraft);
      }
      return Response.json({});
    })
  );
  const onChange = renderPanel();
  expand();
  fireEvent.change(screen.getByLabelText("Paste a campaign brief"), {
    target: { value: "Only use footage after 2:56:29. Expedia must be a major focus." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Extract requirements" }));

  expect(
    await screen.findByText("Extracted by model — review before creating")
  ).toBeInTheDocument();
  // The extract response alone must not change what will be submitted,
  // and no requirement row may appear before the user confirms.
  expect(lastPayload(onChange)).toBeNull();
  expect(screen.queryByLabelText("Requirement")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Use this draft" }));
  const payload = lastPayload(onChange);
  expect(payload?.requirements).toEqual([
    {
      type: "topic_focus",
      text: "Expedia must be a major focus of the content",
      kind: "mandatory",
      topics: ["Expedia"],
      requires_major: true,
    },
  ]);
  expect(payload?.allowed_ranges).toEqual([{ start: "2:56:29", end: null }]);

  const extractCall = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => String(url).includes("/api/briefs/extract"));
  expect(JSON.parse(String(extractCall?.[1]?.body))).toEqual({
    brief_text: "Only use footage after 2:56:29. Expedia must be a major focus.",
  });
});

it("labels a deterministic extraction honestly and can discard it", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...extractedDraft,
        extraction: {
          source: "deterministic",
          model: null,
          warnings: ["No permitted model is configured; basic extraction was used"],
        },
      })
    )
  );
  const onChange = renderPanel();
  expand();
  fireEvent.change(screen.getByLabelText("Paste a campaign brief"), {
    target: { value: "Expedia rules" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Extract requirements" }));
  expect(
    await screen.findByText("Basic extraction — review before creating")
  ).toBeInTheDocument();
  expect(
    screen.getByText(/No permitted model is configured/)
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Discard" }));
  await waitFor(() =>
    expect(
      screen.queryByText("Basic extraction — review before creating")
    ).not.toBeInTheDocument()
  );
  expect(lastPayload(onChange)).toBeNull();
});

it("shows a pre-generation summary that mirrors the payload exactly", () => {
  const onChange = vi.fn();
  render(
    <ContentBriefPanel
      onChange={onChange}
      summaryContext={{
        sourceLabel: "https://www.youtube.com/watch?v=stream",
        targetClipCount: 10,
        clipLength: "short",
        campaignName: "Expedia x Speed",
        referenceFromCampaign: true,
      }}
    />
  );
  expand();
  fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
  fireEvent.change(screen.getByLabelText("Requirement"), {
    target: { value: "Expedia must be a major focus of the content" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
  const textInputs = screen.getAllByLabelText("Requirement");
  fireEvent.change(textInputs[1], {
    target: { value: "Payouts start at 10k views" },
  });
  fireEvent.change(screen.getAllByLabelText("Type")[1], {
    target: { value: "info_only" },
  });
  fireEvent.click(screen.getAllByRole("button", { name: "Add range" })[0]);
  fireEvent.change(screen.getAllByLabelText("Range start")[0], {
    target: { value: "10589" },
  });

  // Source, campaign, reference-style source, counts and durations.
  expect(screen.getByText("What will be enforced")).toBeInTheDocument();
  expect(screen.getByText("https://www.youtube.com/watch?v=stream")).toBeInTheDocument();
  expect(screen.getByText("Campaign: Expedia x Speed")).toBeInTheDocument();
  expect(
    screen.getByText("Reference style: from the campaign example link")
  ).toBeInTheDocument();
  expect(screen.getByText("Clips: 10")).toBeInTheDocument();
  expect(screen.getByText("Length: Short (15–30s)")).toBeInTheDocument();
  // Range formatted from raw seconds, open end labelled.
  expect(screen.getByText("2:56:29 – to end")).toBeInTheDocument();
  // Mandatory bar in plain words; info-only marked not checked.
  expect(screen.getByText("MANDATORY")).toBeInTheDocument();
  expect(screen.getByText("— must be a major focus")).toBeInTheDocument();
  expect(screen.getByText("NOT CHECKED")).toBeInTheDocument();
  expect(screen.getByText("— not checked — for reference only")).toBeInTheDocument();
  // The hard-enforcement sentence.
  expect(
    screen.getByText(/No clip will contain footage outside these ranges/)
  ).toBeInTheDocument();
});

it("summary shows Full video and the shorter enforcement line without ranges", () => {
  render(<ContentBriefPanel onChange={vi.fn()} />);
  expand();
  fireEvent.click(screen.getByRole("button", { name: "Add requirement" }));
  fireEvent.change(screen.getByLabelText("Requirement"), {
    target: { value: "Talk about hotels" },
  });
  expect(screen.getByText("Full video")).toBeInTheDocument();
  expect(screen.getByText("— must be mentioned")).toBeInTheDocument();
  expect(
    screen.getByText(
      "If no moment satisfies every mandatory requirement, no clips will be made."
    )
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/No clip will contain footage/)
  ).not.toBeInTheDocument();
});

const imageDraft = {
  content_brief: {
    version: 2,
    requirements: [
      {
        id: "r1",
        type: "topic_focus",
        text: "Every clip should connect back to natural diamonds.",
        kind: "mandatory",
        topics: ["natural diamonds"],
        requires_major: true,
      },
    ],
    allowed_ranges: [],
    excluded_ranges: [
      { start_seconds: 47, end_seconds: 53, start_label: "0:47", end_label: "0:53" },
    ],
    flag_points: [
      { at_seconds: 193, at_label: "3:13", category: "licence_plate", note: "blur/crop" },
    ],
    key_timestamps: [
      { at_seconds: 90, at_label: "1:30", description: "How diamonds helped" },
    ],
  },
  extraction: { source: "deterministic", model: null, warnings: [] },
  brief_text: "Key timestamps:\n01:30 — How diamonds helped transform Botswana",
  ocr: { characters: 1234 },
};

it("reads a brief screenshot into a reviewable draft, applying nothing early", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/briefs/extract-image")) {
        return Response.json(imageDraft);
      }
      return Response.json({});
    })
  );
  const onChange = renderPanel();
  expand();
  const file = new File(["png-bytes"], "brief.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Brief screenshot"), {
    target: { files: [file] },
  });

  // The OCR text lands in the paste box for review; the draft waits.
  expect(
    await screen.findByText(/Read 1234 characters from the screenshot/)
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Paste a campaign brief")).toHaveValue(
    imageDraft.brief_text
  );
  expect(screen.getByText(/Also drafted:/)).toBeInTheDocument();
  expect(lastPayload(onChange)).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Use this draft" }));
  const payload = lastPayload(onChange);
  expect(payload?.excluded_ranges).toEqual([{ start: "0:47", end: "0:53" }]);
  expect(payload?.flag_points).toEqual([
    { at: "3:13", category: "licence_plate", note: "blur/crop" },
  ]);
  expect(payload?.key_timestamps).toEqual([
    { at: "1:30", description: "How diamonds helped" },
  ]);

  // The upload went out as multipart form data, not JSON.
  const imageCall = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => String(url).includes("extract-image"));
  expect(imageCall?.[1]?.body).toBeInstanceOf(FormData);
});

it("shows the server error when a screenshot cannot be read", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ detail: "No readable text was found in that image" }),
          { status: 422 }
        )
    )
  );
  const onChange = renderPanel();
  expand();
  fireEvent.change(screen.getByLabelText("Brief screenshot"), {
    target: { files: [new File(["x"], "brief.png", { type: "image/png" })] },
  });
  expect(
    await screen.findByText("No readable text was found in that image")
  ).toBeInTheDocument();
  expect(lastPayload(onChange)).toBeNull();
});

it("adds a never-use range, a flag point and a key timestamp by hand", () => {
  const onChange = renderPanel();
  expand();

  const addRangeButtons = screen.getAllByRole("button", { name: "Add range" });
  fireEvent.click(addRangeButtons[1]); // the never-use section's button
  const starts = screen.getAllByLabelText("Range start");
  const ends = screen.getAllByLabelText("Range end");
  fireEvent.change(starts[starts.length - 1], { target: { value: "0:47" } });
  fireEvent.change(ends[ends.length - 1], { target: { value: "0:53" } });
  expect(lastPayload(onChange)?.excluded_ranges).toEqual([
    { start: "0:47", end: "0:53" },
  ]);

  fireEvent.click(screen.getByRole("button", { name: "Add flag point" }));
  fireEvent.change(screen.getByLabelText("Flag time"), {
    target: { value: "3:13" },
  });
  fireEvent.change(screen.getByLabelText("Flag type"), {
    target: { value: "licence_plate" },
  });
  fireEvent.change(screen.getByLabelText("Note"), {
    target: { value: "blur/crop" },
  });
  expect(lastPayload(onChange)?.flag_points).toEqual([
    { at: "3:13", category: "licence_plate", note: "blur/crop" },
  ]);

  fireEvent.click(screen.getByRole("button", { name: "Add key timestamp" }));
  fireEvent.change(screen.getByLabelText("Timestamp"), {
    target: { value: "1:30" },
  });
  fireEvent.change(screen.getByLabelText("What happens there"), {
    target: { value: "How diamonds helped" },
  });
  expect(lastPayload(onChange)?.key_timestamps).toEqual([
    { at: "1:30", description: "How diamonds helped" },
  ]);

  // The summary mirrors all three families before anything is created.
  expect(screen.getByText("Never used:")).toBeInTheDocument();
  expect(screen.getByText("00:47 – 00:53")).toBeInTheDocument();
  expect(screen.getByText("Flagged for review:")).toBeInTheDocument();
  expect(screen.getByText(/hard-blocked from every clip/)).toBeInTheDocument();
});

it("caps requirements at 10 and ranges at 8", () => {
  renderPanel();
  expand();
  const addRequirement = screen.getByRole("button", { name: "Add requirement" });
  for (let i = 0; i < 10; i += 1) fireEvent.click(addRequirement);
  expect(addRequirement).toBeDisabled();
  expect(screen.getByText(/At most 10 requirements/)).toBeInTheDocument();

  const addRange = screen.getAllByRole("button", { name: "Add range" })[0];
  for (let i = 0; i < 8; i += 1) fireEvent.click(addRange);
  expect(addRange).toBeDisabled();
  expect(screen.getByText(/At most 8 ranges/)).toBeInTheDocument();
});
