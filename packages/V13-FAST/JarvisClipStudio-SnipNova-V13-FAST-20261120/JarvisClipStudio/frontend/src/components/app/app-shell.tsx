"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot, ChartColumn, CornerDownLeft, Film, LogOut, Plus, Radio, Search, Send, Settings, Share2, Shield, Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { signOut, useSession } from "@/lib/auth-client";
import { resetAnalytics } from "@/lib/posthog";
import { useBillingSummary } from "@/hooks/use-billing-summary";
import { useSocialAccess } from "@/hooks/use-social-access";
import { billingPlanMessageKey } from "@/lib/billing-plans";
import { getUpgradeState } from "@/components/billing/upgrade-prompt";
import { fetchGenerations, timeAgo, type GenerationSummary } from "@/lib/generations";
import { StatusBadge } from "@/components/app/status-badge";
import { AppBackground } from "@/components/reactbits/AppBackground";
import { useSpotlight } from "@/components/reactbits/SpotlightCard";
import { Announcements } from "@/components/announcements";
import { ThemeToggle } from "@/components/theme-toggle";
import { useAppTheme } from "@/components/theme-provider";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";

// `social: true` items only show when social publishing is enabled for the user.
type NavItem = { href: string; labelKey: "create" | "library" | "social" | "analytics" | "publishing" | "agents" | "settings"; icon: typeof Film; match: (path: string) => boolean; social?: boolean };

const NAV: NavItem[] = [
  { href: "/", labelKey: "create", icon: Sparkles, match: (p) => p === "/" },
  { href: "/list", labelKey: "library", icon: Film, match: (p) => p.startsWith("/list") || p.startsWith("/tasks") },
  { href: "/settings/social", labelKey: "social", icon: Share2, match: (p) => p.startsWith("/settings/social"), social: true },
  { href: "/analytics", labelKey: "analytics", icon: ChartColumn, match: (p) => p.startsWith("/analytics"), social: true },
  { href: "/publishing", labelKey: "publishing", icon: Send, match: (p) => p.startsWith("/publishing") },
  { href: "/settings/api-keys", labelKey: "agents", icon: Bot, match: (p) => p.startsWith("/settings/api-keys") },
  { href: "/settings", labelKey: "settings", icon: Settings, match: (p) => p === "/settings" },
];

function initials(name?: string | null, email?: string | null) {
  const source = name?.trim() || email || "?";
  return source.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");
}

