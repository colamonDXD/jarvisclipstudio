export const NICHE_OPTIONS = [
  { value: "podcast_news", label: "Podcast & news" },
  { value: "travel_vlog", label: "Travel vlog" },
  { value: "meme_comedy", label: "Meme & comedy" },
  { value: "gaming", label: "Gaming" },
  { value: "sports", label: "Sports" },
  { value: "education_tutorial", label: "Education & tutorial" },
  { value: "finance_business", label: "Finance & business" },
  { value: "vlog_lifestyle", label: "Vlog & lifestyle" },
  { value: "true_crime", label: "True crime" },
  { value: "hindi_podcast", label: "Hindi podcast" },
  { value: "music_artist", label: "Music artist" },
  { value: "football_edit", label: "Football edit" },
  { value: "japan_travel_cinematic", label: "Japan travel cinematic" },
  { value: "anime_funk_edit", label: "Anime funk edit" },
] as const;

export type NicheOverride = (typeof NICHE_OPTIONS)[number]["value"];

/**
 * The caption preset each niche profile declares for itself, mirroring
 * backend/src/auto_director/profiles/<niche>.json `captions.preset_id`.
 * The create form uses it to follow a manual niche pick: with the
 * caption style left on its untouched default, selecting a niche means
 * the clips should LOOK like that niche — captions included — so the
 * payload carries the profile's own preset instead of the generic
 * default (which is pixel-identical to the podcast look). Picking a
 * caption style by hand always wins over this mapping.
 */
export const NICHE_CAPTION_PRESETS: Record<string, string> = {
  podcast_news: "hormozi",
  travel_vlog: "classic_box",
  meme_comedy: "large_centre_pop",
  gaming: "tiktok_animated",
  sports: "bold_uppercase",
  education_tutorial: "hormozi",
  finance_business: "hormozi",
  vlog_lifestyle: "tiktok_animated",
  true_crime: "classic_box",
  hindi_podcast: "hormozi",
  music_artist: "neon",
  football_edit: "bold_uppercase",
  japan_travel_cinematic: "classic_box",
  anime_funk_edit: "minimal",
};

export function nicheLabel(niche: string | null | undefined) {
  return NICHE_OPTIONS.find((option) => option.value === niche)?.label ?? "Auto-detect";
}
