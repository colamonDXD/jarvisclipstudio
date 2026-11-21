import { describe, expect, it } from "vitest";

import { taskFailureDetail } from "./task-failure";

describe("taskFailureDetail", () => {
  it("returns the backend's recorded message for a failed task", () => {
    expect(
      taskFailureDetail({
        status: "error",
        progress_message: "We couldn't download that video. Check the link and try again.",
      }),
    ).toBe("We couldn't download that video. Check the link and try again.");
  });

  it("trims surrounding whitespace", () => {
    expect(
      taskFailureDetail({ status: "error", progress_message: "  Analysis failed.  " }),
    ).toBe("Analysis failed.");
  });

  it("returns null for a failed task with no recorded message", () => {
    expect(taskFailureDetail({ status: "error", progress_message: null })).toBeNull();
    expect(taskFailureDetail({ status: "error", progress_message: "   " })).toBeNull();
    expect(taskFailureDetail({ status: "error" })).toBeNull();
  });

  it("returns null for non-error statuses, even with a progress message", () => {
    expect(
      taskFailureDetail({ status: "processing", progress_message: "Rendering clip 2 of 5" }),
    ).toBeNull();
    expect(
      taskFailureDetail({ status: "completed", progress_message: "Done" }),
    ).toBeNull();
  });

  it("returns null for a missing task", () => {
    expect(taskFailureDetail(null)).toBeNull();
    expect(taskFailureDetail(undefined)).toBeNull();
  });
});
