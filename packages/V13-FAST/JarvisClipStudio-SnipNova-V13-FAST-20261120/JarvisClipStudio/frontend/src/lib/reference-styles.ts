/**
 * Client for the reference-style API (Lane H2): styles the user created
 * from a reference video. The backend stores them outside the stock
 * profiles under `user-<slug>` names; those names are valid niche
 * overrides anywhere a stock niche is.
 */

export interface ReferenceStyleHeadline {
  cuts_per_min?: number | null;
  caption_frame_fraction?: number | null;
  music_bed_present?: boolean | null;
  tempo_bpm?: number | null;
}

export interface ReferenceStyleSummary {
  name: string;
  display_name: string;
  base_niche: string | null;
  /** Margin of the base-niche pick over its runner-up (null on styles
   *  created before the margin was recorded). */
  base_niche_margin?: number | null;
  /** The runner-up donor when the pick was close (null otherwise). */
  base_niche_runner_up?: string | null;
  /** True when the pick was a near-tie that must not be shown as a
   *  confident verdict (null on styles created before the flag existed). */
  base_niche_close_call?: boolean | null;
  source_file: string | null;
  headline: ReferenceStyleHeadline;
  created_at?: string;
}

export interface ReferenceStyleCreated extends ReferenceStyleSummary {
  inferred?: Record<string, string>;
  not_inferred?: string[];
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body?.detail === "string" && body.detail) return body.detail;
  } catch {
    // Non-JSON error body — the status line is all we have.
  }
  return `Request failed: ${response.status}`;
}

export async function fetchReferenceStyles(): Promise<ReferenceStyleSummary[]> {
  const response = await fetch("/api/reference-styles", { cache: "no-store" });
  if (!response.ok) throw new Error(await errorDetail(response));
  const data = (await response.json()) as { profiles?: unknown };
  return Array.isArray(data?.profiles)
    ? (data.profiles as ReferenceStyleSummary[])
    : [];
}

export async function createReferenceStyleFromUpload(
  file: File,
  name: string
): Promise<ReferenceStyleCreated> {
  const form = new FormData();
  form.append("file", file);
  form.append("name", name);
  const response = await fetch("/api/reference-styles/upload", {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as ReferenceStyleCreated;
}

export async function createReferenceStyleFromUrl(
  url: string,
  name: string
): Promise<ReferenceStyleCreated> {
  const response = await fetch("/api/reference-styles/from-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, name }),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as ReferenceStyleCreated;
}

export async function deleteReferenceStyle(name: string): Promise<void> {
  const response = await fetch(
    `/api/reference-styles/${encodeURIComponent(name)}`,
    { method: "DELETE" }
  );
  if (!response.ok) throw new Error(await errorDetail(response));
}
