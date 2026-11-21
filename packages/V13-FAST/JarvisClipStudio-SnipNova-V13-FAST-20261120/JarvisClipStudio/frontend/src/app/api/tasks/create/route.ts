import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";

export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const payload = await request.text();
  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const idempotencyKey = request.headers.get("idempotency-key");
  let upstream: Response;
  let responseText: string;
  try {
    upstream = await fetch(`${apiUrl}/tasks/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...buildBackendAuthHeaders(session.user.id),
        ...(await backendTraceHeaders()),
        ...(idempotencyKey ? { "idempotency-key": idempotencyKey } : {}),
      },
      body: payload,
    });
    responseText = await upstream.text();
  } catch {
    // A network-level failure (backend down or unreachable) must surface
    // as a readable 502 the form can show — never as a raw thrown
    // TypeError ("Failed to fetch").
    return NextResponse.json(
      {
        error:
          "The video service could not be reached. Check that the app is running, then try again.",
      },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
  const traceId = upstream.headers.get("x-trace-id");
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
      ...(traceId ? { "x-trace-id": traceId } : {}),
    },
  });
}
