"use client";

import { useState, useRef, useEffect, useCallback, useId } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  AlertCircle, ArrowRight, ArrowUp, Captions, CaptionsOff, Check, ChevronDown, Crop, Crosshair, FileVideo,
  Gamepad2, Layers, Loader2, Scissors, Timer, Paperclip, Upload, Link2, Wand2, X, Hash, Music, Sparkles, Zap,
} from "lucide-react";
import { CaptionSizeControl } from "@/components/caption-size-control";
import { VideoSourceIcon } from "@/components/video-source-icon";
import { FONT_SEARCH_THRESHOLD, formatUploadLimit, getYouTubeThumbnailUrl, maxVideoUploadBytes, uploadVideoFile, validateVideoSourceUrl } from "@/lib/video-upload";
import { AppShell } from "@/components/app/app-shell";
import { GenerationCard } from "@/components/app/generation-card";
import { VideoDownloadStatus } from "@/components/app/video-download-status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { PaymentFailedBanner } from "@/components/payment-failed-banner";
import { SubscriptionCancelBanner } from "@/components/subscription-cancel-banner";
import { UpgradeDialog, UpgradeNudge, getUpgradeState, upgradeReasonForError, useBillingPlanText, type UpgradeReason } from "@/components/billing/upgrade-prompt";
import { getPublicBillingPlans } from "@/lib/billing-plans";
import { Skeleton } from "@/components/ui/skeleton";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { LayoutPicker, layoutChoiceFrom, type LayoutChoice } from "@/components/layout-picker";
import { Kbd } from "@/components/ui/kbd";
import { useSession } from "@/lib/auth-client";
import { useBillingSummary } from "@/hooks/use-billing-summary";
import { billingPlanMessageKey } from "@/lib/billing-plans";
import { hasPaidAccess } from "@/lib/subscription-status";
import { track } from "@/lib/datafast";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { buildFontOptionsPayload, FONT_TEMPLATE_DEFAULT_VALUE, savedCaptionDefaults } from "@/lib/font-options";
import { fetchGenerations, type GenerationSummary } from "@/lib/generations";
import { CLIP_LENGTHS, isClipLength, type ClipLength } from "@/lib/clip-duration";
import { CLIP_COUNT_OPTIONS } from "@/lib/clip-count";
import { cn } from "@/lib/utils";
import { FontSelectOption, type FontOption } from "@/components/font-select-option";
import { NICHE_CAPTION_PRESETS, NICHE_OPTIONS } from "@/lib/niches";
import {
  CREATION_MODE_OPTIONS,
  MANUAL_CUTTER_MODE,
  parseManualRanges,
  type CreationModeValue,
} from "@/lib/creation-modes";
import {
  STREAMER_JOURNEY_BLURB,
  STREAMER_JOURNEY_PRESET,
  STREAMER_JOURNEY_TITLE,
} from "@/lib/streamer-journey";
import { createSummaryParts } from "@/lib/create-summary";
import { MusicSelect } from "@/components/music-select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ReferenceStyleCard } from "@/components/reference-style-card";
import { ContentBriefPanel } from "@/components/content-brief-panel";
import { CampaignBriefPicker } from "@/components/campaign-brief-picker";
import type { BriefPanelState, ContentBriefPayload } from "@/lib/content-brief";
import type { Campaign } from "@/lib/publishing";
import {
  fetchReferenceStyles,
  type ReferenceStyleSummary,
} from "@/lib/reference-styles";

interface CaptionTemplate {
  id: string;
  name: string;
  description: string;
  animation: string;
  font_family?: string;
  font_size?: number;
  font_color?: string;
}

type OutputFormat = "vertical" | "vertical_fit" | "vertical_pan" | "vertical_split" | "original";

const FRAMING_IDS: OutputFormat[] = ["vertical", "vertical_fit", "vertical_pan", "vertical_split", "original"];

const COLOR_SWATCHES = ["#FFFFFF", "#000000", "#FFD700", "#FF6B6B", "#4ECDC4", "#45B7D1"];

