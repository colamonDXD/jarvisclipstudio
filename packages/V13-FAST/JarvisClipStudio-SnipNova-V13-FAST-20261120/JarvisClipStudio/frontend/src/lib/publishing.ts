/**
 * Client for the publishing workspace API.
 *
 * The workspace never publishes anything: it prepares export bundles
 * and stores the user's own records — posts they made by hand and the
 * performance numbers they type in afterwards. Performance figures are
 * labelled `entered_by_user` end to end.
 */

export type PlatformId = "youtube_shorts" | "instagram_reels" | "tiktok";
export type PostPlatform = PlatformId | "other";

export interface ReadinessCheck {
  name: string;
  status: "pass" | "warn" | "fail" | "unknown";
  detail: string;
}

export interface Readiness {
  platform: string;
  status: "ready" | "warning" | "blocked" | "unknown";
  checks: ReadinessCheck[];
}

export interface EnteredStats {
  post_id: string;
  views: number | null;
  likes: number | null;
  comments: number | null;
  earnings: number | null;
  updated_at: string | null;
}

export interface ClipPost {
  id: string;
  clip_id: string;
  platform: string;
  post_url: string;
  posted_at: string | null;
  campaign_id: string | null;
  campaign_name?: string | null;
  submission_url: string | null;
  submission_dismissed: boolean;
  needs_submission?: boolean;
  stats?: EnteredStats | null;
}

export interface BoardClip {
  clip_id: string;
  task_id: string;
  title: string | null;
  filename: string | null;
  source_title: string | null;
  duration: number | null;
  width: number | null;
  height: number | null;
  niche: string | null;
  qc_status: string | null;
  captions_present: boolean | null;
  hook_status: string | null;
  created_at: string | null;
  video_url: string | null;
  compliance: {
    checked: boolean;
    compliant: boolean;
    checks: Record<string, string>;
  };
  /** Campaign-brief compliance for this clip (Content Requirements). */
  brief?: import("./content-brief").BoardClipBrief | null;
  readiness: Record<PlatformId, Readiness>;
  posts: ClipPost[];
}

export interface Campaign {
  id: string;
  name: string;
  platform: string;
  campaign_url: string | null;
  budget: number | null;
  notes: string | null;
  created_at: string | null;
  /** Content Requirements: the campaign's saved brief, when it has one. */
  has_brief?: boolean;
  brief?: import("./content-brief").StoredBrief | null;
  example_url?: string | null;
  post_count?: number;
  entered_stats?: {
    views: number;
    likes: number;
    comments: number;
    earnings: number;
  } | null;
}

export interface BoardData {
  clips: BoardClip[];
  campaigns: Campaign[];
}

export interface ProductionAnalytics {
  clips_total: number;
  tasks_total: number;
  clips_by_day: { date: string; clips: number }[];
  niche_counts: { niche: string; clips: number }[];
  qc_distribution: Record<string, number>;
  compliance: {
    checked_clips: number;
    compliant_clips: number;
    rate: number | null;
  };
  hook_distribution: { status: string; clips: number }[];
  stage_timings: Record<string, { mean_seconds: number; tasks: number }>;
  token_totals: {
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    unknown_usage_calls: number;
  };
}

export interface PerformanceAnalytics {
  entered_by_user: true;
  posts_total: number;
  posts_measured: number;
  totals: { views: number; likes: number; comments: number; earnings: number };
  per_clip: {
    post_id: string;
    clip_id: string;
    title: string | null;
    platform: string;
    post_url: string;
    posted_at: string | null;
    campaign_id: string | null;
    campaign_name: string | null;
    stats: EnteredStats | null;
  }[];
  per_campaign: {
    campaign_id: string | null;
    campaign_name: string | null;
    posts: number;
    views: number;
    likes: number;
    comments: number;
    earnings: number;
  }[];
}

export interface AnalyticsData {
  production: ProductionAnalytics;
  performance: PerformanceAnalytics;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/publishing${path}`, {
    cache: "no-store",
    ...init,
    ...(init?.body
      ? { headers: { "Content-Type": "application/json", ...init.headers } }
      : {}),
  });
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as T;
}

export function fetchBoard(): Promise<BoardData> {
  return request<BoardData>("/board");
}

export async function fetchCampaigns(): Promise<Campaign[]> {
  const data = await request<{ campaigns?: Campaign[] }>("/campaigns");
  return Array.isArray(data.campaigns) ? data.campaigns : [];
}

export function fetchAnalytics(): Promise<AnalyticsData> {
  return request<AnalyticsData>("/analytics");
}

export function bundleUrl(clipId: string): string {
  return `/api/publishing/clips/${clipId}/bundle`;
}

export async function createCampaign(payload: {
  name: string;
  platform?: string;
  campaign_url?: string;
  budget?: number;
  notes?: string;
}): Promise<Campaign> {
  const data = await request<{ campaign: Campaign }>("/campaigns", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.campaign;
}

export async function deleteCampaign(campaignId: string): Promise<void> {
  await request(`/campaigns/${campaignId}`, { method: "DELETE" });
}

export async function markPosted(payload: {
  clip_id: string;
  platform: PostPlatform;
  post_url?: string;
  campaign_id?: string | null;
  submission_url?: string;
}): Promise<ClipPost> {
  const data = await request<{ post: ClipPost }>("/posts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.post;
}

export async function updatePost(
  postId: string,
  payload: Partial<{
    post_url: string;
    campaign_id: string | null;
    submission_url: string | null;
    submission_dismissed: boolean;
  }>
): Promise<ClipPost> {
  const data = await request<{ post: ClipPost }>(`/posts/${postId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return data.post;
}

export async function deletePost(postId: string): Promise<void> {
  await request(`/posts/${postId}`, { method: "DELETE" });
}

export async function saveStats(
  postId: string,
  stats: {
    views?: number | null;
    likes?: number | null;
    comments?: number | null;
    earnings?: number | null;
  }
): Promise<EnteredStats> {
  const data = await request<{ stats: EnteredStats }>(`/posts/${postId}/stats`, {
    method: "PUT",
    body: JSON.stringify(stats),
  });
  return data.stats;
}
