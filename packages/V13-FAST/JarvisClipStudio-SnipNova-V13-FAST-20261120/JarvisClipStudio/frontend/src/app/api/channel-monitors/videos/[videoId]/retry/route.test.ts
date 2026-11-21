import { POST } from "./route";
import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/backend-api", () => ({
  fetchBackend: vi.fn(),
  createTextProxyResponse: vi.fn(async (upstream: Response) => upstream),
}));

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("/api/channel-monitors/videos/[videoId]/retry", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(createTextProxyResponse).mockImplementation(
      async (upstream: Response) => upstream,
    );
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(jsonResponse({}));
  });

  it("returns 401 when no session exists", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);

    const response = await POST(new Request("http://localhost"), {
      params: Promise.resolve({ videoId: "vid-1" }),
    });

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: "Unauthorized" });
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("proxies the retry to the backend for an authenticated user", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      jsonResponse({ outcome: "queued", message: "Task created" }),
    );

    const response = await POST(new Request("http://localhost"), {
      params: Promise.resolve({ videoId: "vid-1" }),
    });

    expect(response.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledWith(
      "/channel-monitors/videos/vid-1/retry",
      {
        method: "POST",
        userId: "user-1",
        cache: "no-store",
      },
    );
  });

  it("url-encodes the video id in the backend path", async () => {
    await POST(new Request("http://localhost"), {
      params: Promise.resolve({ videoId: "vid 1/x" }),
    });

    expect(fetchBackend).toHaveBeenCalledWith(
      "/channel-monitors/videos/vid%201%2Fx/retry",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
