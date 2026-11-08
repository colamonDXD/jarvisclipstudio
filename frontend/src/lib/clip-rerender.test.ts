import { clipExportPollDelayMs } from "./clip-export";
import { pollClipRerender } from "./clip-rerender";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("clip rerender polling", () => {
  it("backs off and returns the refreshed clip once the job completes", async () => {
    const sleeps: number[] = [];
    const calls: string[] = [];
    const outcome = await pollClipRerender({
      start: async () => {
        calls.push("start");
        return jsonResponse({ job_id: "clip-rerender:clip-1:aa", clip_id: "clip-1", status: "queued", niche_override: "gaming" });
      },
      status: async (jobId) => {
        calls.push(jobId);
        if (calls.length === 2) {
          return jsonResponse({ job_id: jobId, clip_id: "clip-1", status: "processing" });
        }
        return jsonResponse({
          job_id: jobId,
          clip_id: "clip-1",
          status: "completed",
          clip: { id: "clip-1", filename: "clip_rerendered.mp4" },
        });
      },
      readError: async () => "failed",
      fallbackMessage: "Could not re-render",
      timeoutMessage: "Timed out",
      sleep: async (ms) => {
        sleeps.push(ms);
      },
      now: () => 0,
    });

    expect(outcome.status).toBe("completed");
    expect(outcome.clip).toEqual({ id: "clip-1", filename: "clip_rerendered.mp4" });
    expect(calls).toEqual(["start", "clip-rerender:clip-1:aa", "clip-rerender:clip-1:aa"]);
    expect(sleeps).toEqual([clipExportPollDelayMs(0), clipExportPollDelayMs(1)]);
  });

  it("stops when the job fails and when polling runs too long", async () => {
    await expect(pollClipRerender({
      start: async () => jsonResponse({ job_id: "job-2", clip_id: "clip-1", status: "failed", error: "Render failed. Try again." }),
      status: async () => jsonResponse({}),
      readError: async (response) => `status ${response.status}`,
      fallbackMessage: "Could not re-render",
      timeoutMessage: "Timed out",
      sleep: async () => undefined,
    })).rejects.toThrow("Render failed. Try again.");

    let clock = 0;
    await expect(pollClipRerender({
      start: async () => jsonResponse({ job_id: "job-3", clip_id: "clip-1", status: "processing" }),
      status: async () => jsonResponse({ job_id: "job-3", clip_id: "clip-1", status: "processing" }),
      readError: async () => "failed",
      fallbackMessage: "Could not re-render",
      timeoutMessage: "Timed out",
      sleep: async () => {
        clock += 60_000;
      },
      now: () => clock,
      maxWaitMs: 1_000,
    })).rejects.toThrow("Timed out");
  });

  it("surfaces a start error", async () => {
    await expect(pollClipRerender({
      start: async () => jsonResponse({ detail: "invalid niche_override" }, 422),
      status: async () => jsonResponse({}),
      readError: async (response) => `status ${response.status}`,
      fallbackMessage: "Could not re-render",
      timeoutMessage: "Timed out",
    })).rejects.toThrow("status 422");
  });
});
