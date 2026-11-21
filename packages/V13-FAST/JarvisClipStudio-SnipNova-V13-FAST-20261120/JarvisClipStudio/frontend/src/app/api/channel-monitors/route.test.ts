import { GET, POST } from "./route";
import { PUT as UPDATE_MONITOR, DELETE as DELETE_MONITOR } from "./[id]/route";
import { PUT as UPDATE_SETTINGS } from "./settings/route";
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

describe("/api/channel-monitors", () => {
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

    const response = await GET();

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: "Unauthorized" });
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("proxies the list for an authenticated user", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      jsonResponse({ settings: { enabled: true }, monitors: [], videos: [] }),
    );

    const response = await GET();

    expect(response.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledWith("/channel-monitors", {
      method: "GET",
      userId: "user-1",
      cache: "no-store",
    });
  });

  it("proxies an add with its body", async () => {
    const request = new Request("http://localhost/api/channel-monitors", {
      method: "POST",
      body: JSON.stringify({ source_input: "@speedclips" }),
    });

    await POST(request);

    expect(fetchBackend).toHaveBeenCalledWith("/channel-monitors", {
      method: "POST",
      userId: "user-1",
      extraHeaders: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_input: "@speedclips" }),
      cache: "no-store",
    });
  });

  it("proxies the master-toggle update", async () => {
    const request = new Request("http://localhost/api/channel-monitors/settings", {
      method: "PUT",
      body: JSON.stringify({ enabled: true }),
    });

    await UPDATE_SETTINGS(request);

    expect(fetchBackend).toHaveBeenCalledWith("/channel-monitors/settings", {
      method: "PUT",
      userId: "user-1",
      extraHeaders: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: true }),
      cache: "no-store",
    });
  });

  it("proxies a monitor update and delete by id", async () => {
    const request = new Request("http://localhost/api/channel-monitors/mon-1", {
      method: "PUT",
      body: JSON.stringify({ enabled: false }),
    });
    const params = { params: Promise.resolve({ id: "mon-1" }) };

    await UPDATE_MONITOR(request, params);
    expect(fetchBackend).toHaveBeenCalledWith("/channel-monitors/mon-1", {
      method: "PUT",
      userId: "user-1",
      extraHeaders: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: false }),
      cache: "no-store",
    });

    await DELETE_MONITOR(request, params);
    expect(fetchBackend).toHaveBeenCalledWith("/channel-monitors/mon-1", {
      method: "DELETE",
      userId: "user-1",
      cache: "no-store",
    });
  });
});
