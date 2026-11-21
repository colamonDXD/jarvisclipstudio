"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Check, ImagePlus, Loader2, Palette, Trash2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";

interface BrandKit {
  id: string;
  name: string;
  primary_color: string | null;
  accent_color: string | null;
  caption_template: string | null;
  font_name: string | null;
  title_style: string | null;
  music_track: string | null;
  logo_filename: string | null;
  logo_present: boolean;
  logo_position: string;
  logo_opacity: number;
  intro_asset: string | null;
  outro_asset: string | null;
}

interface CaptionTemplateOption {
  id: string;
  name: string;
}

interface MusicTrackOption {
  filename: string;
  display_name: string;
}

interface FontOption {
  name: string;
  display_name: string;
}

const TITLE_STYLES = ["statement", "question", "statistic", "contrast", "story"];
const LOGO_POSITIONS = [
  { value: "bottom_right", label: "Bottom right" },
  { value: "bottom_left", label: "Bottom left" },
  { value: "top_right", label: "Top right" },
  { value: "top_left", label: "Top left" },
];
const NONE = "__none__";

const EMPTY_FORM = {
  name: "",
  primaryColor: "",
  accentColor: "",
  captionTemplate: NONE,
  fontName: NONE,
  titleStyle: NONE,
  musicTrack: NONE,
  logoPosition: "bottom_right",
  logoOpacity: 0.85,
  introAsset: "",
  outroAsset: "",
};

type KitForm = typeof EMPTY_FORM;

function formFromKit(kit: BrandKit): KitForm {
  return {
    name: kit.name,
    primaryColor: kit.primary_color ?? "",
    accentColor: kit.accent_color ?? "",
    captionTemplate: kit.caption_template ?? NONE,
    fontName: kit.font_name ?? NONE,
    titleStyle: kit.title_style ?? NONE,
    musicTrack: kit.music_track ?? NONE,
    logoPosition: kit.logo_position,
    logoOpacity: kit.logo_opacity,
    introAsset: kit.intro_asset ?? "",
    outroAsset: kit.outro_asset ?? "",
  };
}

function payloadFromForm(form: KitForm) {
  const color = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return null;
    return trimmed.startsWith("#") ? trimmed : `#${trimmed}`;
  };
  return {
    name: form.name.trim(),
    primary_color: color(form.primaryColor),
    accent_color: color(form.accentColor),
    caption_template: form.captionTemplate === NONE ? null : form.captionTemplate,
    font_name: form.fontName === NONE ? null : form.fontName,
    title_style: form.titleStyle === NONE ? null : form.titleStyle,
    music_track: form.musicTrack === NONE ? null : form.musicTrack,
    logo_position: form.logoPosition,
    logo_opacity: form.logoOpacity,
    intro_asset: form.introAsset.trim() || null,
    outro_asset: form.outroAsset.trim() || null,
  };
}

async function readError(response: Response, fallback: string): Promise<string> {
  const data = await response.json().catch(() => ({}));
  return typeof data?.detail === "string" ? data.detail : fallback;
}

