"use client";
import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Crosshair, ImagePlus, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  insertOverlay,
  laneLocked,
  overlaysOf,
  placeOverlay,
  timecode,
  type EditDocument,
  type EditorState,
  type OverlayItem,
} from "@/lib/editor/document";
import { cn } from "@/lib/utils";
import { Field, Range, Section } from "./studio-ui";

interface Props {
  doc: EditDocument;
  asset: EditorState;
  endpoint: string;
  /** Playhead on the source timeline; new overlays go here. */
  time: number;
  seek: (time: number) => void;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  selected: string | null;
  select: (id: string | null) => void;
}

const sortByStart = (items: OverlayItem[]) => [...items].sort((a, b) => a.start - b.start);

export function OverlayPanel({ doc, asset, endpoint, time, seek, update, selected, select }: Props) {
  const t = useTranslations("editor");
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const items = sortByStart(overlaysOf(doc));
  const item = items.find((o) => o.id === selected) ?? null;
  // A locked overlays lane keeps its images; every edit control below disables.
  const locked = laneLocked(doc, "overlays");
  const mediaUrl = (assetName: string) => `${endpoint}/media/${assetName}`;

  const edit = (patch: Partial<OverlayItem>, group: string) => {
    if (!item) return;
    update(
      (d) => ({
        ...d,
        overlays: overlaysOf(d).map((o) => (o.id === item.id ? { ...o, ...patch } : o)),
      }),
      group,
    );
  };

  const upload = async (file: File) => {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`${endpoint}/overlays/upload`, { method: "POST", body: form });
      if (!response.ok) throw new Error(`upload failed: ${response.status}`);
      const payload = (await response.json()) as { asset: string; name: string };
      const placed = insertOverlay(doc, payload.asset, payload.name || file.name, time, asset.duration);
      if (!placed) {
        toast.error(t("overlays.uploadFailed"));
        return;
      }
      update(() => placed.doc);
      select(placed.id);
    } catch {
      toast.error(t("overlays.uploadFailed"));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-6">
      <Section title={t("overlays.title")} description={t("overlays.description")}>
        {items.length > 0 ? (
          <ul className="space-y-1" aria-label={t("overlays.listLabel")}>
            {items.map((o) => (
              <li key={o.id}>
                <button
                  type="button"
                  aria-pressed={o.id === selected}
                  onClick={() => {
                    select(o.id);
                    seek(o.start);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md border p-1.5 text-left text-xs transition-colors hover:bg-accent",
                    o.id === selected && "border-primary ring-1 ring-primary",
                  )}
                >
                  <span
                    aria-hidden
                    className="h-9 w-6 shrink-0 rounded-sm bg-muted bg-contain bg-center bg-no-repeat"
                    style={{ backgroundImage: `url(${mediaUrl(o.asset)})` }}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{o.name || t("overlays.untitled")}</span>
                    <span className="block tabular-nums text-muted-foreground">
                      {timecode(o.start)} – {timecode(o.end)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">{t("overlays.empty")}</p>
        )}
      </Section>

      {item && (
        <Section
          title={t("overlays.selectedTitle")}
          action={
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs hover:text-destructive"
              disabled={locked}
              onClick={() => {
                update((d) => ({ ...d, overlays: overlaysOf(d).filter((o) => o.id !== item.id) }));
                select(null);
              }}
            >
              <Trash2 />
              {t("overlays.remove")}
            </Button>
          }
        >
          <div className="flex gap-3">
            <div
              aria-hidden
              className="aspect-[9/16] w-16 shrink-0 rounded-md bg-muted bg-contain bg-center bg-no-repeat"
              style={{ backgroundImage: `url(${mediaUrl(item.asset)})` }}
            />
            <div className="min-w-0 space-y-1 text-xs">
              <p className="truncate font-medium">{item.name || t("overlays.untitled")}</p>
              <p className="tabular-nums text-muted-foreground">
                {timecode(item.start)} – {timecode(item.end)}
              </p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {(["start", "end"] as const).map((edge) => (
              <Field key={edge} label={t(`overlays.${edge}`)}>
                <Input
                  type="number"
                  step="0.01"
                  className="h-8 tabular-nums"
                  aria-label={t(`overlays.${edge}Seconds`)}
                  disabled={locked}
                  value={Number(item[edge].toFixed(2))}
                  onChange={(e) => {
                    const value = Number(e.target.value);
                    if (!Number.isFinite(value)) return;
                    update(
                      (d) =>
                        placeOverlay(
                          d,
                          item.id,
                          edge === "start" ? value : item.start,
                          edge === "end" ? value : item.end,
                          asset.duration,
                        ),
                      "overlay-time",
                    );
                  }}
                />
              </Field>
            ))}
          </div>
          <Range
            label={t("overlays.positionX")}
            value={Math.round(item.x * 100)}
            min={0}
            max={100}
            step={1}
            unit="%"
            disabled={locked}
            onChange={(value) => edit({ x: value / 100 }, "overlay-position")}
          />
          <Range
            label={t("overlays.positionY")}
            value={Math.round(item.y * 100)}
            min={0}
            max={100}
            step={1}
            unit="%"
            disabled={locked}
            onChange={(value) => edit({ y: value / 100 }, "overlay-position")}
          />
          <Range
            label={t("overlays.size")}
            value={Math.round(item.scale * 100)}
            min={5}
            max={100}
            step={1}
            unit="%"
            disabled={locked}
            onChange={(value) => edit({ scale: value / 100 }, "overlay-size")}
          />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => seek(item.start)}>
              <Crosshair />
              {t("overlays.jump")}
            </Button>
          </div>
        </Section>
      )}

      <Section title={t("overlays.addTitle")} description={t("overlays.addDescription")}>
        <input
          ref={fileInput}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) void upload(file);
          }}
        />
        <Button size="sm" variant="outline" disabled={uploading || locked} onClick={() => fileInput.current?.click()}>
          {uploading ? <Loader2 className="animate-spin" /> : <ImagePlus />}
          {uploading ? t("overlays.uploading") : t("overlays.upload")}
        </Button>
      </Section>
    </div>
  );
}
