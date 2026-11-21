"use client";

import { useEffect, useRef } from "react";

/**
 * AppBackground — ambient animated background layer for the app shell.
 *
 * Adapted from React Bits' Galaxy background (twinkling starfield, warp
 * flight, cursor repulsion). The upstream implementation is a WebGL
 * shader component (ogl); this is a lightweight canvas-2d
 * re-implementation with the same look and feel, so it needs no new
 * dependency and no WebGL context.
 *
 * The field ships in two tunings behind ONE token, STARFIELD_VARIANT:
 *
 * - "vivid" (the default): the starfield clearly reads as a starfield —
 *   roughly 420 stars at 1440×1000, points up to 95% alpha, and a few
 *   "hero" stars with a halo and cross sparkle — over a near-black base
 *   with a faint fixed deep-indigo depth wash. Bright points, never a
 *   bright wash; text and cards stay fully readable above it.
 * - "calm": the previous dimmer tuning (fewer, softer stars), one token
 *   change away if vivid ever feels busy for long sessions.
 *
 * Contract: fixed, pointer-events-none, behind content (negative
 * z-index via .app-bg), decorative only (aria-hidden). The render loop
 * pauses when the tab is hidden, and under prefers-reduced-motion a
 * single static frame is drawn instead — with the same preset, so the
 * still frame still reads as a starfield. Without a canvas 2d context
 * (e.g. jsdom) it renders the plain dark layer.
 *
 * Source: https://github.com/DavidHDev/react-bits (MIT + Commons Clause —
 * see LICENSE_NOTE.md in this folder).
 */

type Star = {
  // Field position in [-1, 1] space and depth z in (0, 1]; the warp
  // drift lowers z so stars ease outward from the centre.
  x: number;
  y: number;
  z: number;
  baseAlpha: number;
  radius: number;
  twinklePhase: number;
  twinkleSpeed: number;
  tint: string;
  // A hero star draws larger, with a soft halo and a cross sparkle.
  hero: boolean;
  // Cursor-repulsion offset (CSS px), eased back to zero each frame.
  ox: number;
  oy: number;
};

type StarfieldPreset = {
  // Star count = viewport area / densityDivisor, clamped to [min, max].
  densityDivisor: number;
  minStars: number;
  maxStars: number;
  alphaMin: number;
  alphaMax: number;
  alphaCap: number;
  radiusMin: number;
  radiusMax: number;
  heroFraction: number;
  // Warp drift speed (z per second).
  drift: number;
  // Alpha of the fixed deep-indigo radial wash at the canvas centre.
  washAlpha: number;
  tints: string[];
};

const STARFIELD_PRESETS: Record<"vivid" | "calm", StarfieldPreset> = {
  vivid: {
    densityDivisor: 3400,
    minStars: 240,
    maxStars: 620,
    alphaMin: 0.35,
    alphaMax: 0.95,
    alphaCap: 0.95,
    radiusMin: 0.6,
    radiusMax: 1.9,
    heroFraction: 0.07,
    drift: 0.016,
    washAlpha: 0.2,
    tints: ["255,255,255", "255,255,255", "255,255,255", "196,181,253", "147,197,253", "253,224,171"],
  },
  calm: {
    densityDivisor: 5200,
    minStars: 170,
    maxStars: 430,
    alphaMin: 0.22,
    alphaMax: 0.7,
    alphaCap: 0.78,
    radiusMin: 0.5,
    radiusMax: 1.5,
    heroFraction: 0.03,
    drift: 0.011,
    washAlpha: 0.12,
    tints: ["255,255,255", "255,255,255", "255,255,255", "196,181,253", "147,197,253"],
  },
};

// THE starfield token: flip this one value to retune the whole field.
const STARFIELD_VARIANT: keyof typeof STARFIELD_PRESETS = "vivid";
const PRESET = STARFIELD_PRESETS[STARFIELD_VARIANT];

function makeStar(randomZ = false): Star {
  return {
    x: Math.random() * 2 - 1,
    y: Math.random() * 2 - 1,
    z: randomZ ? 0.15 + Math.random() * 0.85 : 1,
    baseAlpha: PRESET.alphaMin + Math.random() * (PRESET.alphaMax - PRESET.alphaMin),
    radius: PRESET.radiusMin + Math.random() * (PRESET.radiusMax - PRESET.radiusMin),
    twinklePhase: Math.random() * Math.PI * 2,
    twinkleSpeed: 0.4 + Math.random() * 1.1,
    tint: PRESET.tints[Math.floor(Math.random() * PRESET.tints.length)],
    hero: Math.random() < PRESET.heroFraction,
    ox: 0,
    oy: 0,
  };
}

