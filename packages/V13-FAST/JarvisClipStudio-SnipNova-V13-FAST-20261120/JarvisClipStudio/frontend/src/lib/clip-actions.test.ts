import { describe, expect, it } from "vitest";
import { EXPORT_PRESETS, exportPresetAudioBitrate, exportPresetBitrate } from "./clip-actions";

// The frontend mirror of the backend's preset table (clip_editor.py).
// Shorts follows YouTube's published upload-encoding settings for
// 1080p SDR — 8 Mbps at standard frame rates, 12 Mbps at 48-60 fps —
// so the three presets must never collapse back into one encode (D4).
describe("Export presets", () => {
  it("gives each platform its own rates", () => {
    const byId = Object.fromEntries(EXPORT_PRESETS.map((p) => [p.id, p]));
    expect(byId.tiktok.bitrate).toBe(10_000_000);
    expect(byId.tiktok.audioBitrate).toBe(192_000);
    expect(byId.reels.bitrate).toBe(12_000_000);
    expect(byId.reels.audioBitrate).toBe(192_000);
    expect(byId.shorts.bitrate).toBe(8_000_000);
    expect(byId.shorts.highFpsBitrate).toBe(12_000_000);
    expect(byId.shorts.audioBitrate).toBe(384_000);
  });

  it("picks the frame-rate row for Shorts and one rate for the others", () => {
    expect(exportPresetBitrate("shorts", 24)).toBe(8_000_000);
    expect(exportPresetBitrate("shorts", 30)).toBe(8_000_000);
    expect(exportPresetBitrate("shorts", 60)).toBe(12_000_000);
    expect(exportPresetBitrate("shorts", null)).toBe(8_000_000);
    expect(exportPresetBitrate("tiktok", 60)).toBe(10_000_000);
    expect(exportPresetBitrate("reels", 24)).toBe(12_000_000);
    expect(exportPresetBitrate("unknown", 30)).toBe(10_000_000);
  });

  it("keeps the three presets distinct at 30 fps", () => {
    const rates = EXPORT_PRESETS.map(
      (p) => `${exportPresetBitrate(p.id, 30)}/${p.audioBitrate}`,
    );
    expect(new Set(rates).size).toBe(3);
  });

  // D-P1: the device export must read the preset's audio rate from the
  // same table the server surfaces use — Shorts is 384k, the others 192k.
  it("gives each platform its own audio rate", () => {
    expect(exportPresetAudioBitrate("shorts")).toBe(384_000);
    expect(exportPresetAudioBitrate("tiktok")).toBe(192_000);
    expect(exportPresetAudioBitrate("reels")).toBe(192_000);
    expect(exportPresetAudioBitrate("unknown")).toBe(192_000);
  });
});