function formatBytes(bytes: number) {
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

// Run Console spec-sheet row names for the F59 summary parts: the
// console grammar calls the niche choice NICHE and the output format
// OUTPUT; the other parts keep their summary names.
const SPEC_ROW_LABELS: Record<string, string> = {
  Style: "Niche",
  Format: "Output",
};

// Round 2 identity: one lucide glyph per creation mode, used by the
// composer hero's segmented control and the explainer tiles below it.
// Zap = the AI does everything; Crosshair = you pick the moments;
// Sparkles = you pick, the AI edits; Scissors = you cut, the AI edits.
const MODE_ICONS: Record<CreationModeValue, typeof Zap> = {
  full_auto: Zap,
  moments_manual: Crosshair,
  moments_ai: Sparkles,
  manual_cutter_ai: Scissors,
};

// Explainer-tile spans in the 3-column bento below the composer:
// wide / narrow / narrow / wide, fixed — the asymmetry is in the
// composition, never in a layout jump when the selection changes.
const MODE_TILE_SPANS: Record<CreationModeValue, string> = {
  full_auto: "sm:col-span-2",
  moments_manual: "",
  moments_ai: "",
  manual_cutter_ai: "sm:col-span-2",
};

export default function HomeApp() {
  const t = useTranslations("home");
  const tCommon = useTranslations("common");
  const [url, setUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const urlInputRef = useRef<HTMLInputElement | null>(null);
  const { data: session, isPending } = useSession();
  const sourceType: "youtube" | "upload" = file ? "upload" : "youtube";

  // Font customization states — null means "use the caption template's own value"
  const [fontFamily, setFontFamily] = useState<string | null>(null);
  const [fontSize, setFontSize] = useState<number | null>(null);
  const [fontColor, setFontColor] = useState<string | null>(null);
  const [availableFonts, setAvailableFonts] = useState<FontOption[]>([]);
  const [fontSearch, setFontSearch] = useState("");
  const [fontLoadError, setFontLoadError] = useState<string | null>(null);
  const [isUploadingFont, setIsUploadingFont] = useState(false);
  const fontUploadInputRef = useRef<HTMLInputElement | null>(null);

  const [captionTemplate, setCaptionTemplate] = useState("default");
  // Whether the user has ever picked a caption style by hand. Until they
  // do, the style follows a manual niche pick (the profile's own preset);
  // afterwards their pick wins and niche changes stop touching it.
  const captionTemplateTouched = useRef(false);
  // Double-submit guard for the create form. Enter, Cmd/Ctrl+Enter and a
  // click on Generate can all fire handleSubmit in the same tick — before
  // the isLoading state has re-rendered the disabled controls — so the
  // guard lives in a ref, not state.
  const submittingRef = useRef(false);
  const [availableTemplates, setAvailableTemplates] = useState<CaptionTemplate[]>([]);
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("vertical");
  const [clipLength, setClipLength] = useState<ClipLength>("auto");
  const [clipLengthSaveError, setClipLengthSaveError] = useState(false);
  const [isClipLengthLoading, setIsClipLengthLoading] = useState(false);
  const clipLengthEdited = useRef(false);
  const clipLengthSave = useRef<Promise<unknown>>(Promise.resolve());
  const [addSubtitles, setAddSubtitles] = useState(true);
  const [cutLongPauses, setCutLongPauses] = useState(true);
  const [pauseThresholdMs, setPauseThresholdMs] = useState("1200");
  const [removeFillerWords, setRemoveFillerWords] = useState(true);
  const [filteredWords, setFilteredWords] = useState("");
  // A niche override is a stock niche name or a user reference style
  // (user-<slug>); the backend validates existence either way.
  const [nicheOverride, setNicheOverride] = useState<string | null>(null);
  const [musicOverride, setMusicOverride] = useState<string | null>(null);
  // Music mode for this generation: "auto" (profile bed, else the Auto
  // bed), "on" (a bed must play: the picked track, else the profile's
  // bed) or "off" (no music at all, whatever the profile carries). Auto
  // is the default and is omitted from the payload, like the other
  // auto-by-omission controls.
  const [musicMode, setMusicMode] = useState<"auto" | "on" | "off">("auto");
  // Template choice for the new task. Plain — just cuts is the default:
  // the clip renders with cuts, silence tightening, captions and the
  // hook title only (no B-roll, music, SFX, cards or effects), framed
  // full-screen. Always sent (unlike music_mode's omit-for-Auto): the
  // backend treats an omitted template_mode as Auto for legacy rows, so
  // omitting the default here would silently invert it.
  const [templateMode, setTemplateMode] = useState<"auto" | "plain">("plain");
  // Framing choice for the new task: how the wide source sits in the
  // vertical clip — "auto" (the layout planner decides per shot), "fit"
  // (the whole picture inside, with blurred bands above and below) or
  // "crop" (the full-screen face-tracked crop). Independent of the
  // template choice, so Plain can pair with Fit. Always sent, like
  // template_mode: on a Plain task an omitted value means Plain's crop
  // default while an explicit "auto" lets the planner decide, so the
  // control must mean the same thing on every template.
  const [framingMode, setFramingMode] = useState<"auto" | "fit" | "crop">("auto");
  // Gaming layout template for streamer clips ("face on top, game
  // below"; backend media/gaming_templates.py, Phase 2). The picker
  // renders only for the Gaming niche; "auto" keeps the automatic
  // reaction layout and sends no fields. Framing wins over the
  // template on the backend (fit short-circuits to letterbox, crop
  // disables the reaction layout), so the picker disables itself
  // unless framing is Auto.
  const [layoutTemplate, setLayoutTemplate] = useState<"auto" | "facecam_red" | "facecam_gold">("auto");
  const [layoutCredit, setLayoutCredit] = useState("");
  // Title on/off: off burns no hook title into the clips.
  const [hookTitleEnabled, setHookTitleEnabled] = useState(true);
  // B-roll switch for the new task: its own On/Off, independent of the
  // other Auto template effects. Defaults On (Auto's behaviour today).
  // In Plain the control is disabled and shows Off — Plain never uses
  // B-roll — and the payload stores an explicit false there, so the
  // switch is honoured exactly as displayed.
  const [brollEnabled, setBrollEnabled] = useState(true);
  // Brand Kit opt-in for this generation; null = no kit (renders as
  // before). The kit only fills what the other controls leave unchosen.
  const [brandKitId, setBrandKitId] = useState<string | null>(null);
  const [brandKits, setBrandKits] = useState<Array<{ id: string; name: string }>>([]);
  // Saved recipe picked for this generation; null = no recipe (the key
  // is omitted from the payload). The backend merges the recipe's
  // choices under the form's explicit ones, so a pick never overrides
  // a control the user set by hand.
  const [recipeId, setRecipeId] = useState<string | null>(null);
  const [recipes, setRecipes] = useState<Array<{ id: string; name: string }>>([]);
  // Clip-count target for this generation only; null = Auto (the backend's
  // duration-based formula decides, and the key is omitted from the payload).
  const [targetClipCount, setTargetClipCount] = useState<number | null>(null);
  // Campaign brief for this generation only; null = no brief (the key is
  // omitted from the payload and the run behaves exactly as before).
  const [contentBrief, setContentBrief] = useState<ContentBriefPayload | null>(null);
  // Campaign picked from the brief library; its saved brief is loaded
  // into the panel (edits affect this generation only) and its id is
  // recorded on the task. Null = no campaign.
  const [briefCampaign, setBriefCampaign] = useState<Campaign | null>(null);
  const [briefLoad, setBriefLoad] = useState<{
    state: BriefPanelState;
    nonce: number;
  } | null>(null);
  const briefLoadNonce = useRef(0);
  const handleBriefCampaignChange = useCallback(
    (campaign: Campaign | null, briefState: BriefPanelState | null) => {
      setBriefCampaign(campaign);
      if (briefState) {
        briefLoadNonce.current += 1;
        setBriefLoad({ state: briefState, nonce: briefLoadNonce.current });
      }
    },
    []
  );
  // Creation mode (tracker 1.9): which of the four workflows runs.
  // Full AutoClip is the historical pipeline, so it is the default.
  const [creationMode, setCreationMode] = useState<CreationModeValue>("full_auto");
  const [manualRangesText, setManualRangesText] = useState("");
  // Streamer Reaction journey: a preset over the controls above, not
  // a mode of its own. Active exactly while the controls still hold
  // the preset's values; touching any control leaves the journey.
  const streamerJourneyActive =
    creationMode === STREAMER_JOURNEY_PRESET.creationMode &&
    templateMode === STREAMER_JOURNEY_PRESET.templateMode &&
    framingMode === STREAMER_JOURNEY_PRESET.framingMode &&
    nicheOverride === STREAMER_JOURNEY_PRESET.nicheOverride &&
    outputFormat === STREAMER_JOURNEY_PRESET.outputFormat;
  const applyStreamerJourney = () => {
    setCreationMode(STREAMER_JOURNEY_PRESET.creationMode);
    setTemplateMode(STREAMER_JOURNEY_PRESET.templateMode);
    setFramingMode(STREAMER_JOURNEY_PRESET.framingMode);
    setNicheOverride(STREAMER_JOURNEY_PRESET.nicheOverride);
    setOutputFormat(STREAMER_JOURNEY_PRESET.outputFormat);
    // The gaming profile's caption preset follows the niche pick,
    // exactly as when the niche is picked by hand — unless the user
    // already chose a caption style themselves.
    if (!captionTemplateTouched.current) {
      setCaptionTemplate(
        NICHE_CAPTION_PRESETS[STREAMER_JOURNEY_PRESET.nicheOverride] ?? "default",
      );
    }
  };
  const [userStyles, setUserStyles] = useState<ReferenceStyleSummary[]>([]);
  const refreshUserStyles = useCallback(async () => {
    try {
      setUserStyles(await fetchReferenceStyles());
    } catch {
      // No styles list (backend down, signed out) — the picker just
      // shows the stock styles, exactly as before this feature.
      setUserStyles([]);
    }
  }, []);
  useEffect(() => {
    void refreshUserStyles();
  }, [refreshUserStyles]);

  const [recent, setRecent] = useState<GenerationSummary[] | null>(null);
  const [billingSummary, updateBillingSummary] = useBillingSummary(Boolean(session?.user?.id));
  const [upgradeReason, setUpgradeReason] = useState<UpgradeReason | null>(null);
  const [lastUpgradeReason, setLastUpgradeReason] = useState<UpgradeReason>({ kind: "start" });
  const openUpgrade = useCallback((reason: UpgradeReason) => {
    setLastUpgradeReason(reason);
    setUpgradeReason(reason);
  }, []);
  const youtubeThumbnailUrl = sourceType === "youtube" ? getYouTubeThumbnailUrl(url) : null;
  // Brand mark of the pasted link; a neutral link icon until it is recognised.
  const urlSource = url.trim() ? validateVideoSourceUrl(url).source : undefined;
  const uploadLimitBytes = billingSummary?.max_upload_bytes && billingSummary.max_upload_bytes > 0
    ? billingSummary.max_upload_bytes
    : maxVideoUploadBytes();

  useEffect(() => {
    let cancelled = false;
    clipLengthEdited.current = false;
    setClipLength("auto");
    if (session?.user?.id) {
      setIsClipLengthLoading(true);
      void fetch("/api/preferences")
        .then((response) => response.ok ? response.json() : null)
        .then((preferences) => {
          if (cancelled) return;
          if (!clipLengthEdited.current && isClipLength(preferences?.clipLength)) {
            setClipLength(preferences.clipLength);
          }
          // Caption defaults from Settings; anything picked here meanwhile wins.
          const captionDefaults = savedCaptionDefaults(preferences);
          setFontFamily((current) => current ?? captionDefaults.fontFamily);
          setFontSize((current) => current ?? captionDefaults.fontSize);
          setFontColor((current) => current ?? captionDefaults.fontColor);
        })
        .catch(() => {})
        .finally(() => { if (!cancelled) setIsClipLengthLoading(false); });
    } else {
      setIsClipLengthLoading(false);
    }
    return () => { cancelled = true; };
  }, [session?.user?.id]);

  function chooseClipLength(value: ClipLength) {
    clipLengthEdited.current = true;
    setClipLength(value);
    setIsClipLengthLoading(false);
    setClipLengthSaveError(false);
    // Serialize changes so a slow previous save cannot overwrite the latest choice.
    clipLengthSave.current = clipLengthSave.current.then(async () => {
      try {
        const response = await fetch("/api/preferences", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ clipLength: value }),
        });
        setClipLengthSaveError(!response.ok);
      } catch {
        setClipLengthSaveError(true);
      }
    });
  }

  const refreshFonts = useCallback(async () => {
    try {
      setFontLoadError(null);
      const response = await fetch("/api/fonts", { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load fonts (${response.status})`);
      const data = await response.json();
      const fonts: FontOption[] = data.fonts || [];
      setAvailableFonts(fonts);

      const styleElement = document.createElement("style");
      styleElement.id = "custom-fonts";
      styleElement.innerHTML = fonts.map((font) => `
        @font-face {
          font-family: '${font.name}';
          src: url('/api/fonts/${font.name}') format('${font.format === "otf" ? "opentype" : "truetype"}');
          font-weight: normal;
          font-style: normal;
        }`).join("\n");
      document.getElementById("custom-fonts")?.remove();
      document.head.appendChild(styleElement);
    } catch (error) {
      console.error("Failed to load fonts:", error);
      setFontLoadError(t("errors.fonts"));
    }
  }, [t]);

  useEffect(() => { void refreshFonts(); }, [refreshFonts]);

  useEffect(() => {
    fetch("/api/caption-templates")
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (data) setAvailableTemplates(data.templates || []); })
      .catch((error) => console.error("Failed to load caption templates:", error));
    // Brand kits are offered only when the user has saved at least one.
    fetch("/api/brand-kits")
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (data) setBrandKits(data.kits || []); })
      .catch(() => setBrandKits([]));
    // Recipes likewise: the picker appears only when one is saved.
    fetch("/api/recipes")
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (data) setRecipes(data.recipes || []); })
      .catch(() => setRecipes([]));
  }, []);

  useEffect(() => {
    if (!session?.user?.id) return;
    fetchGenerations()
      .then((tasks) => setRecent(tasks.slice(0, 8)))
      .catch((error) => { console.error("Failed to load recent generations:", error); setRecent([]); });
  }, [session?.user?.id]);

  const attachFile = (next: File | null) => {
    if (next && !next.type.startsWith("video/") && !/\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(next.name)) {
      setError(t("errors.notVideo"));
      return;
    }
    if (next && next.size > uploadLimitBytes) {
      setError(t("errors.fileTooLarge", {
        size: formatBytes(next.size),
        limit: formatUploadLimit(uploadLimitBytes),
      }));
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    setError(null);
    setFile(next);
    if (!next && fileInputRef.current) fileInputRef.current.value = "";
    if (!next) requestAnimationFrame(() => urlInputRef.current?.focus());
  };

  const handleFontUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const fontFile = event.target.files?.[0];
    event.target.value = "";
    if (!fontFile) return;

    const lower = fontFile.name.toLowerCase();
    if (!lower.endsWith(".ttf") && !lower.endsWith(".otf")) {
      setError(t("errors.fontType"));
      return;
    }

    try {
      setIsUploadingFont(true);
      setError(null);
      const formData = new FormData();
      formData.append("file", fontFile);
      const response = await fetch("/api/fonts/upload", { method: "POST", body: formData });
      if (!response.ok) {
        setError(formatSupportMessage(await parseApiError(response, t("errors.uploadFont"))));
        return;
      }
      const data = await response.json();
      if (data?.font?.name) setFontFamily(data.font.name);
      await refreshFonts();
    } catch (uploadError) {
      console.error("Failed to upload font:", uploadError);
      setError(t("errors.uploadFontRetry"));
    } finally {
      setIsUploadingFont(false);
    }
  };

  const filteredFonts = availableFonts.filter((font) => {
    const keyword = fontSearch.toLowerCase().trim();
    return !keyword || font.display_name.toLowerCase().includes(keyword) || font.name.toLowerCase().includes(keyword);
  });

  const canUploadCustomFonts =
    !billingSummary?.monetization_enabled ||
    hasPaidAccess(billingSummary.plan, billingSummary.subscription_status, billingSummary.subscription_provider);

  // Effective values for the live preview only — the submitted payload keeps nulls.
  const selectedTemplate = availableTemplates.find((template) => template.id === captionTemplate);
  const previewFontFamily = fontFamily ?? selectedTemplate?.font_family ?? "TikTokSans-Regular";
  const previewFontSize = fontSize ?? selectedTemplate?.font_size ?? 24;
  const previewFontColor = fontColor ?? selectedTemplate?.font_color ?? "#FFFFFF";
  const generationRequiresUpgrade = Boolean(billingSummary?.monetization_enabled && !billingSummary.can_create_task);
  const upgradeState = billingSummary?.monetization_enabled ? getUpgradeState(billingSummary) : null;
  // Users can explore every option; the plan prompt only appears when they hit Generate.
  const controlsDisabled = isLoading;
  const hasSource = sourceType === "upload" ? Boolean(file) : Boolean(url.trim());
  const cleanupCount = [cutLongPauses, removeFillerWords, filteredWords.trim().length > 0].filter(Boolean).length;
  const customized = fontFamily !== null || fontSize !== null || fontColor !== null;

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (submittingRef.current) return;
    if (!hasSource || isLoading || isClipLengthLoading || !session?.user?.id) return;
    submittingRef.current = true;
    if (generationRequiresUpgrade) {
      openUpgrade({ kind: upgradeState?.isPaid ? "limit" : "start" });
      submittingRef.current = false;
      return;
    }
    if (sourceType !== "upload") {
      const validation = validateVideoSourceUrl(url);
      if (validation.error) { setError(validation.error); submittingRef.current = false; return; }
    }
    // Manual Cutter contract: ranges are required and must parse
    // before any upload work starts (the backend re-validates them).
    let manualRanges: { start: number; end: number }[] | null = null;
    if (creationMode === MANUAL_CUTTER_MODE) {
      const parsed = parseManualRanges(manualRangesText);
      if (parsed.error || !parsed.ranges) {
        setError(parsed.error ?? "Add at least one time range, one per line.");
        submittingRef.current = false;
        return;
      }
      manualRanges = parsed.ranges;
    }

    setIsLoading(true);
    setError(null);
    setStatusMessage("");

    const fontOptions = buildFontOptionsPayload(fontFamily, fontSize, fontColor);

    try {
      let videoUrl = url.trim();
      const normalizedPauseThreshold = Number.isFinite(Number(pauseThresholdMs))
        ? Math.max(250, Math.min(3000, Math.round(Number(pauseThresholdMs))))
        : 1200;
      const normalizedFilteredWords = filteredWords.split(",").map((word) => word.trim().toLowerCase()).filter(Boolean);

      if (sourceType === "upload" && file) {
        setStatusMessage(t("status.uploading"));
        videoUrl = await uploadVideoFile(file, uploadLimitBytes);
      }

      setStatusMessage(sourceType === "youtube" ? t("status.checkingLength") : t("status.starting"));
      const startResponse = await fetch("/api/tasks/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: { url: videoUrl, title: sourceType === "upload" && file?.name ? file.name.slice(0, 500) : null },
          font_options: fontOptions,
          caption_template: captionTemplate,
          processing_mode: "fast",
          output_format: outputFormat,
          clip_length: clipLength,
          add_subtitles: addSubtitles,
          cut_long_pauses: cutLongPauses,
          pause_threshold_ms: normalizedPauseThreshold,
          remove_filler_words: removeFillerWords,
          filtered_words: normalizedFilteredWords,
          // Never send a blank override: a blank string is "no override"
          // (auto-detect), and the payload is the last place that can be
          // guaranteed regardless of which control last touched the state.
          niche_override: nicheOverride?.trim() ? nicheOverride : null,
          // Omit for Auto so the profile's mood keeps choosing, as before.
          ...(musicOverride ? { music_override: musicOverride } : {}),
          // Omit for Auto so the stored row stays NULL (pre-control rows
          // behave as Auto); On/Off are explicit user instructions.
          ...(musicMode !== "auto" ? { music_mode: musicMode } : {}),
          // Always sent: Plain is the default, and an omitted value
          // means Auto on the backend (legacy rows), so the chosen
          // template must travel explicitly either way.
          template_mode: templateMode,
          // Always sent, like template_mode (see the state note): the
          // explicit framing choice, "auto" included.
          framing_mode: framingMode,
          // Always sent: the "Title on clips" switch (hook titles on
          // unless the user turns them off for this task).
          hook_title_enabled: hookTitleEnabled,
          // Layout picker: a face-on-top layout travels for any niche (it
          // applies when the clip has a facecam); Auto/Full screen/Crop
          // travel as framing_mode only.
          ...(layoutTemplate !== "auto"
            ? {
                layout_template: layoutTemplate,
                ...(layoutCredit.trim() ? { layout_credit: layoutCredit.trim() } : {}),
              }
            : {}),
          // Always sent, like template_mode: the task's own B-roll
          // switch. Plain stores an explicit false (Plain never uses
          // B-roll); in Auto the picked On/Off travels as-is.
          broll_enabled: templateMode === "plain" ? false : brollEnabled,
          // Omit for no kit so the task stores no opt-in, as before.
          ...(brandKitId ? { brand_kit_id: brandKitId } : {}),
          // Omit for no recipe so the task is created from the form
          // alone; a picked recipe's id is all the backend needs (it
          // merges the stored choices under the explicit ones).
          ...(recipeId ? { recipe_id: recipeId } : {}),
          // Omit for Auto so the duration-based formula keeps deciding.
          ...(targetClipCount ? { target_clip_count: targetClipCount } : {}),
          // Omit entirely when the brief panel is empty.
          ...(contentBrief ? { content_brief: contentBrief } : {}),
          // Record the picked campaign; the backend snapshots its brief
          // only when the payload carries no brief of its own.
          ...(briefCampaign ? { campaign_id: briefCampaign.id } : {}),
          creation_mode: creationMode,
          // Ranges travel only with the Manual Cutter workflow; the
          // backend rejects them for every other mode.
          ...(manualRanges ? { manual_ranges: manualRanges } : {}),
        }),
      });

      if (!startResponse.ok) {
        const info = await parseApiError(startResponse, t("errors.api", { status: startResponse.status }));
        const upgradeForError = upgradeReasonForError(info.code, info.detail, upgradeState?.nextPlan?.youtubeMaxMinutes);
        if (upgradeForError) {
          openUpgrade(upgradeForError);
          setIsLoading(false);
          setStatusMessage("");
          submittingRef.current = false;
          return;
        }
        throw new Error(formatSupportMessage(info));
      }

      const startResult = await startResponse.json();
      track("task_created", {
        source_type: sourceType,
        caption_template: captionTemplate,
        output_format: outputFormat,
        add_subtitles: addSubtitles,
        cut_long_pauses: cutLongPauses,
        pause_threshold_ms: normalizedPauseThreshold,
        remove_filler_words: removeFillerWords,
        filtered_words: normalizedFilteredWords,
        processing_mode: "fast",
      });
      window.location.href = `/tasks/${startResult.task_id}`;
    } catch (error) {
      console.error("Error processing video:", error);
      setError(error instanceof Error ? error.message : t("errors.processVideo"));
      setIsLoading(false);
      setStatusMessage("");
      submittingRef.current = false;
    }
  };

  if (isPending || !session?.user) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-2xl space-y-4">
          <Skeleton className="mx-auto h-9 w-64" />
          <Skeleton className="h-36 w-full rounded-2xl" />
        </div>
      </div>
    );
  }

  const limitNote = billingSummary?.max_youtube_duration_seconds
    ? t("limitNote", {
        plan: tCommon(`plans.${billingPlanMessageKey(billingSummary.plan)}`),
        minutes: billingSummary.max_youtube_duration_seconds / 60,
      })
    : null;

  // Pre-processing summary (F59): what this run will use, in plain
  // words, from the form's current values only.
  const summaryParts = createSummaryParts({
    sourceLabel: file ? file.name : url.trim() || null,
    creationModeLabel:
      CREATION_MODE_OPTIONS.find((mode) => mode.value === creationMode)?.label ??
      creationMode,
    templateLabel:
      templateMode === "plain" ? t("templateMode.modePlain") : t("templateMode.modeAuto"),
    framingLabel: {
      auto: t("framingMode.modeAuto"),
      fit: t("framingMode.modeFit"),
      crop: t("framingMode.modeCrop"),
    }[framingMode],
    nicheLabel:
      nicheOverride === null
        ? "Auto-detect"
        : (NICHE_OPTIONS.find((niche) => niche.value === nicheOverride)?.label ??
          userStyles.find((style) => style.name === nicheOverride)?.display_name ??
          nicheOverride),
    outputFormatLabel: t(`framingOptions.${outputFormat}.label`),
  });

  return (
    <AppShell>
      <main className="mx-auto w-full max-w-[1440px] px-4 pb-24 pt-6 sm:px-6 lg:px-10">
        {billingSummary?.cancel_at && billingSummary.subscription_provider === "stripe" && (
          <SubscriptionCancelBanner
            cancelAt={billingSummary.cancel_at}
            onRestarted={() => updateBillingSummary({ cancel_at: null })}
            className="mb-8"
          />
        )}
        {upgradeState?.needsPayment && <PaymentFailedBanner className="mb-8" />}

        <div className="animate-rise">
          <VideoDownloadStatus />

          {/* HEADLINE — Round 2: the page opens on what it does, in
              display type, not on a form column. The second line
              carries the brand gradient (the logo's own two hues). */}
          <div className="mt-8 max-w-2xl">
            <h1 className="font-display text-4xl font-bold leading-[1.04] tracking-tight sm:text-5xl">
              One long video in.
              <span className="rb-gradient-text block">Clips worth posting out.</span>
            </h1>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              Paste a link or drop a file, pick how much the AI does, and hit generate — the fine-tuning lives below.
            </p>
          </div>

          <form
            onSubmit={handleSubmit}
            onDragOver={(event) => { event.preventDefault(); if (!controlsDisabled) setIsDragging(true); }}
            onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setIsDragging(false); }}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragging(false);
              if (!controlsDisabled) attachFile(event.dataTransfer.files?.[0] ?? null);
            }}
            className="relative mt-8"
          >
            <input
              id="video-upload"
              type="file"
              accept="video/*"
              ref={fileInputRef}
              aria-describedby="source-limits"
              onChange={(event) => attachFile(event.target.files?.[0] ?? null)}
              disabled={controlsDisabled}
              className="hidden"
            />

            {/* COMPOSER HERO — Round 2: the page's one job as a single
                glass surface (Raycast-style): the source, the mode
                switcher and the Generate action live in one card, lit
                by the brand glow behind it. Everything below the hero
                is fine-tuning. */}
            <section className="relative">
              <div aria-hidden className="hero-glow pointer-events-none absolute -inset-x-4 -bottom-6 top-2 rounded-full" />
              <div className="glass-hero relative rounded-3xl p-5 sm:p-6">
              {isDragging && (
                <div className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 rounded-3xl bg-background/95 text-sm font-medium">
                  <Upload className="size-6 text-brand" />{t("drop")}
                </div>
              )}
              <div className="flex items-center gap-2">
                <Link2 className="size-4 shrink-0 text-brand" aria-hidden />
                <SectionEyebrow>Source</SectionEyebrow>
              </div>

              <div className="mt-4 flex items-start gap-3 sm:gap-4">
                {file ? (
                  <div className="flex min-w-0 flex-1 items-center gap-3 rounded-2xl bg-muted/60 p-2.5 pr-3">
                    <span className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-foreground text-background"><FileVideo className="size-5" /></span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{file.name}</p>
                      <p className="text-xs text-muted-foreground">{t("readyToUpload", { size: formatBytes(file.size) })}</p>
                    </div>
                    <button type="button" onClick={() => attachFile(null)} disabled={isLoading} aria-label={t("removeFile")} className="rounded-md p-1 text-muted-foreground hover:bg-background hover:text-foreground">
                      <X className="size-4" />
                    </button>
                  </div>
                ) : (
                  <>
                    {youtubeThumbnailUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={youtubeThumbnailUrl} alt="" className="mt-0.5 hidden h-12 w-[84px] shrink-0 rounded-lg object-cover sm:block" />
                    ) : (
                      <span className="mt-0.5 flex size-12 shrink-0 items-center justify-center rounded-xl bg-muted text-muted-foreground">{urlSource ? <VideoSourceIcon source={urlSource} className="size-5" aria-hidden /> : <Link2 className="size-5" aria-hidden />}</span>
                    )}
                    <div className="min-w-0 flex-1">
                      <label htmlFor="youtube-url" className="sr-only">{t("youtubeUrl")}</label>
                      <input
                        id="youtube-url"
                        ref={urlInputRef}
                        type="url"
                        autoFocus
                        autoComplete="off"
                        placeholder={t("urlPlaceholder")}
                        aria-describedby="source-limits"
                        value={url}
                        onChange={(e) => setUrl(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void handleSubmit(); } }}
                        disabled={controlsDisabled}
                        className="h-12 w-full bg-transparent text-lg outline-none placeholder:text-muted-foreground/80 disabled:cursor-not-allowed"
                      />
                    </div>
                  </>
                )}
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                {!file && (
                  <Chip onClick={() => fileInputRef.current?.click()} disabled={controlsDisabled} aria-label={t("uploadFile")} aria-describedby="source-limits">
                    <Paperclip className="size-3.5" /><span className="hidden sm:inline">{t("upload")}</span>
                  </Chip>
                )}
                <span className="ml-auto hidden text-xs text-muted-foreground sm:inline-flex sm:items-center sm:gap-1">
                  {isLoading ? <span role="status">{statusMessage || t("status.working")}</span> : hasSource ? <><Kbd>⌘</Kbd><Kbd>↵</Kbd></> : null}
                </span>
              </div>
              {/* MODE + ACTION — the creation-mode switcher as a
                  segmented control and the one Generate action, both
                  inside the composer. The selected chip carries the
                  brand gradient so the choice reads instantly. */}
              <div className="mt-6 border-t border-foreground/[0.07] pt-5">
                <div role="radiogroup" aria-label="Creation mode" className="flex flex-wrap gap-2">
                  {CREATION_MODE_OPTIONS.map((mode) => {
                    const selected = creationMode === mode.value;
                    const ModeIcon = MODE_ICONS[mode.value];
                    return (
                      <button
                        key={mode.value}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        disabled={controlsDisabled}
                        onClick={() => setCreationMode(mode.value)}
                        className={cn(
                          "inline-flex h-10 items-center gap-2 rounded-full px-3.5 text-sm font-medium transition-colors disabled:opacity-50",
                          selected
                            ? "brand-gradient text-white shadow-[0_10px_28px_-10px_var(--brand)]"
                            : "border border-foreground/15 text-muted-foreground hover:bg-accent hover:text-foreground",
                        )}
                      >
                        <ModeIcon className="size-4 shrink-0" aria-hidden />
                        {mode.label}
                      </button>
                    );
                  })}
                </div>

                <div className="mt-5 flex flex-wrap items-center gap-3">
                  <span className="hidden text-xs text-muted-foreground sm:inline">
                    Drop a video file anywhere to attach it.
                  </span>
                  <Button
                    type="submit"
                    aria-label={t("generate")}
                    disabled={!hasSource || isLoading || isClipLengthLoading}
                    className={cn(
                      "ml-auto h-12 rounded-full px-7 text-base font-semibold disabled:opacity-100",
                      !hasSource || isLoading || isClipLengthLoading
                        ? "bg-muted text-muted-foreground shadow-sm"
                        : "brand-gradient text-white shadow-[0_16px_36px_-12px_var(--brand)] hover:brightness-110",
                    )}
                  >
                    {isLoading ? <Loader2 className="size-5 animate-spin" /> : <ArrowUp className="size-5" />}
                    {t("generate")}
                  </Button>
                </div>

                {creationMode === MANUAL_CUTTER_MODE && (
                  <div className="mt-5 border-t border-foreground/[0.07] pt-5">
                    <div className="space-y-1.5">
                      <label htmlFor="manual-ranges" className="text-xs font-medium text-muted-foreground">
                        Your time ranges — one per line
                      </label>
                      <Textarea
                        id="manual-ranges"
                        value={manualRangesText}
                        onChange={(e) => setManualRangesText(e.target.value)}
                        disabled={controlsDisabled}
                        rows={4}
                        placeholder={"1:23-1:52\n4:05-4:40"}
                        className="font-mono text-sm"
                      />
                      <p className="text-xs text-muted-foreground">
                        Write start-end per line, like 1:23-1:52. Up to 20 ranges; the AI edits exactly these and never hunts for other moments.
                      </p>
                    </div>
                  </div>
                )}
              </div>
              </div>
            </section>

            <div className="mt-5 lg:grid lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-5">
            <div className="grid min-w-0 content-start gap-5">
            {/* CREATION MODE — the four workflows explained as
                icon-led tiles in a varied-span bento (2/1/1/2). The
                tiles drive the same state as the composer's chips;
                the selected tile gets the gradient medallion. The
                Streamer Reaction journey gets its own section below:
                it is a preset over the same controls, not a fifth
                mode. */}
            <section>
              <SectionEyebrow>Creation mode</SectionEyebrow>
              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                {CREATION_MODE_OPTIONS.map((mode) => {
                  const selected = creationMode === mode.value;
                  const ModeIcon = MODE_ICONS[mode.value];
                  return (
                    <button
                      key={mode.value}
                      type="button"
                      aria-pressed={selected}
                      disabled={controlsDisabled}
                      onClick={() => setCreationMode(mode.value)}
                      className={cn(
                        "glass-tile rounded-2xl p-5 text-left disabled:opacity-50",
                        MODE_TILE_SPANS[mode.value],
                        selected ? "glass-tile--selected" : "glass-tile--hover",
                      )}
                    >
                      <span className="flex items-center gap-3">
                        <span
                          className={cn(
                            "flex size-9 shrink-0 items-center justify-center rounded-xl",
                            selected ? "brand-gradient text-white" : "bg-muted text-muted-foreground",
                          )}
                        >
                          <ModeIcon className="size-[18px]" aria-hidden />
                        </span>
                        <span className="font-display text-[15px] font-semibold">{mode.label}</span>
                        {selected && (
                          <span className="ml-auto flex size-5 shrink-0 items-center justify-center rounded-full bg-brand text-brand-foreground">
                            <Check className="size-3" />
                          </span>
                        )}
                      </span>
                      <span className="mt-2.5 block text-sm leading-relaxed text-muted-foreground">{mode.blurb}</span>
                    </button>
                  );
                })}
              </div>
            </section>

            {/* STREAMER REACTION — the journey entry: one click sets
                the creation mode, template, framing, niche and format
                to the streamer-VOD combination (see
                lib/streamer-journey). A preset, not a fifth mode. */}
            <section>
              <SectionEyebrow>Streamer reaction</SectionEyebrow>
              <button
                type="button"
                onClick={applyStreamerJourney}
                disabled={controlsDisabled}
                aria-pressed={streamerJourneyActive}
                className={cn(
                  "glass-tile mt-3 w-full rounded-2xl p-5 text-left disabled:opacity-50",
                  streamerJourneyActive ? "glass-tile--selected" : "glass-tile--featured glass-tile--hover",
                )}
              >
                <span className="flex items-start gap-3.5">
                  <span className="brand-gradient flex size-10 shrink-0 items-center justify-center rounded-xl text-white">
                    <Gamepad2 className="size-5" aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-3">
                      <span className="font-display text-[15px] font-semibold">{STREAMER_JOURNEY_TITLE}</span>
                      {streamerJourneyActive && (
                        <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-brand text-brand-foreground">
                          <Check className="size-3" />
                        </span>
                      )}
                    </span>
                    <span className="mt-1.5 block text-sm leading-relaxed text-muted-foreground">
                      {STREAMER_JOURNEY_BLURB}
                    </span>
                  </span>
                </span>
              </button>
            </section>

            {/* TEMPLATE — its own section: Auto's full edit, or
                Plain's cuts-only render. */}
            <section className="glass-tile rounded-2xl p-5">
              <div className="flex w-full flex-wrap items-center justify-between gap-4">
                <span className="flex items-center gap-2">
                  <Layers className="size-4 shrink-0 text-brand" aria-hidden />
                  <SectionEyebrow>{t("templateMode.modeLabel")}</SectionEyebrow>
                </span>
                <ToggleGroup
                  type="single"
                  variant="outline"
                  size="default"
                  aria-label={t("templateMode.modeLabel")}
                  value={templateMode}
                  disabled={controlsDisabled}
                  onValueChange={(value) => {
                    if (value === "auto" || value === "plain") setTemplateMode(value);
                  }}
                >
                  <ToggleGroupItem value="auto" aria-label={t("templateMode.modeAuto")} className="px-4">{t("templateMode.modeAuto")}</ToggleGroupItem>
                  <ToggleGroupItem value="plain" aria-label={t("templateMode.modePlain")} className="px-4">{t("templateMode.modePlain")}</ToggleGroupItem>
                </ToggleGroup>
              </div>
            </section>

            {/* OPTIONS — everything else, grouped into collapsible
                glass tiles in the bento grid (mono eyebrow header).
                Panel content stays mounted when collapsed. */}
            <div className="grid items-start gap-5 md:grid-cols-6">
              <OptionPanel title="Framing & format" icon={Crop} className="md:col-span-3">
                <OptionRow label="Output format">
                  <Popover>
                    <PopoverTrigger asChild>
                      <Chip disabled={controlsDisabled}>
                        <Crop className="size-3.5" />{t(`framingOptions.${outputFormat}.label`)}<ChevronDown className="size-3 opacity-60" />
                      </Chip>
                    </PopoverTrigger>
                    <PopoverContent align="end" className="w-[min(92vw,420px)] rounded-2xl p-3">
                      <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">{t("framing")}</p>
                      <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label={t("framing")}>
                        {FRAMING_IDS.map((id) => (
                          <button
                            key={id}
                            type="button"
                            role="radio"
                            aria-checked={outputFormat === id}
                            onClick={() => setOutputFormat(id)}
                            className={cn("rounded-xl border p-3 text-left transition-colors hover:bg-accent", outputFormat === id && "border-foreground ring-1 ring-foreground")}
                          >
                            <FramingGlyph kind={id} />
                            <span className="mt-2 block text-sm font-medium">{t(`framingOptions.${id}.label`)}</span>
                            <span className="block text-xs text-muted-foreground">{t(`framingOptions.${id}.hint`)}</span>
                          </button>
                        ))}
                      </div>
                    </PopoverContent>
                  </Popover>
                </OptionRow>

                <div className="space-y-2 py-2">
                  <p className="text-sm font-medium">Layout</p>
                  <LayoutPicker
                    value={layoutChoiceFrom(framingMode, layoutTemplate)}
                    disabled={controlsDisabled}
                    onChange={(choice: LayoutChoice) => {
                      if (choice === "facecam_red" || choice === "facecam_gold") {
                        setLayoutTemplate(choice);
                        setFramingMode("auto");
                      } else {
                        setLayoutTemplate("auto");
                        setFramingMode(choice);
                      }
                    }}
                  />
                  {layoutTemplate !== "auto" && (
                    <Input
                      value={layoutCredit}
                      onChange={(event) => setLayoutCredit(event.target.value)}
                      placeholder="Credit line (optional), e.g. @iShowSpeed"
                      aria-label="Credit, e.g. @iShowSpeed"
                      maxLength={40}
                      disabled={controlsDisabled}
                      className="h-8 w-full max-w-sm text-xs"
                    />
                  )}
                </div>

                <OptionRow label="Title on clips">
                  <Switch
                    id="hook-title-enabled"
                    aria-label="Title on clips"
                    checked={hookTitleEnabled}
                    onCheckedChange={setHookTitleEnabled}
                    disabled={controlsDisabled}
                  />
                </OptionRow>

                <OptionRow label={t("clipLength.title")}>
                  <Popover>
                    <PopoverTrigger asChild>
                      <Chip disabled={controlsDisabled} active={clipLength !== "auto"} aria-label={t("clipLength.picker")}>
                        <Timer className="size-3.5" />{t(`clipLength.${clipLength}`)}<ChevronDown className="size-3 opacity-60" />
                      </Chip>
                    </PopoverTrigger>
                    <PopoverContent align="end" className="w-72 rounded-2xl p-3">
                      <p className="mb-2 text-sm font-medium">{t("clipLength.title")}</p>
                      <div role="radiogroup" aria-label={t("clipLength.title")} className="grid gap-1">
                        {CLIP_LENGTHS.map((length) => (
                          <button type="button" role="radio" aria-checked={clipLength === length} disabled={controlsDisabled} key={length} onClick={() => chooseClipLength(length)} className={cn("flex items-center justify-between rounded-lg px-3 py-2 text-sm hover:bg-muted disabled:opacity-50", clipLength === length && "bg-muted")}>
                            <span>{t(`clipLength.${length}`)}</span>
                            {clipLength === length && <Check className="size-4" />}
                          </button>
                        ))}
                      </div>
                      <p className="mt-2 text-xs text-muted-foreground">{t("clipLength.hint")}</p>
                      {clipLengthSaveError && <p role="status" className="mt-2 text-xs text-destructive">{t("clipLength.saveError")}</p>}
                    </PopoverContent>
                  </Popover>
                </OptionRow>

                <OptionRow label={t("clipCount.title")}>
                  <Popover>
                    <PopoverTrigger asChild>
                      <Chip disabled={controlsDisabled} active={targetClipCount !== null} aria-label={t("clipCount.picker")}>
                        <Hash className="size-3.5" />{targetClipCount === null ? t("clipCount.auto") : t("clipCount.option", { count: targetClipCount })}<ChevronDown className="size-3 opacity-60" />
                      </Chip>
                    </PopoverTrigger>
                    <PopoverContent align="end" className="w-72 rounded-2xl p-3">
                      <p className="mb-2 text-sm font-medium">{t("clipCount.title")}</p>
                      <div role="radiogroup" aria-label={t("clipCount.title")} className="grid gap-1">
                        <button type="button" role="radio" aria-checked={targetClipCount === null} disabled={controlsDisabled} onClick={() => setTargetClipCount(null)} className={cn("flex items-center justify-between rounded-lg px-3 py-2 text-sm hover:bg-muted disabled:opacity-50", targetClipCount === null && "bg-muted")}>
                          <span>{t("clipCount.auto")}</span>
                          {targetClipCount === null && <Check className="size-4" />}
                        </button>
                        {CLIP_COUNT_OPTIONS.map((count) => (
                          <button type="button" role="radio" aria-checked={targetClipCount === count} disabled={controlsDisabled} key={count} onClick={() => setTargetClipCount(count)} className={cn("flex items-center justify-between rounded-lg px-3 py-2 text-sm hover:bg-muted disabled:opacity-50", targetClipCount === count && "bg-muted")}>
                            <span>{t("clipCount.option", { count })}</span>
                            {targetClipCount === count && <Check className="size-4" />}
                          </button>
                        ))}
                      </div>
                      <p className="mt-2 text-xs text-muted-foreground">{t("clipCount.hint")}</p>
                    </PopoverContent>
                  </Popover>
                </OptionRow>
              </OptionPanel>

              <OptionPanel title={t("captions")} icon={Captions} className="md:col-span-3">
                <OptionRow label={t("captionStyle")}>
                  <Popover>
                    <PopoverTrigger asChild>
                      <Chip disabled={controlsDisabled} active={customized || !addSubtitles}>
                        {addSubtitles ? <Captions className="size-3.5" /> : <CaptionsOff className="size-3.5" />}
                        {addSubtitles ? selectedTemplate?.name || t("defaultTemplate") : t("noCaptions")}<ChevronDown className="size-3 opacity-60" />
                      </Chip>
                    </PopoverTrigger>
                    <PopoverContent align="end" className="w-[min(92vw,560px)] rounded-2xl p-0">
                      <div className="grid gap-0 sm:grid-cols-[minmax(0,1fr)_180px]">
                        <div className="max-h-[420px] min-w-0 space-y-4 overflow-y-auto p-4">
                          <div className="flex items-center justify-between gap-4 border-b pb-4">
                            <label htmlFor="add-captions" className="text-sm font-medium">{t("captions")}</label>
                            <Switch id="add-captions" checked={addSubtitles} onCheckedChange={setAddSubtitles} disabled={controlsDisabled} />
                          </div>
                          <div className={cn(!addSubtitles && "pointer-events-none opacity-50")} inert={!addSubtitles}>
                            <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">{t("captionStyle")}</p>
                            <div className="flex flex-col gap-1" role="radiogroup" aria-label={t("captionStyle")}>
                              {(availableTemplates.length ? availableTemplates : [{ id: "default", name: t("defaultTemplate"), description: t("defaultTemplateDescription"), animation: "" } as CaptionTemplate]).map((template) => (
                                <button
                                  key={template.id}
                                  type="button"
                                  role="radio"
                                  aria-checked={captionTemplate === template.id}
                                  onClick={() => { captionTemplateTouched.current = true; setCaptionTemplate(template.id); }}
                                  className={cn("flex w-full min-w-0 items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-accent", captionTemplate === template.id && "bg-accent")}
                                >
                                  <span className="size-3 shrink-0 rounded-full border" style={{ backgroundColor: template.font_color || "#fff" }} />
                                  <span className="min-w-0 flex-1">
                                    <span className="block text-sm font-medium">{template.name}</span>
                                    <span className="block truncate text-xs text-muted-foreground">{template.description}</span>
                                  </span>
                                  {captionTemplate === template.id && <Check className="size-4" />}
                                </button>
                              ))}
                            </div>
                          </div>

                          <div className={cn("space-y-3 border-t pt-4", !addSubtitles && "pointer-events-none opacity-50")} inert={!addSubtitles}>
                            <div className="flex items-center justify-between">
                              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{t("customize")}</p>
                              {customized && (
                                <button type="button" className="text-xs text-muted-foreground underline-offset-2 hover:underline" onClick={() => { setFontFamily(null); setFontSize(null); setFontColor(null); }}>
                                  {t("resetTemplate")}
                                </button>
                              )}
                            </div>
                            <div className="space-y-2">
                              <div className="flex items-center justify-between gap-2">
                                <label className="text-sm">{t("font")}</label>
                                <input ref={fontUploadInputRef} type="file" accept=".ttf,.otf" onChange={handleFontUpload} className="hidden" />
                                <Button type="button" variant="ghost" size="sm" className="h-7 text-xs" disabled={controlsDisabled || isUploadingFont} onClick={() => canUploadCustomFonts ? fontUploadInputRef.current?.click() : openUpgrade({ kind: "custom_fonts" })}>
                                  <Upload className="size-3" />{isUploadingFont ? t("status.uploadingFont") : t("uploadFont")}
                                </Button>
                              </div>
                              {!canUploadCustomFonts && (
                                <button type="button" onClick={() => openUpgrade({ kind: "custom_fonts" })} className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground">
                                  {t("customFontsPaid")}
                                </button>
                              )}
                              {availableFonts.length > FONT_SEARCH_THRESHOLD && (
                                <Input value={fontSearch} onChange={(e) => setFontSearch(e.target.value)} placeholder={t("searchFonts")} className="h-8" />
                              )}
                              <Select value={fontFamily ?? FONT_TEMPLATE_DEFAULT_VALUE} onValueChange={(value) => setFontFamily(value === FONT_TEMPLATE_DEFAULT_VALUE ? null : value)}>
                                <SelectTrigger className="w-full"><SelectValue placeholder={t("templateDefault")} /></SelectTrigger>
                                <SelectContent>
                                  <SelectItem value={FONT_TEMPLATE_DEFAULT_VALUE}>{t("templateDefault")}</SelectItem>
                                  {filteredFonts.map((font) => (
                                    <FontSelectOption key={font.name} font={font} />
                                  ))}
                                  {availableFonts.length > 0 && filteredFonts.length === 0 && <SelectItem value="__no_match__" disabled>{t("noFontMatch")}</SelectItem>}
                                </SelectContent>
                              </Select>
                              {fontLoadError && <p className="text-xs text-amber-700 dark:text-amber-400">{fontLoadError}</p>}
                            </div>
                            <CaptionSizeControl value={fontSize} onChange={setFontSize} disabled={controlsDisabled} />
                            <div className="space-y-2">
                              <p className="text-sm">{t("color")}</p>
                              <div className="flex flex-wrap items-center gap-1.5">
                                <button type="button" onClick={() => setFontColor(null)} className={cn("h-7 rounded-full border px-2.5 text-xs", fontColor === null ? "border-foreground bg-foreground text-background" : "hover:bg-accent")}>{t("template")}</button>
                                {COLOR_SWATCHES.map((color) => (
                                  <button key={color} type="button" onClick={() => setFontColor(color)} title={color} aria-label={t("captionColor", { color })}
                                    className={cn("size-7 rounded-full border-2 transition-transform hover:scale-110", fontColor === color ? "border-foreground" : "border-border")}
                                    style={{ backgroundColor: color }} />
                                ))}
                                <label className="relative size-7 cursor-pointer overflow-hidden rounded-full border-2 border-dashed border-border" title={t("customColor")}>
                                  <input type="color" value={fontColor ?? "#FFFFFF"} onChange={(e) => setFontColor(e.target.value)} className="absolute inset-0 cursor-pointer opacity-0" aria-label={t("customCaptionColor")} />
                                  <span className="absolute inset-1 rounded-full bg-[conic-gradient(red,yellow,lime,cyan,blue,magenta,red)]" />
                                </label>
                              </div>
                            </div>
                          </div>
                        </div>

                        <div className="hidden border-l bg-muted/40 p-4 sm:block">
                          <CaptionPreview
                            thumbnail={youtubeThumbnailUrl}
                            fontFamily={previewFontFamily}
                            fontSize={previewFontSize}
                            color={previewFontColor}
                          />
                          <p className="mt-3 text-center text-[11px] text-muted-foreground">{t("livePreview")}</p>
                        </div>
                      </div>
                    </PopoverContent>
                  </Popover>
                </OptionRow>
              </OptionPanel>

              <OptionPanel title="B-roll & music" icon={Music} className="md:col-span-2">
                <OptionRow label={t("broll")}>
                  <ToggleGroup
                    type="single"
                    variant="outline"
                    size="sm"
                    aria-label={t("brollMode.modeLabel")}
                    value={templateMode === "plain" ? "off" : brollEnabled ? "on" : "off"}
                    disabled={controlsDisabled || templateMode === "plain"}
                    title={templateMode === "plain" ? t("brollMode.plainHint") : undefined}
                    onValueChange={(value) => {
                      if (value === "on") setBrollEnabled(true);
                      else if (value === "off") setBrollEnabled(false);
                    }}
                  >
                    <ToggleGroupItem value="on" aria-label={`${t("broll")} ${t("brollMode.modeOn")}`} title={templateMode === "plain" ? t("brollMode.plainHint") : undefined}>{t("brollMode.modeOn")}</ToggleGroupItem>
                    <ToggleGroupItem value="off" aria-label={`${t("broll")} ${t("brollMode.modeOff")}`} title={templateMode === "plain" ? t("brollMode.plainHint") : undefined}>{t("brollMode.modeOff")}</ToggleGroupItem>
                  </ToggleGroup>
                </OptionRow>

                <OptionRow label={t("music.modeLabel")}>
                  <ToggleGroup
                    type="single"
                    variant="outline"
                    size="sm"
                    aria-label={t("music.modeLabel")}
                    value={musicMode}
                    disabled={controlsDisabled || templateMode === "plain"}
                    onValueChange={(value) => {
                      if (value === "auto" || value === "on" || value === "off") setMusicMode(value);
                    }}
                  >
                    <ToggleGroupItem value="auto" aria-label={t("music.modeAuto")}>{t("music.modeAuto")}</ToggleGroupItem>
                    <ToggleGroupItem value="on" aria-label={t("music.modeOn")}>{t("music.modeOn")}</ToggleGroupItem>
                    <ToggleGroupItem value="off" aria-label={t("music.modeOff")}>{t("music.modeOff")}</ToggleGroupItem>
                  </ToggleGroup>
                </OptionRow>

                <OptionRow label="Music track">
                  <MusicSelect value={musicOverride} onChange={setMusicOverride} disabled={controlsDisabled || musicMode === "off" || templateMode === "plain"} />
                </OptionRow>
              </OptionPanel>

              <OptionPanel title="Campaign & style" icon={Sparkles} className="md:col-span-4">
                <OptionRow label="Niche">
                  <Select value={nicheOverride ?? "auto"} onValueChange={(value) => {
                    // Radix fires onValueChange("") when the controlled value
                    // is set externally (the reference-style card auto-selects
                    // a just-created style) before the dropdown has ever opened
                    // and the new item has registered. "" is Radix's
                    // no-selection signal, never a pick in this control — the
                    // sentinel for "no override" is "auto" — so ignore it
                    // instead of letting it clobber the style the card just
                    // selected (which then reached the API as
                    // niche_override: "" and hard-failed task creation).
                    if (!value) return;
                    const next = value === "auto" ? null : value;
                    setNicheOverride(next);
                    // A manual niche pick carries the profile's caption
                    // grammar with it: while the caption style is untouched,
                    // it follows the niche (back to the plain default on
                    // Auto-detect). A style the user picked by hand is never
                    // overridden by a niche change.
                    if (!captionTemplateTouched.current) {
                      setCaptionTemplate(next ? (NICHE_CAPTION_PRESETS[next] ?? "default") : "default");
                    }
                  }}>
                    <SelectTrigger className="h-9 w-[220px] max-w-full text-xs" aria-label="Niche">
                      <SelectValue placeholder="Auto-detect" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">Auto-detect</SelectItem>
                      {NICHE_OPTIONS.map((niche) => <SelectItem key={niche.value} value={niche.value}>{niche.label}</SelectItem>)}
                      {userStyles.map((style) => <SelectItem key={style.name} value={style.name}>{style.display_name} ({t("referenceStyle.pickerSuffix")})</SelectItem>)}
                    </SelectContent>
                  </Select>
                </OptionRow>

                {brandKits.length > 0 && (
                  <OptionRow label="Brand kit">
                    <Select value={brandKitId ?? "none"} onValueChange={(value) => setBrandKitId(value === "none" ? null : value)}>
                      <SelectTrigger className="h-9 w-[220px] max-w-full text-xs" aria-label="Brand kit">
                        <SelectValue placeholder="No brand kit" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">No brand kit</SelectItem>
                        {brandKits.map((kit) => <SelectItem key={kit.id} value={kit.id}>{kit.name}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </OptionRow>
                )}
                {recipes.length > 0 && (
                  <OptionRow label="Recipe">
                    <Select value={recipeId ?? "none"} onValueChange={(value) => setRecipeId(value === "none" ? null : value)}>
                      <SelectTrigger className="h-9 w-[220px] max-w-full text-xs" aria-label="Recipe">
                        <SelectValue placeholder="No recipe" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">No recipe</SelectItem>
                        {recipes.map((recipe) => <SelectItem key={recipe.id} value={recipe.id}>{recipe.name}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </OptionRow>
                )}
              </OptionPanel>

              <OptionPanel title={t("cleanupTitle")} icon={Wand2} className="md:col-span-6">
                <OptionRow label={t("cleanupBody")}>
                  <Popover>
                    <PopoverTrigger asChild>
                      <Chip disabled={controlsDisabled} active={cleanupCount > 0}>
                        <Wand2 className="size-3.5" />{t("cleanup")}{cleanupCount > 0 && <span className="rounded-full bg-foreground px-1.5 text-[10px] text-background">{cleanupCount}</span>}
                        <ChevronDown className="size-3 opacity-60" />
                      </Chip>
                    </PopoverTrigger>
                    <PopoverContent align="end" className="w-[min(92vw,340px)] space-y-4 rounded-2xl">
                      <div>
                        <p className="text-sm font-medium">{t("cleanupTitle")}</p>
                        <p className="text-xs text-muted-foreground">{t("cleanupBody")}</p>
                      </div>
                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <label htmlFor="cut-pauses" className="text-sm font-medium">{t("shortenPauses")}</label>
                          <p className="text-xs text-muted-foreground">{t("shortenPausesHint")}</p>
                        </div>
                        <Switch id="cut-pauses" checked={cutLongPauses} onCheckedChange={setCutLongPauses} />
                      </div>
                      <div className="flex items-center gap-2">
                        <Input type="number" min={250} max={3000} step={50} value={pauseThresholdMs} onChange={(e) => setPauseThresholdMs(e.target.value)} disabled={!cutLongPauses} aria-label={t("pauseThreshold")} className="h-8 w-24" />
                        <span className="text-xs text-muted-foreground">{t("milliseconds")}</span>
                      </div>
                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <label htmlFor="filler-words" className="text-sm font-medium">{t("removeFillers")}</label>
                          <p className="text-xs text-muted-foreground">{t("removeFillersHint")}</p>
                        </div>
                        <Switch id="filler-words" checked={removeFillerWords} onCheckedChange={setRemoveFillerWords} />
                      </div>
                      <div className="space-y-1.5">
                        <label htmlFor="filtered-words" className="text-xs font-medium text-muted-foreground">{t("extraWords")}</label>
                        <Input id="filtered-words" value={filteredWords} onChange={(e) => setFilteredWords(e.target.value)} placeholder={t("extraWordsPlaceholder")} className="h-8" />
                      </div>
                    </PopoverContent>
                  </Popover>
                </OptionRow>
              </OptionPanel>
            </div>

            </div>

            {/* RUN SPEC RAIL — Round 2: a slim sticky summary rail
                (300px), marked by the brand-gradient top bar. Summary
                only — the form's one Generate action lives in the
                composer hero now. The F59 summary rows are unchanged. */}
            <aside className="mt-5 lg:mt-0">
              <div className="glass-panel relative overflow-hidden rounded-2xl lg:sticky lg:top-[72px]">
                <div aria-hidden className="brand-gradient h-[2px] w-full" />
                <div className="p-4">
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                  Run spec
                </p>
                <div aria-label="Run summary" className="mt-3">
                  <p className="text-sm font-medium text-foreground">Before you generate: </p>
                  <div className="mt-1.5">
                    {summaryParts.map((part) => (
                      <div
                        key={part.label}
                        className="flex items-baseline justify-between gap-4 border-t border-foreground/[0.06] py-1.5 first:border-t-0 first:pt-0"
                      >
                        <span className="shrink-0 font-mono text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                          {`${SPEC_ROW_LABELS[part.label] ?? part.label}: `}
                        </span>
                        <span className="min-w-0 truncate text-right text-[13px] font-medium" title={part.value}>
                          {part.value}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
                {upgradeState && <PlanStatusNudge state={upgradeState} onUpgrade={openUpgrade} />}
                </div>
              </div>
            </aside>
            </div>
          </form>

          <div className="mt-5 grid items-start gap-5 lg:grid-cols-2">
            <div>
            <ReferenceStyleCard
              styles={userStyles}
              onRefresh={refreshUserStyles}
              onSelect={(name) => { if (name) setNicheOverride(name); }}
              onDeleted={(name) => {
                if (nicheOverride === name) setNicheOverride(null);
              }}
              disabled={controlsDisabled}
            />
          </div>

            <div className="grid gap-5">
            <div>
            <CampaignBriefPicker
              disabled={controlsDisabled}
              currentBrief={contentBrief}
              onCampaignChange={handleBriefCampaignChange}
            />
          </div>

            <div>
            <ContentBriefPanel
              disabled={controlsDisabled}
              onChange={setContentBrief}
              externalLoad={briefLoad}
              summaryContext={{
                sourceLabel:
                  sourceType === "upload"
                    ? (file?.name ?? null)
                    : url.trim() || null,
                targetClipCount,
                clipLength,
                campaignName: briefCampaign?.name ?? null,
                referenceFromCampaign: Boolean(
                  briefCampaign?.example_url && nicheOverride === null
                ),
              }}
            />
            </div>
            </div>
          </div>

          {isLoading && statusMessage && <p role="status" className="mt-3 text-center text-sm text-muted-foreground sm:hidden">{statusMessage}</p>}

          {error && (
            <Alert className="mt-4 border-red-200 bg-red-50 dark:border-red-900/60 dark:bg-red-950/40">
              <AlertCircle className="h-4 w-4 text-red-500" />
              <AlertDescription className="text-sm text-red-700 dark:text-red-300">{error}</AlertDescription>
            </Alert>
          )}

          <p id="source-limits" className="mt-4 text-center text-xs text-muted-foreground">
            {t("uploadLimits", { size: formatUploadLimit(uploadLimitBytes) })}{" "}
            {limitNote ? `${limitNote} ` : ""}
            {t.rich("completionEmails", {
              settings: (chunks) => (
                <Link href="/settings" className="font-medium text-foreground underline-offset-2 hover:underline">{chunks}</Link>
              ),
            })}
          </p>
        </div>

        <section className="mt-16 md:mt-24" aria-labelledby="recent-heading">
          <div className="mb-4 flex items-end justify-between">
            <h2 id="recent-heading" className="font-display text-lg font-bold tracking-tight">{t("recent")}</h2>
            {recent && recent.length > 0 && (
              <Link href="/list" className="group flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
                {t("viewLibrary")}<ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
              </Link>
            )}
          </div>
          {recent === null ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {[0, 1, 2, 3].map((key) => <Skeleton key={key} className="aspect-[3/4] rounded-xl" />)}
            </div>
          ) : recent.length === 0 ? (
            <div className="rounded-2xl border border-dashed p-10 text-center text-sm text-muted-foreground">
              {t("recentEmpty")}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-3 lg:grid-cols-4">
              {recent.map((generation) => <GenerationCard key={generation.id} generation={generation} />)}
            </div>
          )}
        </section>
      </main>
      <UpgradeDialog
        open={upgradeReason !== null}
        onOpenChange={(open) => { if (!open) setUpgradeReason(null); }}
        reason={upgradeReason ?? lastUpgradeReason}
        billing={billingSummary}
        source="home"
      />
    </AppShell>
  );
}

/**
 * A Studio Console section eyebrow: the mono, uppercase, letter-spaced
 * micro-label that heads every console section (SOURCE, CREATION
 * MODE, TEMPLATE, RUN SPEC…).
 */
function SectionEyebrow({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="font-mono text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
      {children}
    </h2>
  );
}

/**
 * One collapsible group of create options in the bento layout: a
 * glass tile headed by a mono eyebrow. Content stays mounted while
 * collapsed (the collapse is a CSS row animation), so a closed panel
 * never unmounts a control mid-edit.
 */
function OptionPanel({ title, icon: Icon, className, children }: {
  title: string;
  icon: typeof Crop;
  className?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <section className={cn("glass-tile rounded-2xl px-5", className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-3 py-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Icon className="size-4 shrink-0 text-brand" />
        <span className="flex-1 font-mono text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">{title}</span>
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform duration-300", open && "rotate-180")} />
      </button>
      <div
        id={panelId}
        className={cn("grid transition-[grid-template-rows] duration-300 ease-out", open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}
      >
        <div className="overflow-hidden">
          <div className="space-y-4 pb-6 pt-1">{children}</div>
        </div>
      </div>
    </section>
  );
}

function OptionRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <span className="text-sm font-medium text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

function Chip({ active, className, children, ...props }: React.ComponentProps<"button"> & { active?: boolean }) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex h-9 items-center gap-1.5 rounded-full border px-3 text-xs font-medium text-foreground/80 transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50 data-[state=open]:bg-accent",
        active && "border-foreground/30 bg-accent text-foreground",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

function CaptionPreview({ thumbnail, fontFamily, fontSize, color }: { thumbnail: string | null; fontFamily: string; fontSize: number; color: string }) {
  const t = useTranslations("home");
  return (
    <div className="relative mx-auto aspect-[9/16] w-full overflow-hidden rounded-xl bg-stone-800 shadow-inner">
      {thumbnail ? (
        <div className="absolute inset-0 scale-110 bg-cover bg-center blur-[2px]" style={{ backgroundImage: `url(${thumbnail})` }} />
      ) : (
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_30%,oklch(0.55_0.05_50),oklch(0.22_0.01_50))]" />
      )}
      <div className="absolute inset-x-0 top-0 h-1/3 bg-gradient-to-b from-black/40 to-transparent" />
      <p className="absolute inset-x-2 top-[10%] text-center text-[10px] font-bold leading-tight text-white drop-shadow">{t("previewHook")}</p>
      <p
        className="absolute inset-x-2 top-[72%] text-center font-bold leading-snug"
        style={{
          color,
          fontFamily: `'${fontFamily}', system-ui, sans-serif`,
          fontSize: `${Math.max(Math.min(fontSize * 0.5, 18), 10)}px`,
          textShadow: "0 2px 6px rgba(0,0,0,0.8), 0 0 2px rgba(0,0,0,0.9)",
        }}
      >
        {t("previewCaptions")}
      </p>
    </div>
  );
}

function FramingGlyph({ kind }: { kind: OutputFormat }) {
  return (
    <span className="flex h-12 items-center justify-center rounded-lg bg-muted" aria-hidden>
      {kind === "original" ? (
        <span className="h-6 w-10 rounded-[3px] border-2 border-foreground/70" />
      ) : kind === "vertical_fit" ? (
        <span className="flex h-9 w-5 items-center rounded-[3px] border-2 border-foreground/70"><span className="h-3 w-full bg-foreground/30" /></span>
      ) : kind === "vertical_split" ? (
        <span className="flex h-9 w-5 flex-col gap-0.5 rounded-[3px] border-2 border-foreground/70 p-0.5"><span className="flex-1 rounded-[1px] bg-foreground/30" /><span className="flex-1 rounded-[1px] bg-foreground/30" /></span>
      ) : (
        <span className="relative flex h-9 w-5 items-center justify-center rounded-[3px] border-2 border-foreground/70">
          <span className={cn("size-2 rounded-full bg-brand", kind === "vertical_pan" && "motion-safe:animate-[pulse_1.4s_ease-in-out_infinite]")} />
        </span>
      )}
    </span>
  );
}

function PlanStatusNudge({
  state,
  onUpgrade,
}: {
  state: NonNullable<ReturnType<typeof getUpgradeState>>;
  onUpgrade: (reason: UpgradeReason) => void;
}) {
  const t = useTranslations("home");
  const tCommon = useTranslations("common");
  const { formatMinutes } = useBillingPlanText();
  const { nextPlan, resetsOn } = state;
  const refreshes = resetsOn ? t("nudge.refreshesOn", { date: resetsOn }) : t("nudge.refreshesNext");
  const nextPlanName = nextPlan ? tCommon(`plans.${billingPlanMessageKey(nextPlan.id)}`) : "";

  if (state.needsPayment) return null;

  if (!state.isPaid) {
    const entry = getPublicBillingPlans()[0];
    return (
      <UpgradeNudge
        className="mt-6"
        title={t("nudge.startTitle")}
        description={entry ? t("nudge.startDescription", { price: `$${entry.priceMonthly}`, length: formatMinutes(entry.minuteLimit) }) : undefined}
        action={t("nudge.seePlans")}
        onAction={() => onUpgrade({ kind: "start" })}
      />
    );
  }

  if (state.atLimit) {
    return (
      <UpgradeNudge
        className="mt-6"
        title={state.limit ? t("nudge.limitTitle", { length: formatMinutes(state.limit) }) : t("nudge.limitTitleAll")}
        description={nextPlan ? t("nudge.limitWithPlan", { refreshes, plan: nextPlanName, limit: formatMinutes(nextPlan.minuteLimit) }) : state.managedByAppStore ? t("nudge.limitAppStore", { refreshes }) : refreshes}
        action={nextPlan ? t("nudge.getPlan", { plan: nextPlanName }) : undefined}
        onAction={nextPlan ? () => onUpgrade({ kind: "limit" }) : undefined}
      />
    );
  }

  if (state.nearLimit && state.remaining !== null) {
    return (
      <UpgradeNudge
        className="mt-6"
        title={t("nudge.remaining", { count: state.remaining })}
        description={nextPlan ? t("nudge.nearWithPlan", { refreshes, plan: nextPlanName, limit: formatMinutes(nextPlan.minuteLimit) }) : refreshes}
        action={nextPlan ? t("nudge.upgrade") : undefined}
        onAction={nextPlan ? () => onUpgrade({ kind: "explore" }) : undefined}
      />
    );
  }

  return null;
}
