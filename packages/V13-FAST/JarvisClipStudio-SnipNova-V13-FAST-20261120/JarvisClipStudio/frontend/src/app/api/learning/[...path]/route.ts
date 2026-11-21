import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

async function proxyLearningRequest(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { path } = await params;
  const incomingUrl = new URL(request.url);
  const targetPath = `/learning/${path.join("/")}${incomingUrl.search}`;
  // Learning calls carry small JSON bodies (rollback); forward the raw
  // bytes so nothing is re-encoded on the way through.
  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.arrayBuffer();

  const upstream = await fetchBackend(targetPath, {
    method: request.method,
    signal: request.signal,
    userId: session.user.id,
    extraHeaders: {
      ...(body && request.headers.get("content-type")
        ? { "Content-Type": request.headers.get("content-type") as string }
        : {}),
      ...(request.headers.get("accept")
        ? { Accept: request.headers.get("accept") as string }
        : {}),
    },
    body,
    cache: "no-store",
  });

  return createProxyResponse(upstream);
}

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyLearningRequest(request, context);
}

export async function POST(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyLearningRequest(request, context);
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyLearningRequest(request, context);
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxyLearningRequest(request, context);
}
