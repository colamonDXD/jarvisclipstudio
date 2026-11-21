import type { CreationModeValue } from "@/lib/creation-modes";

/**
 * The Streamer Reaction journey preset (GTA streamer-reaction
 * milestone 1): one click on the create page sets the existing
 * controls to the combination a streamer VOD (gameplay + facecam +
 * mic) needs. Every value is an existing payload field — the preset
 * invents nothing:
 *
 * - creation_mode "moments_ai" (Find Moments + AI Editing): the AI
 *   finds candidate moments and parks the task for the user's pick,
 *   then applies the full AI edit to the picks. The parking is part
 *   of the deal and the card says so in plain language.
 * - template_mode "auto": the reaction layout (facecam + gameplay
 *   stacked) only exists in the Auto template. Plain — just cuts
 *   never splits, so a Plain streamer task could not keep the
 *   facecam and the gameplay in frame together.
 * - framing_mode "auto": the layout planner decides per shot, which
 *   is what a facecam inset needs; fit/crop would fight it.
 * - niche_override "gaming": the existing gaming profile (its
 *   caption preset follows the pick, like any manual niche pick).
 * - output_format "vertical": the 9:16 deliverable.
 */
export const STREAMER_JOURNEY_PRESET = {
  creationMode: "moments_ai",
  templateMode: "auto",
  framingMode: "auto",
  nicheOverride: "gaming",
  outputFormat: "vertical",
} as const satisfies {
  creationMode: CreationModeValue;
  templateMode: "auto" | "plain";
  framingMode: "auto" | "fit" | "crop";
  nicheOverride: string;
  outputFormat: string;
};

export const STREAMER_JOURNEY_TITLE = "Streamer Reaction";

export const STREAMER_JOURNEY_BLURB =
  "For a streamer VOD with facecam and mic — GTA reactions, first plays, trailer reactions. The AI finds the moments and stops for your pick, then edits your picks with the full Auto layout, which is what keeps the facecam and the gameplay in the frame together (Plain — just cuts never splits, so it can't). Pauses and waits for you to pick the clips — if you don't pick within 15 minutes, it continues with the top picks automatically.";
