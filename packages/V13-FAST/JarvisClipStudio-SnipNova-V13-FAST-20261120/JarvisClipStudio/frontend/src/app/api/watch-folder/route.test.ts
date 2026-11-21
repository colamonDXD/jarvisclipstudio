import { GET } from "./route";
import { PUT } from "./settings/route";
import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/backend-api", () => ({
  fetchBackend: vi.fn(),
  createTextProxyResponse: vi.fn(async (upstream: Response) => upstream),
}));

describe("/api/watch-folder", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(createTextProxyResponse).mockImplementation(
      async (upstream: Response) => upstream,
    );
  });

  it("returns 401 when no session exists", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);

    const response = await GET();

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: "Unauthorized" });
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("proxies the settings and files for an authenticated user", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(
        JSON.stringify({
          settings: { enabled: true, effective_folder: "/data/inbox" },
          files: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const response = await GET();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      settings: { enabled: true, effective_folder: "/data/inbox" },
      files: [],
    });
    expect(fetchBackend).toHaveBeenCalledWith("/watch-folder", {
      method: "GET",
      userId: "user-1",
      cache: "no-store",
    });
  });

  it("forwards settings updates to the backend", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ settings: { enabled: true } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await PUT(
      new Request("http://localhost/api/watch-folder/settings", {
        method: "PUT",
        body: JSON.stringify({ enabled: true, folder_path: null }),
      }),
    );

    expect(response.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledWith("/watch-folder/settings", {
      method: "PUT",
      userId: "user-1",
      extraHeaders: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: true, folder_path: null }),
      cache: "no-store",
    });
  });
});
