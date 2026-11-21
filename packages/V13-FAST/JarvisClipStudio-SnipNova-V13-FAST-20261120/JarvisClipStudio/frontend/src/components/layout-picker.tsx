"use client";

import { cn } from "@/lib/utils";

// One picker for how the 9:16 frame is laid out, with a picture of each
// option (owner, 2026-11-18: "make a clear option with photo reference").
// It sets two task fields: framing_mode (auto / fit / crop) and
// layout_template (facecam_red / facecam_gold: face on top, game below).
export type LayoutChoice = "auto" | "facecam_red" | "facecam_gold" | "fit" | "crop";

const OPTIONS: { value: LayoutChoice; label: string; hint: string }[] = [
  { value: "auto", label: "Auto", hint: "Picks per clip" },
  { value: "facecam_red", label: "Face on top", hint: "Red divider" },
  { value: "facecam_gold", label: "Face on top", hint: "Gold divider" },
  { value: "fit", label: "Full screen", hint: "Whole picture" },
  { value: "crop", label: "Crop", hint: "Zoom on the face" },
];

function Face({ x, y, s }: { x: number; y: number; s: number }) {
  // A simple person: head and shoulders.
  return (
    <g>
      <ellipse cx={x} cy={y + s * 1.55} rx={s * 1.35} ry={s * 0.75} fill="#3b82f6" />
      <circle cx={x} cy={y} r={s * 0.62} fill="#f2c4a0" />
      <path d={`M ${x - s * 0.66} ${y - s * 0.1} Q ${x} ${y - s * 1.0} ${x + s * 0.66} ${y - s * 0.1}`} fill="#3a2a1e" />
      <rect x={x - s * 0.8} y={y - s * 0.25} width={s * 0.25} height={s * 0.5} rx={s * 0.1} fill="#222" />
      <rect x={x + s * 0.55} y={y - s * 0.25} width={s * 0.25} height={s * 0.5} rx={s * 0.1} fill="#222" />
    </g>
  );
}

function Game({ x, y, w, h }: { x: number; y: number; w: number; h: number }) {
  // A daytime game scene: sky, sun, palms, road and a car.
  return (
    <g>
      <rect x={x} y={y} width={w} height={h * 0.55} fill="#7cc6f2" />
      <circle cx={x + w * 0.78} cy={y + h * 0.18} r={h * 0.08} fill="#ffd54a" />
      <rect x={x} y={y + h * 0.55} width={w} height={h * 0.45} fill="#5d6b78" />
      <rect x={x} y={y + h * 0.5} width={w} height={h * 0.07} fill="#6fbf73" />
      <rect x={x + w * 0.12} y={y + h * 0.22} width={w * 0.03} height={h * 0.3} fill="#7a5230" />
      <ellipse cx={x + w * 0.135} cy={y + h * 0.22} rx={w * 0.09} ry={h * 0.05} fill="#2e8b57" />
      <rect x={x + w * 0.05} y={y + h * 0.76} width={w * 0.9} height={h * 0.02} fill="#e6e6e6" />
      <rect x={x + w * 0.35} y={y + h * 0.6} width={w * 0.34} height={h * 0.12} rx={h * 0.03} fill="#e53935" />
      <circle cx={x + w * 0.42} cy={y + h * 0.73} r={h * 0.035} fill="#111" />
      <circle cx={x + w * 0.62} cy={y + h * 0.73} r={h * 0.035} fill="#111" />
    </g>
  );
}

function Caption({ y }: { y: number }) {
  return (
    <g>
      <rect x={16} y={y} width={28} height={6} rx={1.5} fill="#fff" stroke="#000" strokeWidth={1} />
    </g>
  );
}

function Picture({ kind }: { kind: LayoutChoice }) {
  const W = 60;
  const H = 106;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-[106px] w-[60px]" aria-hidden>
      <defs>
        <clipPath id={`phone-${kind}`}>
          <rect x={0} y={0} width={W} height={H} rx={6} />
        </clipPath>
      </defs>
      <g clipPath={`url(#phone-${kind})`}>
        <rect x={0} y={0} width={W} height={H} fill="#1f2430" />
        {(kind === "facecam_red" || kind === "facecam_gold") && (
          <>
            <rect x={0} y={0} width={W} height={48} fill="#6b4fa0" />
            <Face x={30} y={20} s={10} />
            <Game x={0} y={49} w={W} h={57} />
            <rect x={0} y={47} width={W} height={2.5} fill={kind === "facecam_red" ? "#E5262B" : "#F5B700"} />
            <Caption y={44} />
          </>
        )}
        {kind === "fit" && (
          <>
            <rect x={0} y={0} width={W} height={H} fill="#2b3340" />
            <Game x={0} y={36} w={W} h={34} />
            <rect x={2} y={38} width={13} height={9} fill="#6b4fa0" stroke="#fff" strokeWidth={0.6} />
            <Face x={8.5} y={41.5} s={2.6} />
            <Caption y={80} />
          </>
        )}
        {kind === "crop" && (
          <>
            <rect x={0} y={0} width={W} height={H} fill="#6b4fa0" />
            <Face x={30} y={40} s={18} />
            <Caption y={84} />
          </>
        )}
        {kind === "auto" && (
          <>
            <rect x={0} y={0} width={W} height={H} fill="#2b3340" />
            <text x={30} y={50} textAnchor="middle" fontSize={22} fill="#a78bfa">✦</text>
            <text x={30} y={68} textAnchor="middle" fontSize={9} fontWeight={700} fill="#e5e7eb">AUTO</text>
          </>
        )}
      </g>
      <rect x={0.5} y={0.5} width={W - 1} height={H - 1} rx={6} fill="none" stroke="currentColor" strokeOpacity={0.35} />
    </svg>
  );
}

export function LayoutPicker({
  value,
  onChange,
  disabled,
}: {
  value: LayoutChoice;
  onChange: (value: LayoutChoice) => void;
  disabled?: boolean;
}) {
  return (
    <div role="radiogroup" aria-label="Layout" className="grid grid-cols-3 gap-2 sm:grid-cols-5">
      {OPTIONS.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={`${option.label} · ${option.hint}`}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "flex flex-col items-center gap-1.5 rounded-xl border p-2 text-center transition-colors",
              selected ? "border-brand bg-brand/10 ring-1 ring-brand" : "hover:bg-muted/60",
              disabled && "cursor-not-allowed opacity-50",
            )}
          >
            <Picture kind={option.value} />
            <span className="text-xs font-medium leading-tight">{option.label}</span>
            <span className="text-[11px] leading-tight text-muted-foreground">{option.hint}</span>
          </button>
        );
      })}
    </div>
  );
}

export function layoutChoiceFrom(framingMode: "auto" | "fit" | "crop", layoutTemplate: string): LayoutChoice {
  if (layoutTemplate === "facecam_red" || layoutTemplate === "facecam_gold") return layoutTemplate;
  return framingMode;
}
