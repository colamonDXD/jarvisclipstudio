import { clipExportPollDelayMs } from "./clip-export";

export type ClipRerenderStatus = "queued" | "processing" | "completed" | "failed";

export type ClipRerenderOutcome = {
  job_id: string;
  status: ClipRerenderStatus;
  clip: Record<string, unknown> | null;
  error: string | null;
};

const ACTIVE_STATUSES = new Set<ClipRerenderStatus>(["queued", "processing"]);

function isClipRerenderOutcome(value: unknown): value is ClipRerenderOutcome {
  if (!value || typeof value !== "object") return false;
  const outcome = value as Partial<ClipRerenderOutcome>;
  return typeof outcome.job_id === "string" && typeof outcome.status === "string";
}

async function readOutcome(
  response: Response,
  readError: (response: Response) => Promise<string>,
  fallbackMessage: string,
): Promise<ClipRerenderOutcome> {
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(fallbackMessage);
  }
  if (!isClipRerenderOutcome(payload)) {
    throw new Error(fallbackMessage);
  }
  return {
    job_id: payload.job_id,
    status: payload.status,
    clip:
      payload.clip && typeof payload.clip === "object"
        ? (payload.clip as Record<string, unknown>)
        : null,
    error: typeof payload.error === "string" ? payload.error : null,
  };
}

/**
 * Queue a per-clip re-render and poll until the worker finishes it.
 * Mirrors pollClipExport: the refreshed clip payload is returned so the
 * caller can swap it into local state without a full task refetch.
 */
export async function pollClipRerender({
  start,
  status,
  readError,
  fallbackMessage,
  timeoutMessage,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  now = () => Date.now(),
  maxWaitMs = 20 * 60 * 1000,
}: {
  start: () => Promise<Response>;
  status: (jobId: string) => Promise<Response>;
  readError: (response: Response) => Promise<string>;
  fallbackMessage: string;
  timeoutMessage: string;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  maxWaitMs?: number;
}): Promise<ClipRerenderOutcome> {
  const startedAt = now();
  let outcome = await readOutcome(await start(), readError, fallbackMessage);
  let attempt = 0;
  while (ACTIVE_STATUSES.has(outcome.status)) {
    if (now() - startedAt > maxWaitMs) {
      throw new Error(timeoutMessage);
    }
    await sleep(clipExportPollDelayMs(attempt));
    attempt += 1;
    outcome = await readOutcome(await status(outcome.job_id), readError, fallbackMessage);
  }
  if (outcome.status === "failed") {
    throw new Error(outcome.error || fallbackMessage);
  }
  if (outcome.status !== "completed") {
    throw new Error(fallbackMessage);
  }
  return outcome;
}
