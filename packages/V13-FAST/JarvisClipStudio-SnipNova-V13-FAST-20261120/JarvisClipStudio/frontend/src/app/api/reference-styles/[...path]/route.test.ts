import { DELETE, POST } from "./route";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({ getServerSession: vi.fn() }));
vi.mock("@/server/backend-api", async () => ({
  ...(await vi.importActual<typeof import("@/server/backend-api")>(
    "@/server/backend-api",
  )),
  fetchBackend: vi.fn(),
}));

const context = (path: string[]) => ({ params: Promise.resolve({ path }) });

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getServerSession).mockResolvedValue({
    user: { id: "owner" },
  } as never);
});

it("returns 401 when unauthenticated and never calls the backend", async () => {
  vi.mocked(getServerSession).mockResolvedValue(null);

  const response = await POST(
    new Request("https://app.example/api/reference-styles/from-url", {
      method: "POST",
    }),
    context(["from-url"]),
  );

  expect(response.status).toBe(401);
  expect(fetchBackend).not.toHaveBeenCalled();
});

it("proxies POST from-url with its JSON body to the backend", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(
    new Response(JSON.stringify({ name: "user-beast-mode" }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    }),
  );
  const payload = {
    url: "https://www.youtube.com/shorts/yUsCZsk4P4M",
    name: "Beast mode",
  };
  const request = new Request(
    "https://app.example/api/reference-styles/from-url",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );

  const response = await POST(request, context(["from-url"]));

  expect(response.status).toBe(201);
  await expect(response.json()).resolves.toEqual({ name: "user-beast-mode" });
  expect(fetchBackend).toHaveBeenCalledOnce();
  const [targetPath, init] = vi.mocked(fetchBackend).mock.calls[0];
  expect(targetPath).toBe("/reference-styles/from-url");
  expect(init).toEqual(
    expect.objectContaining({ method: "POST", userId: "owner" }),
  );
  expect(init?.extraHeaders as Record<string, string>).toEqual(
    expect.objectContaining({ "Content-Type": "application/json" }),
  );
  expect(JSON.parse(new TextDecoder().decode(init?.body as ArrayBuffer))).toEqual(
    payload,
  );
});

it("proxies POST upload as raw multipart bytes", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(
    new Response(JSON.stringify({ name: "user-my-style" }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    }),
  );
  const form = new FormData();
  form.append(
    "file",
    new File(["fake-video-bytes"], "reference.mp4", { type: "video/mp4" }),
  );
  form.append("name", "My style");
  const request = new Request(
    "https://app.example/api/reference-styles/upload",
    { method: "POST", body: form },
  );
  // What the route must forward, exactly: the request's own raw bytes.
  // (This test environment's FormData serializer renames the file part
  // and drops its payload, so byte-equality with the request — not the
  // filename — is the honest passthrough claim.)
  const expectedBytes = await request.clone().arrayBuffer();

  const response = await POST(request, context(["upload"]));

  expect(response.status).toBe(201);
  expect(fetchBackend).toHaveBeenCalledOnce();
  const [targetPath, init] = vi.mocked(fetchBackend).mock.calls[0];
  expect(targetPath).toBe("/reference-styles/upload");
  expect(init).toEqual(
    expect.objectContaining({ method: "POST", userId: "owner" }),
  );
  expect(
    (init?.extraHeaders as Record<string, string>)?.["Content-Type"],
  ).toContain("multipart/form-data; boundary=");
  expect(init?.body).toBeInstanceOf(ArrayBuffer);
  expect(new Uint8Array(init?.body as ArrayBuffer)).toEqual(
    new Uint8Array(expectedBytes),
  );
  const forwarded = new TextDecoder().decode(init?.body as ArrayBuffer);
  expect(forwarded).toContain('name="file"');
  expect(forwarded).toContain('name="name"');
  expect(forwarded).toContain("My style");
});

it("proxies DELETE by style name to the backend", async () => {
  vi.mocked(fetchBackend).mockResolvedValue(
    new Response(JSON.stringify({ deleted: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  const request = new Request(
    "https://app.example/api/reference-styles/user-my-style",
    { method: "DELETE" },
  );

  const response = await DELETE(request, context(["user-my-style"]));

  expect(response.status).toBe(200);
  await expect(response.json()).resolves.toEqual({ deleted: true });
  expect(fetchBackend).toHaveBeenCalledWith(
    "/reference-styles/user-my-style",
    expect.objectContaining({ method: "DELETE", userId: "owner" }),
  );
});
