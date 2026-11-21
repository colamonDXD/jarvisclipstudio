"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, Eraser, ImageDown, Loader2, Save } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  FACE_PANEL_SHARE,
  arrangements,
  boxesPayload,
  coverCropPx,
  dragBox,
  isUsableBox,
  loadSavedBoxes,
  pointerToFraction,
  saveBoxes,
  type BoxArrangement,
  type FractionBox,
  type ManualBoxes,
} from "./boxes";

const CAM_COLOR = "#ec4899"; // pink, per the handoff
const GAME_COLOR = "#22d3ee"; // cyan
const PREVIEW_W = 144;
const PREVIEW_H = 256;

function errorText(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return fallback;
}

/**
 * "Draw boxes" step: grab one frame of the stream, drag the facecam box
 * (pink) and the gameplay box (cyan) on it, preview the stacked 9:16
 * result, and send the boxes with the session so the render uses exactly
 * those crops instead of auto-detection. Boxes are fractions of the
 * frame; saved per channel in localStorage.
 */
export function BoxDraw({
  url,
  layout,
  onChange,
}: {
  url: string;
  layout: string;
  onChange: (boxes: ManualBoxes | null) => void;
}) {
  const [frameAt, setFrameAt] = useState("1200");
  const [grabbing, setGrabbing] = useState(false);
  const [hasFrame, setHasFrame] = useState(false);
  const [mode, setMode] = useState<"cam" | "game">("cam");
  const [cam, setCam] = useState<FractionBox | null>(null);
  const [game, setGame] = useState<FractionBox | null>(null);
  const [arrange, setArrange] = useState<BoxArrangement>("top");
  const [drag, setDrag] = useState<FractionBox | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const previewRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const dragStartRef = useRef<{ fx: number; fy: number } | null>(null);
  const divider = layout === "facecam_gold" ? "#C9A227" : "#E5262B";

  useEffect(() => {
    onChange(boxesPayload(cam, game, arrange));
  }, [cam, game, arrange, onChange]);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const paint = (box: FractionBox, color: string, label: string) => {
      const x = box.x * canvas.width;
      const y = box.y * canvas.height;
      const w = box.w * canvas.width;
      const h = box.h * canvas.height;
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(2, canvas.width / 320);
      ctx.strokeRect(x, y, w, h);
      ctx.fillStyle = color;
      const fontPx = Math.max(12, canvas.width / 48);
      ctx.font = `600 ${fontPx}px sans-serif`;
      const tw = ctx.measureText(label).width;
      ctx.fillRect(x, Math.max(0, y - fontPx - 6), tw + 10, fontPx + 6);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, x + 5, Math.max(fontPx, y - 5));
    };
    if (game) paint(game, GAME_COLOR, "GAMEPLAY");
    if (cam) paint(cam, CAM_COLOR, "FACE");
    if (drag) paint(drag, mode === "cam" ? CAM_COLOR : GAME_COLOR, mode === "cam" ? "FACE" : "GAMEPLAY");

    const preview = previewRef.current;
    if (!preview) return;
    const pctx = preview.getContext("2d");
    if (!pctx) return;
    pctx.fillStyle = "#000";
    pctx.fillRect(0, 0, PREVIEW_W, PREVIEW_H);
    if (!isUsableBox(cam) || !isUsableBox(game)) return;
    if (arrange !== "top" && arrange !== "bottom") {
      const paintPanel = (box: FractionBox, x: number, y: number, w: number, h: number) => {
        const crop = coverCropPx(box, img.naturalWidth, img.naturalHeight, w / h);
        pctx.drawImage(img, crop.x, crop.y, crop.w, crop.h,
          x * PREVIEW_W / 1080, y * PREVIEW_H / 1920, w * PREVIEW_W / 1080, h * PREVIEW_H / 1920);
      };
      const full = { x: 0, y: 0, w: 1, h: 1 };
      if (arrange === "blur") {
        pctx.save();
        pctx.filter = "blur(4px)";
        paintPanel(full, -24, -24, 1128, 1968);
        pctx.restore();
        const scale = Math.min(PREVIEW_W / img.naturalWidth, PREVIEW_H / img.naturalHeight);
        const w = scale * img.naturalWidth, h = scale * img.naturalHeight;
        pctx.drawImage(img, (PREVIEW_W - w) / 2, (PREVIEW_H - h) / 2, w, h);
      } else if (arrange === "side") {
        paintPanel(cam, 0, 0, 344, 1920);
        paintPanel(game, 344, 0, 736, 1920);
      } else {
        paintPanel(arrange === "fill" ? full : game, 0, 0, 1080, 1920);
        if (arrange !== "fill" && arrange !== "game") {
          const w = 344, h = arrange === "circle" ? w : 308;
          const x = arrange.endsWith("r") ? 1080 - w - 36 : 36;
          const y = arrange.startsWith("pip_b") ? 1920 - h - 180 : 72;
          pctx.save();
          if (arrange === "circle") {
            pctx.beginPath();
            pctx.arc((x + w / 2) * PREVIEW_W / 1080, (y + h / 2) * PREVIEW_H / 1920,
              w * PREVIEW_W / 2160, 0, Math.PI * 2);
            pctx.clip();
          }
          paintPanel(cam, x, y, w, h);
          pctx.restore();
        }
      }
      return;
    }
    const faceH = Math.round(PREVIEW_H * FACE_PANEL_SHARE);
    const gameH = PREVIEW_H - faceH;
    const faceCrop = coverCropPx(cam, img.naturalWidth, img.naturalHeight, PREVIEW_W / faceH);
    const gameCrop = coverCropPx(game, img.naturalWidth, img.naturalHeight, PREVIEW_W / gameH);
    const panels = arrange === "top"
      ? [{ crop: faceCrop, y: 0, h: faceH }, { crop: gameCrop, y: faceH, h: gameH }]
      : [{ crop: gameCrop, y: 0, h: gameH }, { crop: faceCrop, y: gameH, h: faceH }];
    for (const p of panels) {
      pctx.drawImage(img, p.crop.x, p.crop.y, p.crop.w, p.crop.h, 0, p.y, PREVIEW_W, p.h);
    }
    pctx.fillStyle = divider;
    pctx.fillRect(0, (arrange === "top" ? faceH : gameH) - 1, PREVIEW_W, 2);
  }, [cam, game, drag, mode, arrange, divider]);

  useEffect(() => {
    redraw();
  }, [redraw, hasFrame]);

  async function grabFrame() {
    if (!url.trim()) return;
    setGrabbing(true);
    try {
      const response = await fetch(
        `/api/live/frame?url=${encodeURIComponent(url.trim())}&at=${encodeURIComponent(frameAt)}`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        toast.error(errorText(body, "Could not grab a frame from that link."));
        return;
      }
      const blob = await response.blob();
      const img = new Image();
      const objectUrl = URL.createObjectURL(blob);
      img.onload = () => {
        if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = objectUrl;
        imgRef.current = img;
        const canvas = canvasRef.current;
        if (canvas) {
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
        }
        const saved = loadSavedBoxes(window.localStorage, url);
        if (saved && !cam && !game) {
          setCam(saved.cam);
          setGame(saved.game);
          setArrange(saved.arrange);
          toast.success("Loaded your saved boxes for this channel.");
        }
        setHasFrame(true);
      };
      img.onerror = () => toast.error("That frame could not be displayed.");
      img.src = objectUrl;
    } finally {
      setGrabbing(false);
    }
  }

  function onPointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!hasFrame) return;
    const rect = event.currentTarget.getBoundingClientRect();
    dragStartRef.current = pointerToFraction(event.clientX, event.clientY, rect);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag(null);
  }

  function onPointerMove(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!dragStartRef.current) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setDrag(dragBox(dragStartRef.current, pointerToFraction(event.clientX, event.clientY, rect)));
  }

  function onPointerUp(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!dragStartRef.current) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const box = dragBox(dragStartRef.current, pointerToFraction(event.clientX, event.clientY, rect));
    dragStartRef.current = null;
    setDrag(null);
    if (!isUsableBox(box)) return;
    if (mode === "cam") setCam(box);
    else setGame(box);
    setMode(mode === "cam" ? "game" : "cam");
  }

  function save() {
    const payload = boxesPayload(cam, game, arrange);
    if (!payload || !url.trim()) return;
    saveBoxes(window.localStorage, url, payload);
    toast.success("Boxes saved for this channel.");
  }

  function clear() {
    setCam(null);
    setGame(null);
  }

  const ready = isUsableBox(cam) && isUsableBox(game);

  return (
    <div className="sm:col-span-2 rounded-xl border border-dashed p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium">Draw boxes (optional)</p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Skip the guesswork: draw the streamer&apos;s facecam and the gameplay on one frame, and every clip
            uses exactly those crops. Without boxes, the facecam is auto-detected.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select aria-label="Frame moment" value={frameAt} onChange={(e) => setFrameAt(e.target.value)}
            className="h-9 rounded-md border bg-background px-2 text-sm">
            <option value="1200">Finished stream · 20 min in</option>
            <option value="0">Live · right now</option>
          </select>
          <Button type="button" variant="outline" size="sm" onClick={() => void grabFrame()}
            disabled={grabbing || !url.trim()}>
            {grabbing ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <ImageDown className="mr-1.5 h-4 w-4" />}
            Grab frame
          </Button>
        </div>
      </div>

      {hasFrame && (
        <div className="mt-4 flex flex-wrap gap-4">
          <div className="min-w-0 flex-1 basis-72">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Button type="button" size="sm" variant={mode === "cam" ? "default" : "outline"}
                onClick={() => setMode("cam")}>
                <span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CAM_COLOR }} />
                Face box
              </Button>
              <Button type="button" size="sm" variant={mode === "game" ? "default" : "outline"}
                onClick={() => setMode("game")}>
                <span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm" style={{ background: GAME_COLOR }} />
                Gameplay box
              </Button>
              <span className="text-xs text-muted-foreground">Drag on the frame to draw the {mode === "cam" ? "facecam" : "gameplay"}.</span>
            </div>
            <canvas ref={canvasRef} data-testid="box-canvas"
              className="w-full cursor-crosshair rounded-lg border touch-none"
              onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} />
          </div>
          <div className="shrink-0">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">9:16 result</p>
            <canvas ref={previewRef} width={PREVIEW_W} height={PREVIEW_H} className="rounded-lg border bg-black" />
            <select aria-label="Layout arrangement" value={arrange}
              onChange={(event) => setArrange(event.target.value as BoxArrangement)}
              className="mt-2 block w-full rounded-md border bg-background px-2 py-1 text-xs">
              {Object.entries(arrangements).map(([value, label]) =>
                <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
        </div>
      )}

      {hasFrame && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="outline" onClick={save} disabled={!ready || !url.trim()}>
            <Save className="mr-1.5 h-4 w-4" /> Save for this channel
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={clear} disabled={!cam && !game}>
            <Eraser className="mr-1.5 h-4 w-4" /> Clear boxes
          </Button>
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <Camera className="h-3.5 w-3.5" />
            {ready ? "These boxes will be sent with the session." : "Draw both boxes to use them for this session."}
          </span>
        </div>
      )}
    </div>
  );
}
