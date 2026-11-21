"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2, Trash2 } from "lucide-react";

import { nicheLabel } from "@/lib/niches";
import {
  createReferenceStyleFromUpload,
  createReferenceStyleFromUrl,
  deleteReferenceStyle,
  type ReferenceStyleCreated,
  type ReferenceStyleSummary,
} from "@/lib/reference-styles";

interface ReferenceStyleCardProps {
  /** The user's saved styles (owned by the create page so the style
   *  picker above stays in sync). */
  styles: ReferenceStyleSummary[];
  onRefresh: () => Promise<void> | void;
  onSelect: (name: string) => void;
  onDeleted: (name: string) => void;
  disabled?: boolean;
}

function formatPercent(fraction: number | null | undefined): string | null {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) {
    return null;
  }
  return `${Math.round(fraction * 100)}%`;
}

/**
 * The create page's Reference Style card: name a style, give it a
 * finished reference video (upload or link), and the backend analyser
 * measures the video and saves a pickable style. The result summary is
 * the analyser's own honest report — what it set from the video and
 * what it could not measure — never a success facade.
 */
export function ReferenceStyleCard({
  styles,
  onRefresh,
  onSelect,
  onDeleted,
  disabled,
}: ReferenceStyleCardProps) {
  const t = useTranslations("home");
  const [styleName, setStyleName] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ReferenceStyleCreated | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const canCreate =
    !creating &&
    !disabled &&
    styleName.trim().length > 0 &&
    (file !== null || url.trim().length > 0);

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    setCreated(null);
    try {
      const result = file
        ? await createReferenceStyleFromUpload(file, styleName.trim())
        : await createReferenceStyleFromUrl(url.trim(), styleName.trim());
      setCreated(result);
      setFile(null);
      setUrl("");
      await onRefresh();
      onSelect(result.name);
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : t("referenceStyle.createFailed")
      );
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (style: ReferenceStyleSummary) => {
    setDeleting(style.name);
    setError(null);
    try {
      await deleteReferenceStyle(style.name);
      if (created?.name === style.name) setCreated(null);
      await onRefresh();
      onDeleted(style.name);
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : t("referenceStyle.createFailed")
      );
    } finally {
      setDeleting(null);
    }
  };

  const headline = created?.headline ?? {};
  const captionsPercent = formatPercent(headline.caption_frame_fraction);
  const musicBed =
    headline.music_bed_present === true
      ? t("referenceStyle.musicBedYes")
      : headline.music_bed_present === false
        ? t("referenceStyle.musicBedNo")
        : t("referenceStyle.musicBedUnknown");

  return (
    <section
      aria-label={t("referenceStyle.title")}
      className="rounded-2xl border bg-background p-4 shadow-[0_1px_2px_rgb(0_0_0/0.04)]"
    >
      <h2 className="text-sm font-medium">{t("referenceStyle.title")}</h2>
      <p className="mt-1 text-xs text-muted-foreground">{t("referenceStyle.body")}</p>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="reference-style-name" className="text-xs font-medium">
            {t("referenceStyle.nameLabel")}
          </label>
          <input
            id="reference-style-name"
            type="text"
            value={styleName}
            onChange={(event) => setStyleName(event.target.value)}
            placeholder={t("referenceStyle.namePlaceholder")}
            disabled={disabled || creating}
            className="mt-1 h-9 w-full rounded-lg border bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground/80 disabled:opacity-50"
          />
        </div>
        <div>
          <label htmlFor="reference-style-url" className="text-xs font-medium">
            {t("referenceStyle.urlLabel")}
          </label>
          <input
            id="reference-style-url"
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder={t("referenceStyle.urlPlaceholder")}
            disabled={disabled || creating || file !== null}
            className="mt-1 h-9 w-full rounded-lg border bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground/80 disabled:opacity-50"
          />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || creating}
          className="inline-flex h-8 items-center rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
        >
          {t("referenceStyle.uploadButton")}
        </button>
        {file && <span className="text-xs text-muted-foreground">{file.name}</span>}
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => void handleCreate()}
          disabled={!canCreate}
          className="inline-flex h-8 items-center gap-1.5 rounded-full bg-foreground px-3.5 text-xs font-medium text-background transition-opacity hover:opacity-90 disabled:pointer-events-none disabled:opacity-40"
        >
          {creating && <Loader2 className="size-3.5 animate-spin" />}
          {creating ? t("referenceStyle.creating") : t("referenceStyle.create")}
        </button>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="video/*"
        className="hidden"
        aria-hidden="true"
        tabIndex={-1}
        onChange={(event) => {
          const picked = event.target.files?.[0];
          event.target.value = "";
          if (picked) setFile(picked);
        }}
      />

      {error && (
        <p role="alert" className="mt-3 text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {created && (
        <div className="mt-3 rounded-xl bg-muted/60 p-3 text-xs">
          <p className="text-sm font-medium">
            {t("referenceStyle.createdTitle")}: {created.display_name}
          </p>
          {created.base_niche && (
            <p className="mt-0.5 text-muted-foreground">
              {created.base_niche_close_call === true &&
              created.base_niche_runner_up
                ? t("referenceStyle.basedOnCloseCall", {
                    niche: nicheLabel(created.base_niche),
                    runnerUp: nicheLabel(created.base_niche_runner_up),
                  })
                : t("referenceStyle.basedOn", {
                    niche: nicheLabel(created.base_niche),
                  })}
            </p>
          )}
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
            {headline.cuts_per_min != null && (
              <div>
                <dt className="text-muted-foreground">{t("referenceStyle.cutsPerMin")}</dt>
                <dd className="font-medium">{Math.round(headline.cuts_per_min)}</dd>
              </div>
            )}
            {captionsPercent && (
              <div>
                <dt className="text-muted-foreground">{t("referenceStyle.captions")}</dt>
                <dd className="font-medium">{captionsPercent}</dd>
              </div>
            )}
            <div>
              <dt className="text-muted-foreground">{t("referenceStyle.musicBed")}</dt>
              <dd className="font-medium">{musicBed}</dd>
            </div>
            {headline.tempo_bpm != null && (
              <div>
                <dt className="text-muted-foreground">{t("referenceStyle.tempo")}</dt>
                <dd className="font-medium">{Math.round(headline.tempo_bpm)} BPM</dd>
              </div>
            )}
          </dl>
          {created.inferred && Object.keys(created.inferred).length > 0 && (
            <div className="mt-2">
              <p className="font-medium">{t("referenceStyle.inferredTitle")}</p>
              <ul className="mt-0.5 list-inside list-disc text-muted-foreground">
                {Object.entries(created.inferred).map(([field, reason]) => (
                  <li key={field}>
                    {field}: {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {created.not_inferred && created.not_inferred.length > 0 && (
            <div className="mt-2">
              <p className="font-medium">{t("referenceStyle.notInferredTitle")}</p>
              <ul className="mt-0.5 list-inside list-disc text-muted-foreground">
                {created.not_inferred.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {styles.length > 0 && (
        <div className="mt-4 border-t pt-3">
          <p className="text-xs font-medium">{t("referenceStyle.existingTitle")}</p>
          <ul className="mt-1.5 divide-y">
            {styles.map((style) => (
              <li key={style.name} className="flex items-center gap-2 py-1.5">
                <span className="min-w-0 flex-1 truncate text-sm">
                  {style.display_name}
                  {style.base_niche && (
                    <span className="text-xs text-muted-foreground">
                      {" "}
                      · {nicheLabel(style.base_niche)}
                      {style.base_niche_close_call === true && (
                        <> · {t("referenceStyle.closeCallNote")}</>
                      )}
                    </span>
                  )}
                </span>
                <button
                  type="button"
                  aria-label={t("referenceStyle.delete", { name: style.display_name })}
                  onClick={() => void handleDelete(style)}
                  disabled={disabled || deleting === style.name}
                  className="inline-flex size-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
                >
                  {deleting === style.name ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="size-3.5" />
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