/**
 * Signed-in application chrome: a two-tier top navigation bar over the
 * aurora background — no left rail, no bottom bar, at any breakpoint.
 * The utility tier carries the SNIPNOVA lockup and current-section
 * title on the left, the ⌘K search as the centrepiece, and usage,
 * announcements, the primary action and the account cluster on the
 * right. Directly beneath it, the nav tier carries every primary
 * destination as an icon-and-label tab, the active one underlined in
 * brand colour; the strip scrolls horizontally when it overflows, so
 * the same bar serves phones and desktops. The editor is a full-screen
 * workspace with its own back button, so the chrome steps aside there.
 * The ⌘K palette and the N shortcut work everywhere.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations("shell");
  const { data: session } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [billing] = useBillingSummary(Boolean(session?.user));
  // The editor is a full-screen workspace with its own back button: no
  // app chrome there. Elements stay in place (hidden) so children keep
  // their position.
  const immersive = /^\/tasks\/[^/]+\/edit/.test(pathname);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      const target = event.target as HTMLElement | null;
      const typing = target?.closest("input, textarea, select, [contenteditable=true], [role=dialog]");
      if (!typing && !event.metaKey && !event.ctrlKey && !event.altKey && event.key.toLowerCase() === "n") {
        event.preventDefault();
        router.push("/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  // Children must keep the same tree position whether or not the session has
  // loaded — otherwise the page remounts and re-runs its initial fetches.
  const user = session?.user;
  useAppTheme(Boolean(user));
  const admin = Boolean((user as { is_admin?: boolean } | undefined)?.is_admin);
  const socialAccess = useSocialAccess(Boolean(user));
  const labeled = NAV.map((item) => ({ ...item, label: t(`nav.${item.labelKey}`) }));
  // Live clipping: shown only when the server has LIVE_CLIPPING on.
  const [liveEnabled, setLiveEnabled] = useState(false);
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    fetch("/api/live/status", { cache: "no-store" })
      .then(async (r) => (r.ok ? ((await r.json()) as { enabled?: boolean }) : null))
      .then((data) => { if (!cancelled) setLiveEnabled(data?.enabled === true); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [user]);
  const nav = labeled.filter((item) => !item.social || socialAccess === true);
  if (liveEnabled) {
    nav.splice(2, 0, { href: "/live", labelKey: "create", label: "Live", icon: Radio, match: (p: string) => p.startsWith("/live") });
  }
  const items = admin ? [...nav, { href: "/admin", label: t("nav.admin"), icon: Shield, match: (p: string) => p.startsWith("/admin") }] : nav;

  const pageTitle =
    items.find((item) => item.match(pathname))?.label ??
    (pathname.startsWith("/settings") ? t("nav.settings") : null);

  return (
    <div data-app-shell={user ? "" : undefined} className={cn(user && "min-h-dvh bg-canvas isolate")}>
      {user ? <AppBackground /> : null}

      <div className="min-w-0">
        {user && !immersive ? (
          <header className="sticky top-0 z-40 border-b border-foreground/[0.07] bg-background/50 backdrop-blur-xl">
            <div className="flex h-14 items-center gap-3 px-4 sm:px-6">
              <Link href="/" aria-label="SnipNova" className="flex shrink-0 items-center">
                <Image src="/logo.svg" alt="" width={28} height={28} className="size-[28px]" priority />
              </Link>
              <div className="flex min-w-0 items-baseline gap-2.5">
                <span className="font-display text-[15px] font-bold uppercase tracking-tight">
                  SnipNova
                </span>
                {pageTitle ? (
                  <>
                    <span aria-hidden className="hidden text-muted-foreground/40 md:block">/</span>
                    <span className="hidden truncate font-display text-[15px] font-semibold tracking-tight md:block">
                      {pageTitle}
                    </span>
                  </>
                ) : null}
              </div>

              <div className="flex min-w-0 flex-1 justify-center px-1 sm:px-3">
                <button
                  type="button"
                  onClick={() => setPaletteOpen(true)}
                  aria-label={t("search.generations")}
                  className="flex h-9 w-full max-w-lg items-center gap-2.5 rounded-lg border border-foreground/[0.08] bg-card/50 px-3 text-sm text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Search className="size-4 shrink-0" />
                  <span className="min-w-0 flex-1 truncate text-left">{t("search.placeholder")}</span>
                  <Kbd className="hidden sm:inline-flex">⌘K</Kbd>
                </button>
              </div>

              <div className="flex shrink-0 items-center gap-1.5">
                <Link
                  href="/"
                  className="hidden h-9 items-center gap-1.5 rounded-lg bg-foreground px-3.5 text-sm font-medium text-background transition-colors hover:bg-foreground/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex"
                >
                  <Plus className="size-4" />
                  {t("nav.newGeneration")}
                </Link>
                {billing?.monetization_enabled && <UsagePill billing={billing} />}
                <Announcements key={user.id} userId={user.id} compact />
                <UserMenu name={user.name} email={user.email} />
              </div>
            </div>

            <TopNav items={items} pathname={pathname} />
          </header>
        ) : null}
        {children}
      </div>

      {user ? <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} /> : null}
    </div>
  );
}

/**
 * The nav tier of the top bar: every primary destination as an
 * icon-and-label tab in rail order. The active tab reads in full
 * foreground with a brand icon and a brand underline flush with the
 * header's bottom border. The strip scrolls horizontally when it
 * overflows, so one strip serves every screen size.
 */
