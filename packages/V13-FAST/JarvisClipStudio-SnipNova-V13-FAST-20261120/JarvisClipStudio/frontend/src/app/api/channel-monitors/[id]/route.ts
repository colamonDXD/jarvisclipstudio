import { NextResponse } from "next/server";

import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const body = await request.text();
  const upstream = await fetchBackend(
    `/channel-monitors/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      userId: session.user.id,
      extraHeaders: { "Content-Type": "application/json" },
      body: body || "{}",
      cache: "no-store",
    },
  );

  return createTextProxyResponse(upstream);
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const upstream = await fetchBackend(
    `/channel-monitors/${encodeURIComponent(id)}`,
    {
      method: "DELETE",
      userId: session.user.id,
      cache: "no-store",
    },
  );

  return createTextProxyResponse(upstream);
}
