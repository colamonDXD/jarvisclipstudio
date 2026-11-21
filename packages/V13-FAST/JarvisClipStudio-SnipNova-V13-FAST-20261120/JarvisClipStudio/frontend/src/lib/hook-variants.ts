import { formatSupportMessage, parseApiError } from "./api-error";
import { pollClipRerender } from "./clip-rerender";

/**
 * Hook-variant previews (Lane K): short alternative openings for a
 * finished clip, generated and applied through the task API. Generation
 * and selection are worker jobs, so both go through the same poller as
 * clip re-renders — a selection IS a re-render and its outcome carries
 * the refreshed clip.
 */

export type HookVariantKind = "current" | "cold_open" | "punch";

export interface HookPreview {
  id: string;
  task_id: string;
  clip_id: string;
  batch_id: string;
  variant: HookVariantKind;
  label: string;
  description: string;
  duration: number;
  video_url: string;
  created_at: string | null;
  selected: boolean;
}

type ReadError = (response: Response) => Promise<string>;

export async function readHookVariantError(
  response: Response,
  fallbackMessage: string,
): Promise<string> {
  return formatSupportMessage(await parseApiError(response, fallbackMessage));
}

const taskApiUrl = "/api/tasks";

function previewsUrl(taskId: string, clipId: string): string {
  return `${taskApiUrl}/${taskId}/clips/${clipId}/hook-variants`;
}

export async function fetchHookPreviews(
  taskId: string,
  clipId: string,
  readError: ReadError,
): Promise<HookPreview[]> {
  const response = await fetch(previewsUrl(taskId, clipId), {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  const payload = (await response.json()) as { previews?: HookPreview[] };
  return Array.isArray(payload.previews) ? payload.previews : [];
}

/** Queue preview generation, poll to completion, return the fresh batch. */
export async function generateHookPreviews(
  taskId: string,
  clipId: string,
  {
    readError,
    fallbackMessage,
    timeoutMessage,
  }: {
    readError: ReadError;
    fallbackMessage: string;
    timeoutMessage: string;
  },
): Promise<HookPreview[]> {
  await pollClipRerender({
    start: () =>
      fetch(previewsUrl(taskId, clipId), {
        method: "POST",
        cache: "no-store",
      }),
    status: (jobId) =>
      fetch(`${previewsUrl(taskId, clipId)}/${encodeURIComponent(jobId)}`, {
        cache: "no-store",
      }),
    readError,
    fallbackMessage,
    timeoutMessage,
  });
  return fetchHookPreviews(taskId, clipId, readError);
}

/**
 * Queue applying one preview to the full clip. The job id lives in the
 * re-render space, so the status poll is the re-render status route and
 * the outcome carries the refreshed clip (with the recorded pick).
 */
export async function applyHookVariant(
  taskId: string,
  clipId: string,
  previewId: string,
  {
    readError,
    fallbackMessage,
    timeoutMessage,
  }: {
    readError: ReadError;
    fallbackMessage: string;
    timeoutMessage: string;
  },
): Promise<Record<string, unknown> | null> {
  const outcome = await pollClipRerender({
    start: () =>
      fetch(
        `${previewsUrl(taskId, clipId)}/${encodeURIComponent(previewId)}/select`,
        { method: "POST", cache: "no-store" },
      ),
    status: (jobId) =>
      fetch(
        `${taskApiUrl}/${taskId}/clips/${clipId}/re-render/${encodeURIComponent(jobId)}`,
        { cache: "no-store" },
      ),
    readError,
    fallbackMessage,
    timeoutMessage,
  });
  return outcome.clip;
}
