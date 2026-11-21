import { beforeEach, describe, expect, it, vi } from "vitest";
import { draft } from "./document.test-fixture";
import type { EditorState } from "./document";

// D-P1 (SETTINGS_RENDERER_VERIFICATION.md §3.3): the on-device export
// hardcoded the audio bitrate at 192k, so a Shorts export encoded audio
// at TikTok's rate instead of the preset's 384k. These tests drive the
// real exportDraft with mediabunny stubbed and assert the bitrate the
// AudioSampleSource is actually constructed with.

const captured = vi.hoisted(() => ({
  audioSources: [] as Array<{ codec: string; bitrate: number }>,
  videoSources: [] as Array<{ codec: string; bitrate: number }>,
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
    async add(...args: unknown[]) {
      void args;
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
      yield* [] as never[];
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

const asset: EditorState = {
  status: "ready",
  basis: "test",
  duration: 3,
  width: 720,
  height: 1280,
  fps: 30,
  hasAudio: true,
  waveform: [],
  draft: { revision: 1, document: draft },
  original: draft,
  jobs: [],
};

describe("exportDraft audio bitrate (D-P1)", () => {
  beforeEach(() => {
    captured.audioSources.length = 0;
    captured.videoSources.length = 0;
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

  it("encodes Shorts audio at the preset 384k, not the hardcoded 192k", async () => {
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      asset,
      "shorts",
      new AbortController().signal,
      () => {},
    );
    expect(captured.audioSources).toHaveLength(1);
    expect(captured.audioSources[0]).toEqual({ codec: "aac", bitrate: 384_000 });
  });

  it("keeps TikTok audio at 192k", async () => {
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      asset,
      "tiktok",
      new AbortController().signal,
      () => {},
    );
    expect(captured.audioSources).toHaveLength(1);
    expect(captured.audioSources[0]).toEqual({ codec: "aac", bitrate: 192_000 });
  });

  it("keeps Reels audio at 192k", async () => {
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      asset,
      "reels",
      new AbortController().signal,
      () => {},
    );
    expect(captured.audioSources).toHaveLength(1);
    expect(captured.audioSources[0]).toEqual({ codec: "aac", bitrate: 192_000 });
  });

  it("falls back to TikTok's audio rate for an unknown preset", async () => {
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      asset,
      "unknown",
      new AbortController().signal,
      () => {},
    );
    expect(captured.audioSources).toHaveLength(1);
    expect(captured.audioSources[0]).toEqual({ codec: "aac", bitrate: 192_000 });
  });

  it("still gives Shorts its 8M video rate at 30 fps", async () => {
    await exportDraft(
      "https://example.test/media/clean.mp4",
      draft,
      asset,
      "shorts",
      new AbortController().signal,
      () => {},
    );
    expect(captured.videoSources).toHaveLength(1);
    expect(captured.videoSources[0]).toEqual({ codec: "avc", bitrate: 8_000_000 });
  });
});
