import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * ShinyText — CSS-driven soft sheen sweeping across text.
 *
 * Adapted from React Bits ShinyText (MIT + Commons Clause — see
 * LICENSE_NOTE.md in this folder). Upstream animates with a
 * requestAnimationFrame loop; this variant is pure CSS, mirroring the app's
 * existing title-sheen pattern: one soft sweep per cycle, then rest.
 *
 * Retuned for the darker theme: the sheen is tinted with `--brand-2` (a muted
 * blue-grey) instead of a bright white highlight, so it reads as a gentle
 * light pass, never a glare.
 *
 * Source: https://github.com/DavidHDev/react-bits
 * (src/ts-tailwind/TextAnimations/ShinyText)
 */
export function ShinyText({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("rb-shiny-text", className)}>{children}</span>;
}
