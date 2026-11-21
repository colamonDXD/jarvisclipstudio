import { downloadBlob, exportPresetAudioBitrate, exportPresetBitrate } from "@/lib/clip-actions";
import {
  clamp,
  editDuration,
  exportGain,
  footageKey,
  footageUrl,
  laneHidden,
  mappedBroll,
  mappedOverlays,
  outputSize,
  overlayPieceAt,
  renderSegments,
  type EditDocument,
  type EditorState,
} from "./document";
import {
  drawEditorFrame,
  loadEditorFont,
  type BRollLayer,
  type OverlayLayer,
} from "./render";

// Device exports render at 30 fps, matching the server editor render
// (backend/src/editor_document.py forces fps=30). mediabunny's
// CanvasSource takes no framerate, so renderVideo drops source frames
// onto this grid instead; the preset video rate is resolved at this
// rate too, so Shorts always uses its 8M standard row (F-P2/F-P3).
export const DEVICE_EXPORT_FPS = 30;

/**
 * What a device export degraded on, so the caller can tell the user
 * instead of failing (F-P4/F-P5). The server render behaves the same
 * way: missing B-roll footage is left out rather than failing the
 * export, and a missing caption font falls back to the bundled fonts
 * dir with libass substituting a face (captions.py ass_fonts_dir).
 */
export interface ExportDraftResult {
  /** Footage keys whose B-roll was left out because the file was missing. */
  skippedBroll: string[];
  /** When the chosen caption font could not load, the bundled font used instead. */
  fontFallback: { requested: string; used: string } | null;
}

/** Bundled caption font the device export falls back to when the chosen
 * font cannot load — the device side of the server's ass_fonts_dir
 * fallback. TikTokSans-Regular is the app default and always bundled. */
export const DEVICE_FONT_FALLBACK = "TikTokSans-Regular";

