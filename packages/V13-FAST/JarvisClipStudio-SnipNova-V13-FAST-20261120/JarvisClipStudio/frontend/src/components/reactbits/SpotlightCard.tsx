"use client";

import { useRef, type MouseEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * SpotlightCard — a soft glow that follows the pointer across a surface.
 *
 * Adapted from React Bits SpotlightCard (MIT + Commons Clause — see
 * LICENSE_NOTE.md in this folder). Upstream runs a requestAnimationFrame
 * loop with grain, flare and proximity tracking; this lean variant keeps
 * only the pointer-tracking glow: the handler writes CSS variables and a
 * CSS radial-gradient overlay (`.rb-spotlight` in globals.css) does the
 * rest — no animation loop, no window listeners.
 *
 * Retuned for the darker theme: the glow is a dim warm-neutral at very low
 * alpha, not a flashlight. It fades out when the pointer leaves and never
 * intercepts clicks.
 *
 * Source: https://github.com/DavidHDev/react-bits
 * (src/ts-tailwind/Components/SpotlightCard)
 */
export function useSpotlight<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);

  const onMouseMove = (event: MouseEvent<T>) => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    el.style.setProperty("--spot-x", `${event.clientX - rect.left}px`);
    el.style.setProperty("--spot-y", `${event.clientY - rect.top}px`);
    el.style.setProperty("--spot-opacity", "1");
  };

  const onMouseLeave = () => {
    ref.current?.style.setProperty("--spot-opacity", "0");
  };

  return { ref, onMouseMove, onMouseLeave };
}

export function SpotlightCard({ children, className }: { children: ReactNode; className?: string }) {
  const spot = useSpotlight<HTMLDivElement>();
  return (
    <div
      ref={spot.ref}
      onMouseMove={spot.onMouseMove}
      onMouseLeave={spot.onMouseLeave}
      className={cn("rb-spotlight", className)}
    >
      {children}
    </div>
  );
}
