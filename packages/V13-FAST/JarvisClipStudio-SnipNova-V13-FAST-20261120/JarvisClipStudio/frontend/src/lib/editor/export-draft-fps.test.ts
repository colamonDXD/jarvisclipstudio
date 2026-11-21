import { beforeEach, describe, expect, it, vi } from "vitest";
import { draft } from "./document.test-fixture";
import type { EditorState } from "./document";

// F-P2/F-P3: the on-device export followed the source frame rate, so a
// 59.94 fps source exported at ~60 fps (181 frames in 3 s) and the
// per-frame video rate control emitted ~2x the preset target, while the
// server editor render forces 30 fps. These tests drive the real
// exportDraft with mediabunny stubbed and synthetic source samples, and
// assert the frames actually handed to the encoder sit on a 30 fps grid
// with the standard-row preset rate — the same pin the server applies.

const captured = vi.hoisted(() => ({
  audioSources: [] as Array<{ codec: string; bitrate: number }>,
  videoSources: [] as Array<{ codec: string; bitrate: number }>,
  videoAdds: [] as Array<{ timestamp: number; duration?: number }>,
  sampleFps: 59.94,
}));

vi.mock("mediabunny", () => {
  class BlobSource {
    constructor(private _options: unknown) {}
  }
  const videoTrack = { canDecode: async () => true };
  const audioTrack = { canDecode: async () => true };
  class Input {
    constructor(private _options: unknown) {}
    async getPrimaryVideoTrack() {
      return videoTrack;
    }
    async getPrimaryAudioTrack() {
      return audioTrack;
    }
    dispose() {}
  }
  class Output {
    target: { buffer: ArrayBuffer | null } = { buffer: new ArrayBuffer(8) };
    constructor(private _options: unknown) {}
    addVideoTrack(...args: unknown[]) {
      void args;
    }
    addAudioTrack(...args: unknown[]) {
      void args;
    }
    async start() {}
    async finalize() {}
    async cancel() {}
  }
  class CanvasSource {
    constructor(
      _canvas: unknown,
      options: { codec: string; bitrate: number },
    ) {
      captured.videoSources.push(options);
    }
    async add(timestamp: number, duration?: number) {
      captured.videoAdds.push({ timestamp, duration });
    }
    close() {}
  }
  class AudioSampleSource {
    constructor(options: { codec: string; bitrate: number }) {
      captured.audioSources.push(options);
    }
    async add(...args: unknown[]) {
      void args;
    }
    close() {}
  }
  class AudioSample {
    constructor(private _options: unknown) {}
    close() {}
  }
  class VideoSampleSink {
    constructor(private _track: unknown) {}
    async *samples() {
      const fps = captured.sampleFps;
      for (let k = 0; k / fps < 3; k++) {
        yield {
          timestamp: k / fps,
          duration: 1 / fps,
          displayWidth: 1920,
          displayHeight: 1080,
          draw: () => {},
          close: () => {},
        };
      }
    }
  }
  class AudioSampleSink {
    constructor(private _track: unknown) {}
    async *samples() {
      yield* [] as never[];
    }
  }
  class Mp4OutputFormat {
    constructor() {}
  }
  class BufferTarget {
    constructor() {}
  }
  return {
    ALL_FORMATS: {},
    BlobSource,
    Input,
    Output,
    CanvasSource,
    AudioSampleSource,
    AudioSample,
    VideoSampleSink,
    AudioSampleSink,
    Mp4OutputFormat,
    BufferTarget,
    canEncodeAudio: async (codec: string) => codec === "aac",
    canEncodeVideo: async () => true,
  };
});

vi.mock("./render", () => ({
  loadEditorFont: vi.fn(async () => {}),
  drawEditorFrame: vi.fn(),
}));

vi.mock("@/lib/clip-actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/clip-actions")>()),
  downloadBlob: vi.fn(),
}));

import { exportDraft } from "./export-draft";

function assetFor(fps: number): EditorState {
  return {
    status: "ready",
    basis: "test",
    duration: 3,
    width: 720,
    height: 1280,
    fps,
    hasAudio: true,
    waveform: [],
    draft: { revision: 1, document: draft },
    original: draft,
    jobs: [],
  };
}

describe("exportDraft output frame rate pin (F-P2/F-P3)", () => {
  beforeEach(() => {
    captured.audioSources.length = 0;
    captured.videoSources.length = 0;
    captured.videoAdds.length = 0;
    captured.sampleFps = 59.94;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        blob: async () => new Blob(["x"], { type: "video/mp4" }),
      })),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      {} as unknown as CanvasRenderingContext2D,
    );
  });

  it("resolves Shorts to its 8M standard row at a 59.94 fps source, not the 12M high-fps row", async () => {
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      assetFor(59.94),
      "shorts",
      new AbortController().signal,
      () => {},
    );
    expect(captured.videoSources).toHaveLength(1);
    expect(captured.videoSources[0]).toEqual({ codec: "avc", bitrate: 8_000_000 });
  });

  it("emits 90 frames on a 30 fps grid for a 3 s clip from a 59.94 fps source", async () => {
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      assetFor(59.94),
      "tiktok",
      new AbortController().signal,
      () => {},
    );
    expect(captured.videoAdds).toHaveLength(90);
    captured.videoAdds.forEach((frame, k) => {
      expect(frame.timestamp).toBeCloseTo(k / 30, 6);
      expect(frame.duration).toBeCloseTo(1 / 30, 6);
    });
  });

  it("emits 90 frames on a 30 fps grid for Shorts at a 59.94 fps source", async () => {
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      assetFor(59.94),
      "shorts",
      new AbortController().signal,
      () => {},
    );
    expect(captured.videoAdds).toHaveLength(90);
    expect(captured.videoAdds[89].timestamp).toBeCloseTo(89 / 30, 6);
  });

  it("keeps the 30 fps grid and the 8M Shorts rate for a 30 fps source", async () => {
    captured.sampleFps = 30;
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      assetFor(30),
      "shorts",
      new AbortController().signal,
      () => {},
    );
    expect(captured.videoSources[0]).toEqual({ codec: "avc", bitrate: 8_000_000 });
    expect(captured.videoAdds).toHaveLength(90);
    captured.videoAdds.forEach((frame, k) => {
      expect(frame.timestamp).toBeCloseTo(k / 30, 6);
    });
  });

  it("keeps the preset audio rates at a 59.94 fps source (no D-P1 regression)", async () => {
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      assetFor(59.94),
      "shorts",
      new AbortController().signal,
      () => {},
    );
    expect(captured.audioSources).toHaveLength(1);
    expect(captured.audioSources[0]).toEqual({ codec: "aac", bitrate: 384_000 });
  });
});
