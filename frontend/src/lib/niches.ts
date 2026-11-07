export const NICHE_OPTIONS = [
  { value: "podcast_news", label: "Podcast & news" },
  { value: "travel_vlog", label: "Travel vlog" },
  { value: "meme_comedy", label: "Meme & comedy" },
  { value: "gaming", label: "Gaming" },
  { value: "sports", label: "Sports" },
  { value: "education_tutorial", label: "Education & tutorial" },
  { value: "finance_business", label: "Finance & business" },
  { value: "vlog_lifestyle", label: "Vlog & lifestyle" },
] as const;

export type NicheOverride = (typeof NICHE_OPTIONS)[number]["value"];

export function nicheLabel(niche: string | null | undefined) {
  return NICHE_OPTIONS.find((option) => option.value === niche)?.label ?? "Auto-detect";
}
