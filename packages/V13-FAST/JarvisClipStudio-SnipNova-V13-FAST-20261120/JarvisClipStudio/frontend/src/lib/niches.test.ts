import { describe, expect, it } from "vitest";

import { NICHE_CAPTION_PRESETS, NICHE_OPTIONS } from "./niches";

describe("NICHE_CAPTION_PRESETS", () => {
  it("covers every selectable niche exactly once", () => {
    expect(Object.keys(NICHE_CAPTION_PRESETS).sort()).toEqual(
      NICHE_OPTIONS.map((option) => option.value).sort(),
    );
  });

  it("maps Comedy to its own centre-pop preset, not the podcast look", () => {
    expect(NICHE_CAPTION_PRESETS.meme_comedy).toBe("large_centre_pop");
    expect(NICHE_CAPTION_PRESETS.podcast_news).toBe("hormozi");
  });
});
