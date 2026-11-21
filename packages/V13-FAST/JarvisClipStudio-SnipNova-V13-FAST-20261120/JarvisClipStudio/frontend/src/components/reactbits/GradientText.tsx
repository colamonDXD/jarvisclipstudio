import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * GradientText — CSS-driven gradient text over the app's brand tokens.
 *
 * Adapted from React Bits GradientText (MIT + Commons Clause — see
 * LICENSE_NOTE.md in this folder). This variant is pure CSS: an animated
 * `background-clip: text` gradient (`--brand` → `--brand-2` → `--brand`)
 * slowly panning. No JavaScript animation.
 *
 * Source: https://github.com/DavidHDev/react-bits
 * (src/ts-tailwind/TextAnimations/GradientText)
 */
export function GradientText({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("rb-gradient-text", className)}>{children}</span>;
}