function TopNav({
  items,
  pathname,
}: {
  items: { href: string; label: string; icon: typeof Film; match: (path: string) => boolean }[];
  pathname: string;
}) {
  const t = useTranslations("shell");
  return (
    <nav
      aria-label={t("search.mainNav")}
      className="scrollbar-none flex h-11 items-stretch gap-1 overflow-x-auto px-4 sm:px-6"
    >
      {items.map((item) => {
        const active = item.match(pathname);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative flex shrink-0 items-center gap-2 px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
              active
                ? "font-medium text-foreground"
                : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
            )}
          >
            <item.icon className={cn("size-4 shrink-0", active && "text-brand")} />
            {item.label}
            {active ? (
              <span aria-hidden className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-brand" />
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}

function UsagePill({ billing }: { billing: NonNullable<ReturnType<typeof useBillingSummary>[0]> }) {
  const t = useTranslations("shell");
  const tCommon = useTranslations("common");
  const spot = useSpotlight<HTMLAnchorElement>();
  const limit = billing.minute_limit ?? null;
  const used = billing.usage_minutes ?? 0;
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
  const upgrade = getUpgradeState(billing);
  const nudge = !upgrade?.isPaid
    ? t("usage.pickPlan")
    : upgrade.nextPlan && (upgrade.atLimit || upgrade.nearLimit)
    ? t("usage.moreWith", { name: tCommon(`plans.${billingPlanMessageKey(upgrade.nextPlan.id)}`) })
    : null;
  const detail = nudge ?? (limit && upgrade?.remaining !== null ? t("usage.minutesLeft", { count: upgrade?.remaining ?? 0 }) : t("usage.minutesThisMonth"));
  return (
    <Link
      ref={spot.ref}
      onMouseMove={spot.onMouseMove}
      onMouseLeave={spot.onMouseLeave}
      href="/settings#plan"
      title={detail}
      className="rb-spotlight hidden h-9 items-center gap-2 overflow-hidden rounded-lg border border-foreground/10 bg-card/60 px-3 text-xs backdrop-blur transition-colors hover:border-foreground/25 md:flex"
    >
      <span className="font-semibold">{tCommon(`plans.${billingPlanMessageKey(billing.plan)}`)}</span>
      <span className="text-muted-foreground tabular-nums">
        {limit ? t("usage.minutesOf", { used, limit }) : t("usage.minutes", { used })}
      </span>
      {limit ? (
        <span className="h-1 w-10 overflow-hidden rounded-full bg-muted">
          <span className="block h-full rounded-full bg-brand" style={{ width: `${pct}%` }} />
        </span>
      ) : null}
    </Link>
  );
}

/**
 * The account cluster: avatar, theme toggle, sign-out — a single row
 * at the right end of the utility tier, tooltips opening below.
 */
function UserMenu({
  name,
  email,
}: {
  name?: string | null;
  email?: string | null;
}) {
  const t = useTranslations("shell");
  const [signingOut, setSigningOut] = useState(false);
  const handleSignOut = async () => {
    setSigningOut(true);
    try { await signOut(); resetAnalytics(); window.location.assign("/sign-in"); }
    catch { toast.error(t("user.signOutError")); setSigningOut(false); }
  };
  return (
    <div className="flex items-center gap-1.5">
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex size-8 shrink-0 cursor-default items-center justify-center rounded-full bg-gradient-to-br from-brand to-brand-2 text-xs font-semibold text-white">
            {initials(name, email)}
          </span>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          <span className="block font-medium">{name || t("user.creator")}</span>
          {email ? <span className="block text-muted-foreground">{email}</span> : null}
        </TooltipContent>
      </Tooltip>
      <ThemeToggle tooltipSide="bottom" />
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" onClick={() => void handleSignOut()} disabled={signingOut} aria-label={t("user.signOut")}
            className="rounded-full p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50">
            <LogOut className="size-4" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">{signingOut ? t("user.signingOut") : t("user.signOut")}</TooltipContent>
      </Tooltip>
    </div>
  );
}

type PaletteEntry = { id: string; label: string; hint?: string; href: string; icon: typeof Film; generation?: GenerationSummary };

function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useTranslations("shell");
  const locale = useLocale();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [generations, setGenerations] = useState<GenerationSummary[]>([]);
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    void fetchGenerations().then(setGenerations).catch(() => {});
  }, [open]);

  const socialAccess = useSocialAccess(open);
  const entries = useMemo<PaletteEntry[]>(() => {
    const q = query.trim().toLowerCase();
    const actions: PaletteEntry[] = [
      { id: "new", label: t("nav.newGeneration"), hint: t("palette.newHint"), href: "/", icon: Plus },
      { id: "library", label: t("palette.library"), href: "/list", icon: Film },
      ...(socialAccess === true ? [{ id: "social", label: t("palette.social"), hint: t("palette.socialHint"), href: "/settings/social", icon: Share2 },
      { id: "analytics", label: t("nav.analytics"), hint: t("palette.analyticsHint"), href: "/analytics", icon: ChartColumn }] : []),
      { id: "publishing", label: t("nav.publishing"), hint: t("palette.publishingHint"), href: "/publishing", icon: Send },
      { id: "keys", label: t("nav.agents"), hint: t("palette.agentsHint"), href: "/settings/api-keys", icon: Bot },
      { id: "settings", label: t("nav.settings"), href: "/settings", icon: Settings },
    ].filter((entry) => !q || entry.label.toLowerCase().includes(q));
    const matches = generations
      .filter((generation) => !q || generation.source_title.toLowerCase().includes(q))
      .slice(0, 8)
      .map((generation) => ({
        id: generation.id, label: generation.source_title, hint: timeAgo(generation.created_at, locale),
        href: `/tasks/${generation.id}`, icon: Film, generation,
      }));
    return [...actions, ...matches];
  }, [generations, query, socialAccess, t, locale]);

  const go = useCallback((entry: PaletteEntry | undefined) => {
    if (!entry) return;
    onOpenChange(false);
    router.push(entry.href);
  }, [onOpenChange, router]);

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="top-[18%] max-w-xl translate-y-0 gap-0 overflow-hidden p-0">
        <DialogTitle className="sr-only">{t("search.dialog")}</DialogTitle>
        <div className="flex items-center gap-3 border-b px-4">
          <Search className="size-4 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(event) => { setQuery(event.target.value); setActive(0); }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") { event.preventDefault(); setActive((i) => Math.min(entries.length - 1, i + 1)); }
              if (event.key === "ArrowUp") { event.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
              if (event.key === "Enter") { event.preventDefault(); go(entries[active]); }
            }}
            placeholder={t("search.placeholder")}
            aria-label={t("search.inputLabel")}
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <Kbd>esc</Kbd>
        </div>
        <div ref={listRef} className="max-h-80 overflow-y-auto p-1.5" role="listbox">
          {entries.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted-foreground">{t("search.empty", { query })}</p>}
          {entries.map((entry, index) => (
            <button
              key={entry.id}
              type="button"
              role="option"
              aria-selected={index === active}
              data-index={index}
              onMouseMove={() => setActive(index)}
              onClick={() => go(entry)}
              className={cn("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm", index === active && "bg-accent")}
            >
              <entry.icon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{entry.label}</span>
              {entry.generation ? <StatusBadge status={entry.generation.status} /> : null}
              {entry.hint && <span className="shrink-0 text-xs text-muted-foreground">{entry.hint}</span>}
              {index === active && <CornerDownLeft className="size-3.5 text-muted-foreground" />}
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
