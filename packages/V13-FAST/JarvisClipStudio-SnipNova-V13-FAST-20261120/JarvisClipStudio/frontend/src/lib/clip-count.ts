export const CLIP_COUNT_OPTIONS = [5, 10, 15, 20, 25, 30] as const;
export type ClipCountOption = (typeof CLIP_COUNT_OPTIONS)[number];
