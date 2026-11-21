/**
 * The real reason a task failed, as the backend recorded it.
 *
 * On failure the backend stores a sanitised, user-facing error
 * message in the task's progress_message in the same write that
 * flips the status to "error" (the raw exception stays in the
 * worker logs — provider errors can embed signed URLs and keys).
 * The task API returns that field, so a failed task's screen can
 * show the actual reason instead of only a generic "processing
 * failed" line. Returns null when there is nothing truthful to
 * add: task not in error, or no message recorded.
 */
export function taskFailureDetail(task: {
  status?: string;
  progress_message?: string | null;
} | null | undefined): string | null {
  if (!task || task.status !== "error") return null;
  const message = task.progress_message?.trim();
  return message ? message : null;
}
