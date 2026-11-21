import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MomentPicker } from "./moment-picker";

const candidates = [
  {
    start_time: "00:10",
    end_time: "00:40",
    text: "First moment",
    reasoning: "Strong hook",
    virality_score: 90,
  },
  {
    start_time: "01:10",
    end_time: "01:40",
    text: "Second moment",
    reasoning: "Good payoff",
    virality_score: 80,
  },
];

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/candidates")) {
      return { ok: true, json: async () => ({ candidates }) };
    }
    if (url.endsWith("/select-moments")) {
      return { ok: true, json: async () => ({ message: "queued" }) };
    }
    throw new Error(`Unexpected fetch ${url} ${init?.method ?? ""}`);
  });
  vi.stubGlobal("fetch", fetchMock);
});

describe("MomentPicker", () => {
  it("lists the parked candidates with their times and scores", async () => {
    render(<MomentPicker taskId="task-1" />);

    expect(await screen.findByText("00:10 – 00:40")).toBeTruthy();
    expect(screen.getByText("First moment")).toBeTruthy();
    expect(screen.getByText("90")).toBeTruthy();
    expect(screen.getByText("0 of 2 picked")).toBeTruthy();
  });

  it("submits only the ticked indexes, sorted", async () => {
    const onSubmitted = vi.fn();
    render(<MomentPicker taskId="task-1" onSubmitted={onSubmitted} />);

    const second = await screen.findByLabelText("Pick moment 2");
    fireEvent.click(second);
    const first = screen.getByLabelText("Pick moment 1");
    fireEvent.click(first);

    const submit = screen.getByRole("button", { name: /Render 2 selected/ });
    fireEvent.click(submit);

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledOnce());
    const post = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/select-moments"),
    );
    expect(post).toBeTruthy();
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      segment_indexes: [0, 1],
    });
    expect(await screen.findByText("Your picks are queued")).toBeTruthy();
  });

  it("keeps submit disabled until something is picked", async () => {
    render(<MomentPicker taskId="task-1" />);

    await screen.findByText("00:10 – 00:40");
    const submit = screen.getByRole("button", { name: /Render selected/ });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows when the wait ends and the task auto-continues", async () => {
    const deadline = "2026-11-10T10:15:00.000Z";
    fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        candidates,
        selection_parked_at: "2026-11-10T10:00:00.000Z",
        auto_pick_minutes: 15,
        auto_pick_deadline: deadline,
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<MomentPicker taskId="task-1" />);

    const expectedTime = new Intl.DateTimeFormat(undefined, {
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(deadline));
    expect(
      await screen.findByText(
        new RegExp(
          `Waiting for your pick — if you don't pick within 15 minutes \\(by ${expectedTime.replace(
            /[.*+?^${}()|[\]\\]/g,
            "\\$&",
          )}\\), this continues with the top picks automatically\\.`,
        ),
      ),
    ).toBeTruthy();
  });

  it("omits the auto-continue note when the API reports no deadline", async () => {
    render(<MomentPicker taskId="task-1" />);

    await screen.findByText("00:10 – 00:40");
    expect(screen.queryByText(/top picks automatically/)).toBeNull();
  });

  it("shows the hook title the API returns for a moment, and none when it is null", async () => {
    fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/candidates")) {
        return {
          ok: true,
          json: async () => ({
            candidates: [
              {
                start_time: "02:00",
                end_time: "02:30",
                text: "Streamer sees the reveal",
                reasoning: "Genuine reaction",
                virality_score: 95,
                hook_title: "He was NOT ready for that",
              },
              {
                start_time: "03:00",
                end_time: "03:30",
                text: "Visual burst",
                reasoning: "Visual selection",
                virality_score: 70,
                hook_title: null,
              },
            ],
          }),
        };
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MomentPicker taskId="task-1" />);

    expect(await screen.findByText("He was NOT ready for that")).toBeTruthy();
    expect(screen.getByText("Visual burst")).toBeTruthy();
    // Exactly one hook title rendered: the null one invents nothing.
    expect(screen.getAllByText(/NOT ready/)).toHaveLength(1);
  });

  it("says so honestly when the slate is gone", async () => {
    fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ candidates: [] }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<MomentPicker taskId="task-1" />);

    expect(
      await screen.findByText(/No candidate moments are stored/),
    ).toBeTruthy();
  });
});
