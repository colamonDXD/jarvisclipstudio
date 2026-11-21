import { beforeEach, describe, expect, it, vi } from "vitest";
import { draft } from "./document.test-fixture";
import type { BRollFootage, EditDocument, EditorState } from "./document";

// F-P4/F-P5 (EXPORT_PARITY_MATRIX.md): the server render degrades
// gracefully where the device export fails loudly — missing B-roll
// footage is skipped server-side but throws on device, and an unloadable
// caption font falls back to the bundled fonts dir server-side (libass
// substitutes a face) but throws on device. These tests drive the real
// exportDraft with mediabunny stubbed and synthetic source samples, and
// assert the device export completes with the miss flagged in its
// result instead of failing — the align-to-server fix.

const captured = vi.hoisted(() => ({
  audioSources: [] as Array<{ codec: string; bitrate: number }>,
  videoSources: [] as Array<{ codec: string; bitrate: number }>,
  videoAdds: [] as Array<{ timestamp: number; duration?: number }>,
  fontLoads: [] as string[],
  fontFail: new Set<string>(),
  drawDocs: [] as unknown[],
  fetchMissing: new Set<string>(),
  progressValues: [] as number[],
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
    async getSample() {
      // A decoded B-roll sample at the requested time (the real
      // mediabunny sink returns one; null would mean "no frame here").
      return {
        displayWidth: 1920,
        displayHeight: 1080,
        draw: () => {},
        close: () => {},
      };
    }
    async *samples() {
      for (let k = 0; k / 59.94 < 3; k++) {
        yield {
          timestamp: k / 59.94,
          duration: 1 / 59.94,
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
  loadEditorFont: vi.fn(async (name: string) => {
    captured.fontLoads.push(name);
    if (captured.fontFail.has(name))
      throw new Error(`Failed to load font ${name}`);
  }),
  drawEditorFrame: vi.fn((...args: unknown[]) => {
    captured.drawDocs.push(args[1]);
  }),
}));

vi.mock("@/lib/clip-actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/clip-actions")>()),
  downloadBlob: vi.fn(),
}));

import { exportDraft } from "./export-draft";

const footage: BRollFootage = {
  provider: "pixabay",
  id: 12345,
  width: 1920,
  height: 1080,
  duration: 10,
  fileUrl: "",
  pageUrl: "",
  thumbnail: "",
  credit: "",
};

const docWithBroll: EditDocument = {
  ...draft,
  broll: [
    {
      id: "b1",
      start: 0,
      end: 3,
      offset: 0,
      query: "test",
      footage,
    },
  ],
};

function assetFor(doc: EditDocument): EditorState {
  return {
    status: "ready",
    basis: "test",
    duration: 3,
    width: 720,
    height: 1280,
    fps: 59.94,
    hasAudio: true,
    waveform: [],
    draft: { revision: 1, document: doc },
    original: doc,
    jobs: [],
  };
}

async function runExport(doc: EditDocument, preset = "tiktok") {
  return exportDraft(
    "https://example.test/media/clean.mp4",
    doc,
    assetFor(doc),
    preset,
    new AbortController().signal,
    (value) => captured.progressValues.push(value),
  );
}

// A user-added font name is not one of the bundled font literals, but it
// can still reach a stored document when its file disappears — that is
// exactly the F-P5 case, so the cast stands for the stored value.
const docWithFont = (base: EditDocument, font: string): EditDocument => ({
  ...base,
  captions: {
    ...base.captions,
    font: font as EditDocument["captions"]["font"],
  },
});