export function AppBackground() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    let width = 0;
    let height = 0;
    let stars: Star[] = [];
    let wash: CanvasGradient | null = null;
    let raf = 0;
    let last = performance.now();
    let elapsed = 0;
    let running = false;
    const pointer = { x: -9999, y: -9999 };

    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.max(
        PRESET.minStars,
        Math.min(PRESET.maxStars, Math.floor((width * height) / PRESET.densityDivisor)),
      );
      stars = Array.from({ length: count }, () => makeStar(true));
      // The depth wash: a fixed deep-indigo radial, brightest at the
      // centre of the field and gone by two-thirds out. Painted first
      // every frame; it is part of the starfield, not a UI glow.
      wash = ctx.createRadialGradient(
        width / 2, height / 2, 0,
        width / 2, height / 2, Math.max(width, height) * 0.72,
      );
      wash.addColorStop(0, `rgba(58,50,130,${PRESET.washAlpha})`);
      wash.addColorStop(0.65, `rgba(58,50,130,${(PRESET.washAlpha * 0.35).toFixed(3)})`);
      wash.addColorStop(1, "rgba(58,50,130,0)");
      if (reducedMotion) draw(0);
    };

    const draw = (dt: number) => {
      ctx.clearRect(0, 0, width, height);
      if (wash) {
        ctx.fillStyle = wash;
        ctx.fillRect(0, 0, width, height);
      }
      const cx = width / 2;
      const cy = height / 2;
      const reach = Math.max(cx, cy);
      for (const star of stars) {
        if (!reducedMotion) {
          // Slow warp drift: depth falls, the star eases outward.
          star.z -= dt * PRESET.drift;
          if (star.z < 0.12) Object.assign(star, makeStar());
        }
        const scale = 1 / star.z;
        let sx = cx + star.x * reach * scale * 0.72;
        let sy = cy + star.y * reach * scale * 0.72;
        if (sx < -20 || sx > width + 20 || sy < -20 || sy > height + 20) {
          if (!reducedMotion) Object.assign(star, makeStar());
          continue;
        }
        // Subtle cursor repulsion: nearby stars lean away, then settle.
        const dx = sx - pointer.x;
        const dy = sy - pointer.y;
        const dist = Math.hypot(dx, dy);
        if (dist < 110 && dist > 0.01) {
          const push = (1 - dist / 110) * 14;
          star.ox += ((dx / dist) * push - star.ox) * 0.12;
          star.oy += ((dy / dist) * push - star.oy) * 0.12;
        } else {
          star.ox *= 0.92;
          star.oy *= 0.92;
        }
        sx += star.ox;
        sy += star.oy;
        const twinkle = reducedMotion ? 0.8 : 0.55 + 0.45 * Math.sin(elapsed * star.twinkleSpeed + star.twinklePhase);
        const alpha = Math.max(0, Math.min(PRESET.alphaCap, star.baseAlpha * twinkle * (1.35 - star.z)));
        const r = star.radius * (1.7 - star.z) * (star.hero ? 1.5 : 1);
        if (star.hero) {
          // Halo first, then the cross sparkle, then the core.
          ctx.beginPath();
          ctx.arc(sx, sy, r * 3.4, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${star.tint},${(alpha * 0.16).toFixed(3)})`;
          ctx.fill();
          ctx.beginPath();
          ctx.moveTo(sx - r * 3.1, sy);
          ctx.lineTo(sx + r * 3.1, sy);
          ctx.moveTo(sx, sy - r * 3.1);
          ctx.lineTo(sx, sy + r * 3.1);
          ctx.strokeStyle = `rgba(${star.tint},${(alpha * 0.45).toFixed(3)})`;
          ctx.lineWidth = 1;
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.arc(sx, sy, r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${star.tint},${alpha.toFixed(3)})`;
        ctx.fill();
      }
    };

    const frame = (now: number) => {
      if (!running) return;
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      elapsed += dt;
      draw(dt);
      raf = requestAnimationFrame(frame);
    };

    const start = () => {
      if (running || reducedMotion) return;
      running = true;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    };
    const stop = () => {
      running = false;
      cancelAnimationFrame(raf);
    };

    const onPointerMove = (event: PointerEvent) => {
      pointer.x = event.clientX;
      pointer.y = event.clientY;
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else start();
    };

    resize();
    if (reducedMotion) {
      draw(0);
    } else {
      start();
      window.addEventListener("resize", resize);
      window.addEventListener("pointermove", onPointerMove, { passive: true });
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      stop();
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <div aria-hidden="true" data-app-background="" className="app-bg">
      <canvas ref={canvasRef} className="app-bg__galaxy" />
    </div>
  );
}
