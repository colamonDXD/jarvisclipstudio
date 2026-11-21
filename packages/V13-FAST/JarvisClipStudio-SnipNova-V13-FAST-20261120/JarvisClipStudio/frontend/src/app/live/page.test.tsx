// frontend/src/app/live/page.test.tsx — NEW: tests for the Live clipping page.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import LivePage from "./page";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/live",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

function makeFetch(statusEnabled: boolean, extraHandlers?: (url: string) => Response | null) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (extraHandlers) {
      const result = extraHandlers(url);
      if (result) return result;
    }
    if (url.includes("/api/live/status")) {
      return Response.json({ enabled: statusEnabled });
    }
    if (url.includes("/api/live/sessions") && !url.includes("/stop") && !url.includes("/sessions/")) {
      return Response.json({ sessions: [] });
    }
    return Response.json({});
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});

// --- Test 1: status disabled shows "not switched on" ---
it("when /api/live/status returns disabled, shows 'not switched on'", async () => {
  vi.stubGlobal("fetch", makeFetch(false));
  render(<LivePage />);

  await screen.findByText(/not switched on/i);
  expect(screen.getByText(/not switched on/i)).toBeInTheDocument();
});

// --- Test 2: 422 with detail.message shows that message in a toast ---
it("a 422 with detail.message shows that message in a toast error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/live/status")) return Response.json({ enabled: true });
      if (url.includes("/api/live/sessions") && init?.method === "POST") {
        return new Response(
          JSON.stringify({ detail: { message: "Stream is offline right now" } }),
          { status: 422, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url.includes("/api/live/sessions")) return Response.json({ sessions: [] });
      return Response.json({});
    })
  );

  render(<LivePage />);
  // Wait for the page to load (enabled state resolved).
  await screen.findByRole("button", { name: /start clipping/i });

  // Fill in the URL and submit.
  fireEvent.change(screen.getByLabelText(/stream link/i), {
    target: { value: "https://www.twitch.tv/teststreamer" },
  });
  fireEvent.click(screen.getByRole("button", { name: /start clipping/i }));

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Stream is offline right now")
  );
});

// --- Test 3: a session in mode "vod" shows its progress_message ---
it("a session in mode 'vod' and status 'recording' shows the progress_message", async () => {
  const vodSession = {
    id: "sess-1",
    url: "https://www.youtube.com/watch?v=abc123",
    status: "recording",
    settings: {},
    recording: true,
    buffered_seconds: 300,
    restarts: 0,
    last_error: null,
    progress_message: "Analysing audio — 42% done",
    mode: "vod",
    started_at: null,
    created_at: new Date().toISOString(),
  };

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/live/status")) return Response.json({ enabled: true });
      if (url.match(/\/api\/live\/sessions\/sess-1$/)) {
        return Response.json({ session: vodSession, clips: [] });
      }
      if (url.includes("/api/live/sessions")) {
        return Response.json({ sessions: [vodSession] });
      }
      return Response.json({});
    })
  );

  render(<LivePage />);

  await screen.findByText("Analysing audio — 42% done");
  expect(screen.getByText("Analysing audio — 42% done")).toBeInTheDocument();
});

// --- Test 4: "Skipped moments (n)" lists reasons ---
it("'Skipped moments (n)' lists the reason for each rejected clip", async () => {
  const session = {
    id: "sess-2",
    url: "https://www.twitch.tv/videos/9999",
    status: "ended",
    settings: {},
    recording: false,
    buffered_seconds: 1200,
    restarts: 0,
    last_error: null,
    progress_message: null,
    mode: "live",
    started_at: null,
    created_at: new Date().toISOString(),
  };

  const clips = [
    {
      id: "clip-1",
      n: 1,
      status: "ready",
      reason: null,
      onset_at: null,
      signals: ["laugh"],
      strength: 0.9,
      talk_share: 0.8,
      has_preview: true,
      has_clip: true,
      duration: 30,
      created_at: new Date().toISOString(),
    },
    {
      id: "clip-2",
      n: null,
      status: "rejected",
      reason: "strength below threshold",
      onset_at: null,
      signals: [],
      strength: 0.1,
      talk_share: 0.2,
      has_preview: false,
      has_clip: false,
      duration: null,
      created_at: new Date().toISOString(),
    },
    {
      id: "clip-3",
      n: null,
      status: "rejected",
      reason: "too close to previous clip",
      onset_at: null,
      signals: [],
      strength: 0.2,
      talk_share: 0.5,
      has_preview: false,
      has_clip: false,
      duration: null,
      created_at: new Date().toISOString(),
    },
  ];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/live/status")) return Response.json({ enabled: true });
      if (url.match(/\/api\/live\/sessions\/sess-2$/)) {
        return Response.json({ session, clips });
      }
      if (url.includes("/api/live/sessions")) {
        return Response.json({ sessions: [session] });
      }
      return Response.json({});
    })
  );

  render(<LivePage />);

  // The <details> summary shows the count.
  const summary = await screen.findByText(/skipped moments \(2\)/i);
  expect(summary).toBeInTheDocument();

  // Open the details element so the content is visible.
  fireEvent.click(summary);

  expect(await screen.findByText(/strength below threshold/i)).toBeInTheDocument();
  expect(screen.getByText(/too close to previous clip/i)).toBeInTheDocument();
});
