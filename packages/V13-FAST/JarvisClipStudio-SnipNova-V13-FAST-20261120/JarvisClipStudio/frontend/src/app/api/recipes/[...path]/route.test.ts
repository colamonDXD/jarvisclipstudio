import { DELETE, GET, PATCH } from "./route";
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

describe("/api/recipes/[...path]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
  });

  it("returns 401 when no session exists", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);

    const response = await GET(
      new Request("https://app.example/api/recipes/r1"),
      context(["r1"]),
    );

    expect(response.status).toBe(401);
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("proxies a single recipe read", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ id: "r1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await GET(
      new Request("https://app.example/api/recipes/r1"),
      context(["r1"]),
    );

    expect(response.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledWith(
      "/recipes/r1",
      expect.objectContaining({ method: "GET", userId: "user-1" }),
    );
  });

  it("proxies the recipe fragment read", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ fragment: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await GET(
      new Request("https://app.example/api/recipes/r1/fragment"),
      context(["r1", "fragment"]),
    );

    expect(response.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledWith(
      "/recipes/r1/fragment",
      expect.objectContaining({ method: "GET", userId: "user-1" }),
    );
  });

  it("proxies a PATCH update and a DELETE", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ id: "r1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const patchResponse = await PATCH(
      new Request("https://app.example/api/recipes/r1", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "Renamed" }),
      }),
      context(["r1"]),
    );
    expect(patchResponse.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledWith(
      "/recipes/r1",
      expect.objectContaining({ method: "PATCH", userId: "user-1" }),
    );

    const deleteResponse = await DELETE(
      new Request("https://app.example/api/recipes/r1", { method: "DELETE" }),
      context(["r1"]),
    );
    expect(deleteResponse.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledWith(
      "/recipes/r1",
      expect.objectContaining({ method: "DELETE", userId: "user-1" }),
    );
  });
});
