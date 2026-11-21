import { GET } from "./route";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/backend-api", async () => {
  const actual = await vi.importActual<typeof import("@/server/backend-api")>(
    "@/server/backend-api",
  );
  return {
    ...actual,
    fetchBackend: vi.fn(),
  };
});

describe("/api/reference-styles", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 when unauthenticated", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);

    const response = await GET();

    expect(response.status).toBe(401);
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("forwards the backend list response and trace headers", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ profiles: [] }), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          "x-trace-id": "trace-1",
        },
      }),
    );

    const response = await GET();

    expect(fetchBackend).toHaveBeenCalledWith(
      "/reference-styles",
      expect.objectContaining({
        method: "GET",
        userId: "user-1",
      }),
    );
    expect(response.headers.get("x-trace-id")).toBe("trace-1");
    await expect(response.json()).resolves.toEqual({ profiles: [] });
  });
});