describe("exportDraft missing-asset handling (F-P4/F-P5)", () => {
  beforeEach(() => {
    captured.audioSources.length = 0;
    captured.videoSources.length = 0;
    captured.videoAdds.length = 0;
    captured.fontLoads.length = 0;
    captured.fontFail.clear();
    captured.drawDocs.length = 0;
    captured.fetchMissing.clear();
    captured.progressValues.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if ([...captured.fetchMissing].some((m) => url.includes(m)))
          return {
            ok: false,
            status: 404,
            blob: async () => new Blob([]),
          };
        return {
          ok: true,
          blob: async () => new Blob(["x"], { type: "video/mp4" }),
        };
      }),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      {} as unknown as CanvasRenderingContext2D,
    );
  });

  it("F-P4: missing B-roll footage is skipped and the export completes", async () => {
    captured.fetchMissing.add("/broll/footage/");
    const result = await runExport(docWithBroll);
    expect(result.skippedBroll).toEqual(["pixabay:12345/1920x1080"]);
    expect(result.fontFallback).toBeNull();
    // The export ran to completion: 90 frames on the 30 fps grid, progress 100.
    expect(captured.videoAdds).toHaveLength(90);
    expect(captured.progressValues).toContain(100);
  });

  it("F-P4 control: present B-roll footage is not flagged as skipped", async () => {
    const result = await runExport(docWithBroll);
    expect(result.skippedBroll).toEqual([]);
    expect(captured.videoAdds).toHaveLength(90);
    expect(captured.progressValues).toContain(100);
  });

  it("F-P5: an unloadable caption font falls back to the bundled font", async () => {
    captured.fontFail.add("NoSuchFont-Regular");
    const doc = docWithFont(draft, "NoSuchFont-Regular");
    const result = await runExport(doc);
    expect(result.fontFallback).toEqual({
      requested: "NoSuchFont-Regular",
      used: "TikTokSans-Regular",
    });
    expect(result.skippedBroll).toEqual([]);
    // The bundled fallback was loaded, and frames were drawn with it.
    expect(captured.fontLoads).toEqual([
      "NoSuchFont-Regular",
      "TikTokSans-Regular",
    ]);
    expect(
      (captured.drawDocs[0] as EditDocument).captions.font,
    ).toBe("TikTokSans-Regular");
    expect(captured.videoAdds).toHaveLength(90);
    expect(captured.progressValues).toContain(100);
  });

  it("F-P5 control: a loadable caption font is used as-is, with no fallback", async () => {
    const result = await runExport(draft);
    expect(result.fontFallback).toBeNull();
    expect(captured.fontLoads).toEqual(["TikTokSans-Regular"]);
    expect(captured.videoAdds).toHaveLength(90);
  });

  it("F-P5 honest failure: the bundled fallback font being gone still fails", async () => {
    captured.fontFail.add("NoSuchFont-Regular");
    captured.fontFail.add("TikTokSans-Regular");
    const doc = docWithFont(draft, "NoSuchFont-Regular");
    await expect(runExport(doc)).rejects.toThrow();
    expect(captured.progressValues).not.toContain(100);
  });

  it("F-P4 + F-P5 together: both misses flagged, export still completes", async () => {
    captured.fetchMissing.add("/broll/footage/");
    captured.fontFail.add("NoSuchFont-Regular");
    const doc = docWithFont(docWithBroll, "NoSuchFont-Regular");
    const result = await runExport(doc);
    expect(result.skippedBroll).toEqual(["pixabay:12345/1920x1080"]);
    expect(result.fontFallback).toEqual({
      requested: "NoSuchFont-Regular",
      used: "TikTokSans-Regular",
    });
    expect(captured.videoAdds).toHaveLength(90);
    expect(captured.progressValues).toContain(100);
  });

  it("keeps the 30 fps pin and the Shorts audio rate when assets are missing (no F-P2/F-P3/D-P1 regression)", async () => {
    captured.fetchMissing.add("/broll/footage/");
    captured.fontFail.add("NoSuchFont-Regular");
    const doc = docWithFont(docWithBroll, "NoSuchFont-Regular");
    await runExport(doc, "shorts");
    expect(captured.videoSources).toHaveLength(1);
    expect(captured.videoSources[0]).toEqual({ codec: "avc", bitrate: 8_000_000 });
    expect(captured.audioSources).toHaveLength(1);
    expect(captured.audioSources[0]).toEqual({ codec: "aac", bitrate: 384_000 });
    expect(captured.videoAdds).toHaveLength(90);
  });
});
