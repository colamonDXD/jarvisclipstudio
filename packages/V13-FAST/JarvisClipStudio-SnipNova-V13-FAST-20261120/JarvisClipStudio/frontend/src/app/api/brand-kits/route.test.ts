import { GET, POST } from "./route";
import { PATCH } from "./[...path]/route";
import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/backend-api", () => ({
  fetchBackend: vi.fn(),
  createProxyResponse: vi.fn(async (upstream: Response) => upstream),
  createTextProxyResponse: vi.fn(async (upstream: Response) => upstream),
}));

describe("/api/brand-kits", () => {
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

  it("proxies the kit list for an authenticated user", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ kits: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await GET();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ kits: [] });
    expect(fetchBackend).toHaveBeenCalledWith("/brand-kits", {
      method: "GET",
      userId: "user-1",
      cache: "no-store",
    });
  });

  it("proxies kit creation with the JSON body intact", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ id: "kit-1", name: "Main" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await POST(
      new Request("http://localhost/api/brand-kits", {
        method: "POST",
        body: JSON.stringify({ name: "Main", primary_color: "#FF00AA" }),
      }),
    );

    expect(response.status).toBe(201);
    expect(fetchBackend).toHaveBeenCalledWith(
      "/brand-kits",
      expect.objectContaining({
        method: "POST",
        userId: "user-1",
        body: JSON.stringify({ name: "Main", primary_color: "#FF00AA" }),
      }),
    );
  });

  it("proxies a kit update to the nested backend path", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ id: "kit-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await PATCH(
      new Request("http://localhost/api/brand-kits/kit-1", {
        method: "PATCH",
        body: JSON.stringify({ accent_color: "#00FFCC" }),
      }),
      { params: Promise.resolve({ path: ["kit-1"] }) },
    );

    expect(response.status).toBe(200);
    expect(fetchBackend).toHaveBeenCalledWith(
      "/brand-kits/kit-1",
      expect.objectContaining({ method: "PATCH", userId: "user-1" }),
    );
  });
});
