import { NextResponse } from "next/server";

import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ videoId: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { videoId } = await params;
  const upstream = await fetchBackend(
    `/channel-monitors/videos/${encodeURIComponent(videoId)}/retry`,
    {
      method: "POST",
      userId: session.user.id,
      cache: "no-store",
    },
  );

  return createTextProxyResponse(upstream);
}
