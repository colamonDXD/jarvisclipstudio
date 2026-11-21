import { GET, POST } from "./route";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/backend-api", () => ({
  fetchBackend: vi.fn(),
  createProxyResponse: vi.fn(async (upstream: Response) => upstream),
  createTextProxyResponse: vi.fn(async (upstream: Response) => upstream),
}));

const context = (path: string[]) => ({ params: Promise.resolve({ path }) });

describe("/api/learning/[...path]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
  });

  it("returns 401 when no session exists", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);

    const response = await GET(
      new Request("https://app.example/api/learning/status"),
      context(["status"]),
    );

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: "Unauthorized" });
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("proxies the learning status for an authenticated user", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ enabled: true, niches: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await GET(
      new Request("https://app.example/api/learning/status"),
      context(["status"]),
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      enabled: true,
      niches: [],
    });
    expect(fetchBackend).toHaveBeenCalledWith(
      "/learning/status",
      expect.objectContaining({ method: "GET", userId: "user-1" }),
    );
  });

  it("proxies a rollback POST with its JSON body", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ rolled_back: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await POST(
      new Request("https://app.example/api/learning/rollback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ niche: "gaming" }),
      }),
      context(["rollback"]),
    );

    expect(response.status).toBe(200);
    const call = vi.mocked(fetchBackend).mock.calls[0];
    expect(call[0]).toBe("/learning/rollback");
    expect(call[1]).toEqual(
      expect.objectContaining({ method: "POST", userId: "user-1" }),
    );
    const forwarded = new TextDecoder().decode(
      call[1]?.body as ArrayBuffer,
    );
    expect(JSON.parse(forwarded)).toEqual({ niche: "gaming" });
  });
});
