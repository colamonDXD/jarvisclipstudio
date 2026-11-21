/**
 * Hand-drawn facecam/gameplay boxes for live clipping.
 *
 * The user draws two rectangles on a frame of the stream; the boxes are
 * stored as fractions of the frame ({x, y, w, h} in 0..1) and sent with
 * the session create, where the backend's `validate_boxes` cleans them
 * and the render stacks the two cover crops 9:16 (face panel 45% of the
 * height, like the backend's FACE_PANEL_H / OUT_H). The helpers here
 * mirror the backend math (media/gaming_templates.py + media/ffmpeg.py)
 * so the on-page preview shows what the render will do.
 */

export type FractionBox = { x: number; y: number; w: number; h: number };
export type BoxArrangement = "top" | "bottom";
export type ManualBoxes = { cam: FractionBox; game: FractionBox; arrange: BoxArrangement };

/** Face panel share of the output height (backend: 864 / 1920). */
export const FACE_PANEL_SHARE = 0.45;
/** Smallest box edge the backend accepts (validate_boxes). */
export const MIN_BOX_EDGE = 0.03;

const round4 = (v: number) => Math.round(v * 10000) / 10000;
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** A drawn box is sendable only when both edges meet the backend minimum. */
export function isUsableBox(box: FractionBox | null | undefined): box is FractionBox {
  return !!box && box.w >= MIN_BOX_EDGE && box.h >= MIN_BOX_EDGE;
}

/** The session-create payload, or null unless both boxes are usable. */
export function boxesPayload(
  cam: FractionBox | null,
  game: FractionBox | null,
  arrange: BoxArrangement,
): ManualBoxes | null {
  if (!isUsableBox(cam) || !isUsableBox(game)) return null;
  return { cam, game, arrange };
}

/** Pointer position over the canvas element -> frame fractions (clamped). */
export function pointerToFraction(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
): { fx: number; fy: number } {
  if (rect.width <= 0 || rect.height <= 0) return { fx: 0, fy: 0 };
  return {
    fx: clamp01((clientX - rect.left) / rect.width),
    fy: clamp01((clientY - rect.top) / rect.height),
  };
}

/** A drag from `start` to `current` (fractions) -> the normalized box. */
export function dragBox(
  start: { fx: number; fy: number },
  current: { fx: number; fy: number },
): FractionBox {
  const x = Math.min(start.fx, current.fx);
  const y = Math.min(start.fy, current.fy);
  return {
    x: round4(x),
    y: round4(y),
    w: round4(Math.abs(current.fx - start.fx)),
    h: round4(Math.abs(current.fy - start.fy)),
  };
}

/** Per-channel storage key: the same streamer's layout comes back. */
export function channelKeyForUrl(rawUrl: string): string {
  const url = rawUrl.trim();
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");
    const parts = parsed.pathname.split("/").filter(Boolean);
    if (host === "twitch.tv" && parts[0]) return `twitch:${parts[0].toLowerCase()}`;
    if ((host === "youtube.com" || host === "m.youtube.com") && parts[0]) {
      if (parts[0].startsWith("@")) return `youtube:${parts[0].toLowerCase()}`;
      const id = parsed.searchParams.get("v");
      if (id) return `youtube:${id}`;
      return `youtube:${parts[0].toLowerCase()}`;
    }
    if (host === "youtu.be" && parts[0]) return `youtube:${parts[0]}`;
    return `${host}${parsed.pathname}`.replace(/\/$/, "") || url;
  } catch {
    return url;
  }
}

export function storageKeyForUrl(rawUrl: string): string {
  return `live-boxes:${channelKeyForUrl(rawUrl)}`;
}

type StorageLike = Pick<Storage, "getItem" | "setItem">;

function asManualBoxes(value: unknown): ManualBoxes | null {
  if (!value || typeof value !== "object") return null;
  const v = value as { cam?: unknown; game?: unknown; arrange?: unknown };
  const box = (b: unknown): FractionBox | null => {
    if (!b || typeof b !== "object") return null;
    const { x, y, w, h } = b as Record<string, unknown>;
    if ([x, y, w, h].some((n) => typeof n !== "number" || !Number.isFinite(n))) return null;
    const out = { x: x as number, y: y as number, w: w as number, h: h as number };
    return isUsableBox(out) ? out : null;
  };
  const cam = box(v.cam);
  const game = box(v.game);
  if (!cam || !game) return null;
  return { cam, game, arrange: v.arrange === "bottom" ? "bottom" : "top" };
}

export function loadSavedBoxes(storage: StorageLike, rawUrl: string): ManualBoxes | null {
  try {
    const raw = storage.getItem(storageKeyForUrl(rawUrl));
    return raw ? asManualBoxes(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

export function saveBoxes(storage: StorageLike, rawUrl: string, boxes: ManualBoxes): void {
  try {
    storage.setItem(storageKeyForUrl(rawUrl), JSON.stringify(boxes));
  } catch {
    // Storage full/blocked: saving is a convenience, never fatal.
  }
}

// --- Preview geometry: mirrors backend _cover_crop (pixels, even sizes) ---

function roundToEven(value: number): number {
  const v = Math.trunc(value);
  return v - (v % 2);
}

function clampEven(value: number, minimum: number, maximum: number): number {
  if (maximum < minimum) return roundToEven(minimum);
  return roundToEven(Math.max(minimum, Math.min(value, maximum)));
}

/** The largest `aspect` (w/h) crop centred inside the drawn box, in source pixels. */
export function coverCropPx(
  box: FractionBox,
  frameW: number,
  frameH: number,
  aspect: number,
): { x: number; y: number; w: number; h: number } {
  const bx = box.x * frameW;
  const by = box.y * frameH;
  const bw = box.w * frameW;
  const bh = box.h * frameH;
  let cw = bw;
  let ch = bw / aspect;
  if (ch > bh) {
    ch = bh;
    cw = bh * aspect;
  }
  cw = Math.max(2, roundToEven(cw));
  ch = Math.max(2, roundToEven(ch));
  return {
    x: clampEven(bx + (bw - cw) / 2, 0, Math.max(0, frameW - cw)),
    y: clampEven(by + (bh - ch) / 2, 0, Math.max(0, frameH - ch)),
    w: cw,
    h: ch,
  };
}
