import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

async function proxySfxRequest(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { path } = await params;
  const incomingUrl = new URL(request.url);
  const targetPath = `/sfx/${path.join("/")}${incomingUrl.search}`;

  const upstream = await fetchBackend(targetPath, {
    method: request.method,
    signal: request.signal,
    userId: session.user.id,
    extraHeaders: {
      ...(request.headers.get("accept")
        ? { Accept: request.headers.get("accept") as string }
        : {}),
      // Preview playback seeks: pass range headers through so the
      // backend's FileResponse can answer with partial content.
      ...(request.headers.get("range")
        ? { Range: request.headers.get("range") as string }
        : {}),
    },
    cache: "no-store",
  });

  return createProxyResponse(upstream);
}

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxySfxRequest(request, context);
}
