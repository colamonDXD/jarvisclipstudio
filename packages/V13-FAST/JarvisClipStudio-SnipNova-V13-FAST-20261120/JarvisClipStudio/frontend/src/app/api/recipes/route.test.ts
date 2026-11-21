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

describe("/api/recipes", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
  });

  it("returns 401 when no session exists", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);

    const response = await GET();

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: "Unauthorized" });
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("proxies the recipe list for an authenticated user", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ recipes: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await GET();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ recipes: [] });
    expect(fetchBackend).toHaveBeenCalledWith("/recipes", {
      method: "GET",
      userId: "user-1",
      cache: "no-store",
    });
  });

  it("proxies recipe creation with the JSON body", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ id: "r1", name: "Podcast setup" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await POST(
      new Request("https://app.example/api/recipes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "Podcast setup", fragment: {} }),
      }),
    );

    expect(response.status).toBe(201);
    expect(fetchBackend).toHaveBeenCalledWith(
      "/recipes",
      expect.objectContaining({
        method: "POST",
        userId: "user-1",
        body: JSON.stringify({ name: "Podcast setup", fragment: {} }),
      }),
    );
  });
});
