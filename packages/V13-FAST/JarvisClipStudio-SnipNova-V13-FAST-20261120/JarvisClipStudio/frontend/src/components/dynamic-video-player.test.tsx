import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DynamicVideoPlayer from "./dynamic-video-player";

function frameOf(video: HTMLElement) {
  return video.parentElement as HTMLElement;
}

describe("DynamicVideoPlayer framing", () => {
  it("starts in the default 9:16 frame before metadata arrives", () => {
    render(<DynamicVideoPlayer src="/clips/a.mp4" />);
    const video = screen.getByLabelText("Video player");
    expect(frameOf(video).style.aspectRatio).toBe("9 / 16");
    // The video paints in normal flow (no inline-element baseline gap) and
    // fills the frame, so nothing can be displaced inside the box.
    expect(video.className).toContain("block");
    expect(video.className).toContain("object-contain");
  });

  it("adopts the clip's intrinsic ratio when metadata loads late (revisit path)", () => {
    render(<DynamicVideoPlayer src="/clips/landscape.mp4" />);
    const video = screen.getByLabelText("Video player") as HTMLVideoElement;
    // Simulate the browser reporting real dimensions only after the frame
    // was already laid out — the state transition behind the preview
    // appearing displaced/clipped when a task was reopened later.
    Object.defineProperty(video, "videoWidth", { value: 1920, configurable: true });
    Object.defineProperty(video, "videoHeight", { value: 1080, configurable: true });
    fireEvent.loadedMetadata(video);
    expect(frameOf(video).style.aspectRatio).toBe("1920 / 1080");
  });

  it("keeps the vertical ratio for a true 9:16 clip", () => {
    render(<DynamicVideoPlayer src="/clips/vertical.mp4" />);
    const video = screen.getByLabelText("Video player") as HTMLVideoElement;
    Object.defineProperty(video, "videoWidth", { value: 1080, configurable: true });
    Object.defineProperty(video, "videoHeight", { value: 1920, configurable: true });
    fireEvent.loadedMetadata(video);
    expect(frameOf(video).style.aspectRatio).toBe("1080 / 1920");
  });

  it("ignores a metadata event with no usable dimensions", () => {
    render(<DynamicVideoPlayer src="/clips/pending.mp4" />);
    const video = screen.getByLabelText("Video player") as HTMLVideoElement;
    fireEvent.loadedMetadata(video);
    expect(frameOf(video).style.aspectRatio).toBe("9 / 16");
  });
});
