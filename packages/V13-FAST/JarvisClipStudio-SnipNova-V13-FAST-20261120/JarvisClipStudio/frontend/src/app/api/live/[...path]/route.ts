import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

// Live clipping: every /api/live/* call goes to the backend's /live/* with
// the signed user headers. Clip and preview files stream through (Range
// forwarded so the <video> element can seek).
async function proxyLiveRequest(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { path } = await params;
  const incomingUrl = new URL(request.url);
  const body =
    request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();
  const extraHeaders: Record<string, string> = {};
  if (body) extraHeaders["Content-Type"] = "application/json";
  for (const name of ["range", "if-range", "accept"]) {
    const value = request.headers.get(name);
    if (value) extraHeaders[name] = value;
  }
  const upstream = await fetchBackend(`/live/${path.map(encodeURIComponent).join("/")}${incomingUrl.search}`, {
    method: request.method,
    signal: request.signal,
    userId: session.user.id,
    extraHeaders,
    body,
    cache: "no-store",
  });
  return createProxyResponse(upstream);
}

export const GET = proxyLiveRequest;
export const POST = proxyLiveRequest;
export const DELETE = proxyLiveRequest;