export default function BrandKitSettingsPage() {
  const [kits, setKits] = useState<BrandKit[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<KitForm>(EMPTY_FORM);
  const [templates, setTemplates] = useState<CaptionTemplateOption[]>([]);
  const [tracks, setTracks] = useState<MusicTrackOption[]>([]);
  const [fonts, setFonts] = useState<FontOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logoInputRef = useRef<HTMLInputElement | null>(null);

  const selectedKit = kits.find((kit) => kit.id === selectedId) ?? null;

  const loadKits = useCallback(async (keepSelection = true) => {
    const response = await fetch("/api/brand-kits", { cache: "no-store" });
    if (!response.ok) throw new Error(await readError(response, "Could not load brand kits"));
    const data = await response.json();
    const list = (data.kits ?? []) as BrandKit[];
    setKits(list);
    if (keepSelection) {
      setSelectedId((current) => {
        const next = list.find((kit) => kit.id === current) ?? list[0] ?? null;
        if (next) setForm(formFromKit(next));
        return next?.id ?? null;
      });
    }
    return list;
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await loadKits(false);
        const [templateData, musicData, fontData] = await Promise.all([
          fetch("/api/caption-templates", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
          fetch("/api/music/library", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
          fetch("/api/fonts", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        ]);
        setTemplates((templateData?.templates ?? []).map((t: CaptionTemplateOption) => ({ id: t.id, name: t.name })));
        setTracks((musicData?.tracks ?? []).map((t: MusicTrackOption) => ({ filename: t.filename, display_name: t.display_name })));
        setFonts((fontData?.fonts ?? []).map((f: FontOption) => ({ name: f.name, display_name: f.display_name })));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load brand kits");
      } finally {
        setLoading(false);
      }
    })();
  }, [loadKits]);

  const startNew = () => {
    setSelectedId(null);
    setForm(EMPTY_FORM);
    setSaved(false);
    setError(null);
  };

  const selectKit = (kit: BrandKit) => {
    setSelectedId(kit.id);
    setForm(formFromKit(kit));
    setSaved(false);
    setError(null);
  };

  async function save() {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const isNew = selectedId === null;
      const response = await fetch(
        isNew ? "/api/brand-kits" : `/api/brand-kits/${selectedId}`,
        {
          method: isNew ? "POST" : "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payloadFromForm(form)),
        },
      );
      if (!response.ok) throw new Error(await readError(response, "Could not save the brand kit"));
      const kit = (await response.json()) as BrandKit;
      await loadKits(false);
      setSelectedId(kit.id);
      setForm(formFromKit({ ...kit, logo_present: kit.logo_present ?? selectedKit?.logo_present ?? false }));
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the brand kit");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!selectedId) return;
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(`/api/brand-kits/${selectedId}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await readError(response, "Could not delete the brand kit"));
      const list = await loadKits(false);
      if (list.length === 0) startNew();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the brand kit");
    } finally {
      setSaving(false);
    }
  }

  async function uploadLogo(file: File) {
    if (!selectedId) return;
    setSaving(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch(`/api/brand-kits/${selectedId}/logo`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) throw new Error(await readError(response, "Could not upload the logo"));
      const kit = (await response.json()) as BrandKit;
      await loadKits(false);
      setForm(formFromKit(kit));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not upload the logo");
    } finally {
      setSaving(false);
    }
  }

  async function removeLogo() {
    if (!selectedId) return;
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(`/api/brand-kits/${selectedId}/logo`, { method: "DELETE" });
      if (!response.ok) throw new Error(await readError(response, "Could not remove the logo"));
      const kit = (await response.json()) as BrandKit;
      await loadKits(false);
      setForm(formFromKit(kit));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the logo");
    } finally {
      setSaving(false);
    }
  }

  const set = (patch: Partial<KitForm>) => {
    setForm((current) => ({ ...current, ...patch }));
    setSaved(false);
  };

  if (loading) {
    return (
      <div className="mx-auto w-full max-w-3xl space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-8 p-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          <Palette className="size-6" /> Brand Kit
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Your own branding for your clips: caption colours and style, a hook-title
          treatment, a default music track and a logo watermark. A kit only changes
          clips when you pick it on the create screen — tasks without a kit render
          exactly as before. The logo and colours also land on each clip&apos;s cover
          image. Intro and outro picks are saved with the kit and stitched onto
          its clips — the intro plays before the clip and the outro after it —
          whenever the named file is in your brand folder.
        </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="size-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {kits.map((kit) => (
          <Button
            key={kit.id}
            type="button"
            variant={kit.id === selectedId ? "default" : "outline"}
            onClick={() => selectKit(kit)}
          >
            {kit.name}
          </Button>
        ))}
        <Button type="button" variant="outline" onClick={startNew}>
          New kit
        </Button>
      </div>

      <div className="space-y-6 rounded-2xl border bg-background p-6">
        <div className="space-y-2">
          <Label htmlFor="kit-name">Kit name</Label>
          <Input
            id="kit-name"
            value={form.name}
            onChange={(event) => set({ name: event.target.value })}
            placeholder="My channel"
          />
        </div>

        <div className="grid gap-6 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="kit-primary">Primary colour (captions + title band)</Label>
            <div className="flex items-center gap-2">
              <input
                type="color"
                aria-label="Primary colour"
                value={/^#[0-9A-Fa-f]{6}$/.test(form.primaryColor) ? form.primaryColor : "#FFFFFF"}
                onChange={(event) => set({ primaryColor: event.target.value.toUpperCase() })}
                className="h-9 w-12 cursor-pointer rounded-md border bg-background"
              />
              <Input
                id="kit-primary"
                value={form.primaryColor}
                onChange={(event) => set({ primaryColor: event.target.value })}
                placeholder="#FFFFFF"
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="kit-accent">Accent colour (highlighted words)</Label>
            <div className="flex items-center gap-2">
              <input
                type="color"
                aria-label="Accent colour"
                value={/^#[0-9A-Fa-f]{6}$/.test(form.accentColor) ? form.accentColor : "#FFE000"}
                onChange={(event) => set({ accentColor: event.target.value.toUpperCase() })}
                className="h-9 w-12 cursor-pointer rounded-md border bg-background"
              />
              <Input
                id="kit-accent"
                value={form.accentColor}
                onChange={(event) => set({ accentColor: event.target.value })}
                placeholder="#FFE000"
              />
            </div>
          </div>
        </div>

        <div className="grid gap-6 sm:grid-cols-2">
          <div className="space-y-2">
            <Label>Caption style</Label>
            <Select value={form.captionTemplate} onValueChange={(value) => set({ captionTemplate: value })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>Style default</SelectItem>
                {templates.map((template) => (
                  <SelectItem key={template.id} value={template.id}>{template.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Caption font</Label>
            <Select value={form.fontName} onValueChange={(value) => set({ fontName: value })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>Template font</SelectItem>
                {fonts.map((font) => (
                  <SelectItem key={font.name} value={font.name}>{font.display_name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Hook title style</Label>
            <Select value={form.titleStyle} onValueChange={(value) => set({ titleStyle: value })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>Style default</SelectItem>
                {TITLE_STYLES.map((style) => (
                  <SelectItem key={style} value={style}>{style}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Default music</Label>
            <Select value={form.musicTrack} onValueChange={(value) => set({ musicTrack: value })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>No kit default</SelectItem>
                {tracks.map((track) => (
                  <SelectItem key={track.filename} value={track.filename}>{track.display_name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-3 rounded-xl border p-4">
          <Label>Logo watermark</Label>
          {selectedKit?.logo_filename ? (
            <p className="text-sm text-muted-foreground">
              {selectedKit.logo_present
                ? `Logo on file: ${selectedKit.logo_filename} — it is drawn on every frame of kit clips and on their covers.`
                : `Logo recorded (${selectedKit.logo_filename}) but the file is missing on this machine, so clips render without it.`}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              {selectedId
                ? "No logo yet. Upload a PNG, JPG or WebP image and it is drawn on every frame of kit clips and on their covers."
                : "Save the kit first, then upload its logo here."}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={logoInputRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void uploadLogo(file);
                event.target.value = "";
              }}
            />
            <Button
              type="button"
              variant="outline"
              disabled={!selectedId || saving}
              onClick={() => logoInputRef.current?.click()}
            >
              <ImagePlus className="size-4" /> Upload logo
            </Button>
            {selectedKit?.logo_filename && (
              <Button type="button" variant="ghost" disabled={saving} onClick={() => void removeLogo()}>
                Remove logo
              </Button>
            )}
          </div>
          <div className="grid gap-6 sm:grid-cols-2">
            <div className="space-y-2">
              <Label>Logo corner</Label>
              <Select value={form.logoPosition} onValueChange={(value) => set({ logoPosition: value })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {LOGO_POSITIONS.map((position) => (
                    <SelectItem key={position.value} value={position.value}>{position.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Logo opacity: {Math.round(form.logoOpacity * 100)}%</Label>
              <Slider
                value={[form.logoOpacity]}
                min={0.1}
                max={1}
                step={0.05}
                onValueChange={(values) => set({ logoOpacity: values[0] ?? 0.85 })}
              />
            </div>
          </div>
        </div>

        <div className="grid gap-6 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="kit-intro">Intro video file</Label>
            <Input
              id="kit-intro"
              value={form.introAsset}
              onChange={(event) => set({ introAsset: event.target.value })}
              placeholder="intro.mp4"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="kit-outro">Outro video file</Label>
            <Input
              id="kit-outro"
              value={form.outroAsset}
              onChange={(event) => set({ outroAsset: event.target.value })}
              placeholder="outro.mp4"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" onClick={() => void save()} disabled={saving || !form.name.trim()}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
            {selectedId ? "Save kit" : "Create kit"}
          </Button>
          {selectedId && (
            <Button type="button" variant="ghost" onClick={() => void remove()} disabled={saving}>
              <Trash2 className="size-4" /> Delete kit
            </Button>
          )}
          {saved && <span className="text-sm text-muted-foreground">Saved.</span>}
        </div>
      </div>
    </div>
  );
}
