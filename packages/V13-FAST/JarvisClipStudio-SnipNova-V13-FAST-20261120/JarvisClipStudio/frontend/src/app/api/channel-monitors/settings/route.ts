import { NextResponse } from "next/server";

import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export async function PUT(request: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.text();
  const upstream = await fetchBackend("/channel-monitors/settings", {
    method: "PUT",
    userId: session.user.id,
    extraHeaders: { "Content-Type": "application/json" },
    body: body || "{}",
    cache: "no-store",
  });

  return createTextProxyResponse(upstream);
}
