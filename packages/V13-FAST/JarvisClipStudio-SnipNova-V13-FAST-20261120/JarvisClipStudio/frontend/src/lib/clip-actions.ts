import { ApiRequestError, parseApiError } from "@/lib/api-error";

export const EXPORT_PRESETS = [
  { id: "tiktok", label: "TikTok", width: 1080, height: 1920, bitrate: 10_000_000, highFpsBitrate: 10_000_000, audioBitrate: 192_000 },
  { id: "reels", label: "Reels", width: 1080, height: 1920, bitrate: 12_000_000, highFpsBitrate: 12_000_000, audioBitrate: 192_000 },
  // YouTube's published upload-encoding table for 1080p SDR: 8 Mbps at
  // standard frame rates, 12 Mbps at 48-60 fps, AAC stereo at 384 kbps.
  // Mirror of EXPORT_PRESETS in the backend's clip_editor.py — keep the
  // two tables in step.
  { id: "shorts", label: "Shorts", width: 1080, height: 1920, bitrate: 8_000_000, highFpsBitrate: 12_000_000, audioBitrate: 384_000 },
] as const;

/** The video bitrate a preset encodes at for a given frame rate:
 * `bitrate` up to 30 fps, `highFpsBitrate` above it. Unknown presets
 * fall back to TikTok's rate, as the backend's table does. */
export function exportPresetBitrate(
  presetId: string,
  fps?: number | null,
): number {
  const preset =
    EXPORT_PRESETS.find((p) => p.id === presetId) ?? EXPORT_PRESETS[0];
  return fps != null && fps > 30 ? preset.highFpsBitrate : preset.bitrate;
}

/** The audio bitrate a preset encodes at. Unknown presets fall back to
 * TikTok's rate, as the backend's table does — the same fallback
 * `exportPresetBitrate` applies to video. */
export function exportPresetAudioBitrate(presetId: string): number {
  const preset =
    EXPORT_PRESETS.find((p) => p.id === presetId) ?? EXPORT_PRESETS[0];
  return preset.audioBitrate;
}

export function getClipUrl(videoUrl: string, version?: string) {
  const url = videoUrl.startsWith("/api/") ? videoUrl : `/api${videoUrl}`;
  return version ? `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(version)}` : url;
}

export async function requestAction(url: string, method: string, body?: unknown) {
  const response = await fetch(url, {
    method,
    ...(body === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  });
  if (!response.ok) {
    throw new ApiRequestError(await parseApiError(response, "Could not save changes. Please try again."));
  }
  return response;
}

export type ClipFeedbackVerdict = "accept" | "reject";

export type ClipFeedbackReason =
  | "hook_weak"
  | "framing"
  | "captions"
  | "music"
  | "sfx"
  | "pacing"
  | "other";

/**
 * Record an explicit accept/reject verdict for one clip. This feeds the
 * learning engine: verdicts become per-niche preference adjustments
 * (selection weights, SFX density, music and caption leanings) once
 * enough of them accumulate. Resolves with the backend's
 * `{ recorded, learning_version }` payload.
 */
export async function sendClipFeedback(
  taskId: string,
  clipId: string,
  verdict: ClipFeedbackVerdict,
  reason?: ClipFeedbackReason,
) {
  const response = await requestAction(
    `/api/tasks/${encodeURIComponent(taskId)}/clips/${encodeURIComponent(clipId)}/feedback`,
    "POST",
    reason ? { verdict, reason } : { verdict },
  );
  return response.json() as Promise<{
    recorded: boolean;
    learning_version: number | null;
  }>;
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser time to begin the download before releasing the URL.
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