export async function exportDraft(
  url: string,
  doc: EditDocument,
  asset: EditorState,
  preset: string,
  signal: AbortSignal,
  progress: (value: number) => void,
): Promise<ExportDraftResult> {
  // Preserve a continuous dissolve (and frame timestamps) through UI splits.
  doc = { ...doc, segments: renderSegments(doc.segments) };
  const m = await import("mediabunny");
  const size = outputSize(doc, asset.width, asset.height);
  // Resolved at the pinned output rate, not the source rate, so a
  // 59.94 fps source does not pick the Shorts 12M high-fps row.
  const bitrate = exportPresetBitrate(preset, DEVICE_EXPORT_FPS);
  // The audio rate comes from the same preset table as the video rate,
  // so Shorts encodes at 384k like the server surfaces (D-P1).
  const audioBitrate = exportPresetAudioBitrate(preset);
  const audioCodec = (await m.canEncodeAudio("aac"))
    ? "aac"
    : (await m.canEncodeAudio("opus"))
      ? "opus"
      : null;
  if (
    !(await m.canEncodeVideo("avc", { ...size, bitrate })) ||
    (asset.hasAudio && !audioCodec)
  )
    throw new Error(
      "This browser cannot encode this video. Choose background export to render it on the server.",
    );
  const skippedBroll: string[] = [];
  let fontFallback: ExportDraftResult["fontFallback"] = null;
  try {
    await loadEditorFont(doc.captions.font);
  } catch (error) {
    // Like the server render (ass_fonts_dir falls back to the bundled
    // fonts dir and libass substitutes a face), a caption font that can
    // no longer load falls back to the bundled default instead of
    // failing the export (F-P5). Only the bundled font itself being
    // gone is an honest failure — there is nothing left to draw with.
    if (doc.captions.font === DEVICE_FONT_FALLBACK) throw error;
    await loadEditorFont(DEVICE_FONT_FALLBACK);
    fontFallback = { requested: doc.captions.font, used: DEVICE_FONT_FALLBACK };
    doc = { ...doc, captions: { ...doc.captions, font: DEVICE_FONT_FALLBACK } };
    console.warn(
      `Caption font "${fontFallback.requested}" could not load; using bundled "${DEVICE_FONT_FALLBACK}" instead.`,
    );
  }
  const mediaUrl = asset.hasLayout && doc.framing.fit === "auto" && doc.framing.aspect === "vertical" ? url.replace(/clean\.mp4$/, "layout.mp4") : url;
  const response = await fetch(mediaUrl, { signal });
  if (!response.ok) throw new Error("Could not load the source video.");
  const input = new m.Input({
    source: new m.BlobSource(await response.blob()),
    formats: m.ALL_FORMATS,
  });
  const output = new m.Output({
    format: new m.Mp4OutputFormat(),
    target: new m.BufferTarget(),
  });
  const canvas = document.createElement("canvas");
  canvas.width = size.width;
  canvas.height = size.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    input.dispose();
    throw new Error("Canvas is unavailable. Use background export.");
  }
  let finalized = false;
  // Stock footage is decoded alongside the clip and drawn under the captions.
  // A hidden B-roll lane is left out of the export, like on the server.
  const pieces = laneHidden(doc, "broll") ? [] : mappedBroll(doc);
  const footage = new Map<string, { input: InstanceType<typeof m.Input>; sink: InstanceType<typeof m.VideoSampleSink> }>();
  // Uploaded overlay images are decoded up front too and drawn over the
  // B-roll, under the captions — the stack the preview and the server
  // export composite. A hidden overlays lane is left out, as on the
  // server; an upload that no longer loads is skipped, also as on the
  // server, while one the browser cannot decode at all fails honestly.
  const overlayPieces = laneHidden(doc, "overlays") ? [] : mappedOverlays(doc);
  const overlayImages = new Map<string, ImageBitmap>();
  const endpoint = url.replace(/\/media\/clean\.mp4$/, "");
  try {
    for (const piece of overlayPieces) {
      const name = piece.item.asset;
      if (overlayImages.has(name)) continue;
      const response = await fetch(`${endpoint}/media/${name}`, { signal });
      if (!response.ok) continue;
      try {
        overlayImages.set(name, await createImageBitmap(await response.blob()));
      } catch {
        throw new Error(
          "This browser cannot decode an overlay image. Use background export.",
        );
      }
    }
    for (const piece of pieces) {
      const key = footageKey(piece.clip.footage);
      if (footage.has(key)) continue;
      const response = await fetch(footageUrl(endpoint, piece.clip.footage), { signal });
      // Like the server render ("B-roll whose footage is missing is left
      // out rather than failing the export"), a stock file that is gone
      // from the editor directory skips that clip instead of failing the
      // whole export (F-P4). The render loop already draws the main
      // footage wherever a piece has no decoded sample, so a skipped
      // clip simply never covers the picture. The miss is recorded in
      // the export result so it is not silent.
      if (!response.ok) {
        skippedBroll.push(key);
        console.warn(`B-roll footage ${key} is missing; leaving it out of the export.`);
        continue;
      }
      const footageInput = new m.Input({
        source: new m.BlobSource(await response.blob()),
        formats: m.ALL_FORMATS,
      });
      const track = await footageInput.getPrimaryVideoTrack();
      if (!track || !(await track.canDecode())) {
        footageInput.dispose();
        throw new Error("This browser cannot decode the B-roll. Use background export.");
      }
      footage.set(key, { input: footageInput, sink: new m.VideoSampleSink(track) });
    }
    const video = await input.getPrimaryVideoTrack(),
      audio = await input.getPrimaryAudioTrack();
    if (
      !video ||
      !(await video.canDecode()) ||
      (audio && !(await audio.canDecode()))
    )
      throw new Error(
        "This browser cannot decode the source. Use background export.",
      );
    const videoSource = new m.CanvasSource(canvas, { codec: "avc", bitrate });
    output.addVideoTrack(videoSource);
    if (audio && !audioCodec)
      throw new Error("Audio encoding is unavailable. Use background export.");
    const audioSource =
      audio && audioCodec
        ? new m.AudioSampleSource({ codec: audioCodec, bitrate: audioBitrate })
        : null;
    if (audioSource) output.addAudioTrack(audioSource);
    await output.start();
    const duration = editDuration(doc);
    const renderVideo = async () => {
      // Frames are emitted only on a 30 fps grid (integer index, so no
      // accumulated float drift): the encoder sees a steady 30 fps
      // cadence whatever the source rate is.
      const frameStep = 1 / DEVICE_EXPORT_FPS;
      let nextFrameIndex = 0;
      let cursor = 0;
      for (const segment of doc.segments) {
        const sink = new m.VideoSampleSink(video);
        for await (const sample of sink.samples(segment.start, segment.end)) {
          try {
            signal.throwIfAborted();
            const start = Math.max(segment.start, sample.timestamp),
              end = Math.min(segment.end, sample.timestamp + sample.duration);
            if (end <= start) continue;
            const at = cursor + start - segment.start;
            const outEnd = at + (end - start);
            // Drop a source frame that holds no 30 fps grid point: at
            // 59.94 fps every second sample is skipped, at 30 fps none
            // is, and a slower source holds its frame across grid points.
            if (nextFrameIndex / DEVICE_EXPORT_FPS >= outEnd) continue;
            const piece = pieces.find((p) => at >= p.start && at < p.end);
            const brollSample = piece
              ? await footage
                  .get(footageKey(piece.clip.footage))
                  ?.sink.getSample(piece.offset + at - piece.start)
              : null;
            let layer: BRollLayer | null = null;
            if (piece && brollSample) {
              const fade = piece.fadeDuration;
              const alpha = clamp(
                Math.min(
                  piece.fadeIn && fade > 0 ? (at - piece.start) / fade : 1,
                  piece.fadeOut && fade > 0 ? (piece.end - at) / fade : 1,
                ),
                0,
                1,
              );
              layer = {
                width: brollSample.displayWidth,
                height: brollSample.displayHeight,
                alpha,
                draw: (x, y, w, h) => brollSample.draw(ctx, x, y, w, h),
              };
            }
            const overlayPiece = overlayPieceAt(overlayPieces, at);
            const overlayBitmap = overlayPiece
              ? overlayImages.get(overlayPiece.item.asset)
              : undefined;
            const overlayLayer: OverlayLayer | null =
              overlayPiece && overlayBitmap
                ? {
                    width: overlayBitmap.width,
                    height: overlayBitmap.height,
                    x: overlayPiece.item.x,
                    y: overlayPiece.item.y,
                    scale: overlayPiece.item.scale,
                    draw: (x, y, w, h) => ctx.drawImage(overlayBitmap, x, y, w, h),
                  }
                : null;
            try {
              drawEditorFrame(
                ctx,
                doc,
                sample.displayWidth,
                sample.displayHeight,
                at,
                (rect) =>
                  sample.draw(ctx, rect.x, rect.y, rect.width, rect.height),
                asset.captionLooks,
                layer,
                "all",
                overlayLayer,
                true,
              );
            } finally {
              brollSample?.close();
            }
            // Emit one frame per 30 fps grid point this sample covers,
            // each lasting one grid step, so the per-frame rate control
            // lands on the preset target instead of ~2x it (F-P3).
            while (nextFrameIndex / DEVICE_EXPORT_FPS < outEnd) {
              const frameAt = nextFrameIndex / DEVICE_EXPORT_FPS;
              const frameDuration = Math.min(frameStep, duration - frameAt);
              if (frameDuration <= 0) break;
              await videoSource.add(frameAt, frameDuration);
              nextFrameIndex += 1;
              progress(
                Math.min(
                  99,
                  Math.round(((frameAt + frameDuration) / duration) * 100),
                ),
              );
            }
          } finally {
            sample.close();
          }
        }
        cursor += segment.end - segment.start;
      }
      videoSource.close();
    };
    const renderAudio = async () => {
      if (!audio || !audioSource) return;
      let cursor = 0;
      for (const segment of doc.segments) {
        const sink = new m.AudioSampleSink(audio);
        for await (const sample of sink.samples(segment.start, segment.end)) {
          try {
            signal.throwIfAborted();
            const startFrame = Math.max(
              0,
              Math.ceil((segment.start - sample.timestamp) * sample.sampleRate),
            );
            const endFrame = Math.min(
              sample.numberOfFrames,
              Math.floor((segment.end - sample.timestamp) * sample.sampleRate),
            );
            if (endFrame <= startFrame) continue;
            const full = new Float32Array(
              sample.allocationSize({ planeIndex: 0, format: "f32" }) / 4,
            );
            sample.copyTo(full, { planeIndex: 0, format: "f32" });
            const data = full.slice(
              startFrame * sample.numberOfChannels,
              endFrame * sample.numberOfChannels,
            );
            const gain = exportGain(doc);
            for (let i = 0; i < data.length; i++) data[i] *= gain;
            const next = new m.AudioSample({
              data,
              format: "f32",
              numberOfChannels: sample.numberOfChannels,
              sampleRate: sample.sampleRate,
              timestamp:
                cursor +
                sample.timestamp +
                startFrame / sample.sampleRate -
                segment.start,
            });
            try {
              await audioSource.add(next);
            } finally {
              next.close();
            }
          } finally {
            sample.close();
          }
        }
        cursor += segment.end - segment.start;
      }
      audioSource.close();
    };
    // Await both producers before releasing input/output, including on failure.
    const outcomes = await Promise.allSettled([renderVideo(), renderAudio()]);
    const failed = outcomes.find(
      (r): r is PromiseRejectedResult => r.status === "rejected",
    );
    if (failed) throw failed.reason;
    signal.throwIfAborted();
    await output.finalize();
    finalized = true;
    if (!output.target.buffer) throw new Error("Export produced no video.");
    signal.throwIfAborted();
    downloadBlob(
      new Blob([output.target.buffer], { type: "video/mp4" }),
      `supoclip-${preset}.mp4`,
    );
    progress(100);
    return { skippedBroll, fontFallback };
  } finally {
    if (!finalized) await output.cancel().catch(() => {});
    input.dispose();
    for (const { input: footageInput } of footage.values()) footageInput.dispose();
    for (const bitmap of overlayImages.values()) bitmap.close();
  }
}
