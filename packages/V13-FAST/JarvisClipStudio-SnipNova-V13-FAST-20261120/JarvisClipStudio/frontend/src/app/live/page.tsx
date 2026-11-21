"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Loader2, Radio, Square, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { BoxDraw } from "./box-draw";
import type { ManualBoxes } from "./boxes";

type LiveSession = {
  id: string;
  url: string;
  status: "requested" | "starting" | "recording" | "stopped" | "ended" | "error";
  settings: { layout_template?: string | null; credit?: string | null; hook_title?: string | null; boxes?: ManualBoxes | null };
  recording: boolean;
  buffered_seconds: number;
  restarts: number;
  last_error: string | null;
  progress_message?: string | null;
  mode?: "live" | "vod";
  started_at: string | null;
  created_at: string | null;
};

type LiveClip = {
  id: string;
  n: number | null;
  status: "rejected" | "queued" | "rendering" | "ready" | "failed";
  reason: string | null;
  onset_at: string | null;
  signals: string[];
  strength: number | null;
  talk_share: number | null;
  has_preview: boolean;
  has_clip: boolean;
  duration: number | null;
  created_at: string | null;
};

const RUNNING = ["requested", "starting", "recording"];
const PAGE = "mx-auto w-full max-w-5xl px-4 py-8 sm:px-8 md:py-10";
const LAYOUTS = [
  { value: "facecam_red", label: "Face on top · red" },
  { value: "facecam_gold", label: "Face on top · gold" },
  { value: "", label: "No facecam (IRL, podcast)" },
];
const SIGNAL_LABELS: Record<string, string> = {
  scream: "Scream", laugh: "Laugh", f0: "Pitch jump", vocal: "Loud voice",
};

function errorText(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return fallback;
}

function clock(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
}

function minutes(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

function StatusLine({ session }: { session: LiveSession }) {
  if (session.mode === "vod" && (session.status === "recording" || session.status === "starting")) {
    return (
      <span className="inline-flex items-center gap-2 text-sm">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {session.progress_message || "Starting…"}
      </span>
    );
  }
  if (session.status === "recording") {
    return (
      <span className="inline-flex items-center gap-2 text-sm">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
        </span>
        Recording · {minutes(session.buffered_seconds)} buffered
        {session.buffered_seconds < 240 && (
          <span className="text-muted-foreground">· learning the streamer&apos;s voice (first clips after ~4 min)</span>
        )}
      </span>
    );
  }
  const text: Record<LiveSession["status"], string> = {
    requested: "Waiting for the live worker…",
    starting: "Connecting to the stream…",
    recording: "",
    stopped: "Stopped",
    ended: session.mode === "vod" ? (session.progress_message || "Finished") : "Stream ended",
    error: "Error",
  };
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      {(session.status === "requested" || session.status === "starting") && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {text[session.status]}
      {session.last_error && session.status !== "stopped" && <span className="truncate">· {session.last_error}</span>}
    </span>
  );
}

function ClipCard({ clip }: { clip: LiveClip }) {
  const src = clip.has_clip ? `/api/live/clips/${clip.id}/file?kind=clip` : `/api/live/clips/${clip.id}/file?kind=preview`;
  const badge =
    clip.status === "ready" ? null :
    clip.status === "failed" ? "Render failed" :
    clip.status === "rendering" ? "Finishing…" : "Preview · finishing soon";
  return (
    <div className="overflow-hidden rounded-xl border bg-background">
      <div className="relative aspect-[9/16] bg-black">
        {(clip.has_clip || clip.has_preview) && (
          <video key={src} src={src} controls playsInline preload="metadata" className="h-full w-full object-contain" />
        )}
        {badge && (
          <span className={cn("absolute left-2 top-2 rounded-full px-2 py-0.5 text-xs font-medium",
            clip.status === "failed" ? "bg-destructive text-white" : "bg-black/70 text-white")}>
            {clip.status === "rendering" && <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />}
            {badge}
          </span>
        )}
      </div>
      <div className="space-y-2 p-3">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>#{clip.n} · {clock(clip.onset_at)}</span>
          {clip.duration ? <span>{Math.round(clip.duration)} s</span> : null}
        </div>
        <div className="flex flex-wrap gap-1">
          {clip.signals.map((s) => (
            <span key={s} className="rounded-full bg-muted px-2 py-0.5 text-xs">{SIGNAL_LABELS[s] ?? s}</span>
          ))}
        </div>
        {clip.has_clip && (
          <Button asChild size="sm" className="w-full">
            <a href={`/api/live/clips/${clip.id}/file?kind=clip`} download>
              <Download className="mr-1.5 h-4 w-4" /> Download
            </a>
          </Button>
        )}
      </div>
    </div>
  );
}

export default function LivePage() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [sessions, setSessions] = useState<LiveSession[]>([]);
  const [current, setCurrent] = useState<{ session: LiveSession; clips: LiveClip[] } | null>(null);
  const [url, setUrl] = useState("");
  const [layout, setLayout] = useState("facecam_red");
  const [credit, setCredit] = useState("");
  const [title, setTitle] = useState("");
  const [clipCount, setClipCount] = useState("10");
  const [request, setRequest] = useState("");
  const [voiceFx, setVoiceFx] = useState("");
  const [boxes, setBoxes] = useState<ManualBoxes | null>(null);
  const [busy, setBusy] = useState(false);
  const selected = useRef<string | null>(null);

  const loadSessions = useCallback(async () => {
    const response = await fetch("/api/live/sessions", { cache: "no-store" });
    if (!response.ok) return [];
    const data = (await response.json()) as { sessions: LiveSession[] };
    setSessions(data.sessions);
    return data.sessions;
  }, []);

  const loadSession = useCallback(async (id: string) => {
    const response = await fetch(`/api/live/sessions/${id}`, { cache: "no-store" });
    if (!response.ok) return;
    const data = (await response.json()) as { session: LiveSession; clips: LiveClip[] };
    if (selected.current === id) setCurrent(data);
  }, []);

  useEffect(() => {
    fetch("/api/live/status", { cache: "no-store" })
      .then(async (r) => (r.ok ? ((await r.json()) as { enabled: boolean }) : { enabled: false }))
      .then(async (s) => {
        setEnabled(s.enabled);
        if (!s.enabled) return;
        const list = await loadSessions();
        if (list.length) {
          selected.current = list[0].id;
          await loadSession(list[0].id);
        }
      })
      .catch(() => setEnabled(false));
  }, [loadSessions, loadSession]);

  const running = current ? RUNNING.includes(current.session.status) : false;
  const rendering = current?.clips.some((c) => c.status === "queued" || c.status === "rendering") ?? false;

  useEffect(() => {
    if (!current || (!running && !rendering)) return;
    const id = current.session.id;
    const timer = window.setInterval(() => { void loadSession(id); }, 5000);
    return () => window.clearInterval(timer);
  }, [current, running, rendering, loadSession]);

  async function start(event: React.FormEvent) {
    event.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    try {
      const response = await fetch("/api/live/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: url.trim(),
          layout_template: layout || null,
          credit: credit.trim() || null,
          hook_title: title.trim() || null,
          max_clips: Math.max(1, Math.min(30, parseInt(clipCount, 10) || 10)),
          request: request.trim() || null,
          voice_fx: voiceFx || null,
          ...(boxes && layout ? { boxes } : {}),
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        toast.error(errorText(body, "Could not start live clipping."));
        return;
      }
      const session = (body as { session: LiveSession }).session;
      selected.current = session.id;
      setCurrent({ session, clips: [] });
      setUrl("");
      toast.success(session.settings && (session as LiveSession).mode === "vod"
        ? "Started. Only the audio is downloaded first; clips appear here one by one."
        : "Live clipping started. Clips appear here as the stream runs.");
      void loadSessions();
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!current) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/live/sessions/${current.session.id}/stop`, { method: "POST" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) toast.error(errorText(body, "Could not stop the session."));
      else toast.success("Stopping. Clips already found will still finish.");
      await loadSession(current.session.id);
      void loadSessions();
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    const response = await fetch(`/api/live/sessions/${id}`, { method: "DELETE" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      toast.error(errorText(body, "Could not delete the session."));
      return;
    }
    if (selected.current === id) {
      selected.current = null;
      setCurrent(null);
    }
    void loadSessions();
  }

  if (enabled === null) {
    return <div className={PAGE}><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>;
  }
  if (!enabled) {
    return (
      <div className={PAGE}>
        <h1 className="text-2xl font-semibold">Live clipping</h1>
        <p className="mt-2 text-muted-foreground">Live clipping is not switched on for this server.</p>
      </div>
    );
  }

  const made = current?.clips.filter((c) => c.status !== "rejected") ?? [];
  const skipped = current?.clips.filter((c) => c.status === "rejected") ?? [];
  const anotherRunning = sessions.some((s) => RUNNING.includes(s.status));

  return (
    <div className={PAGE}>
      <div className="flex items-center gap-2">
        <Radio className="h-6 w-6 text-red-500" />
        <h1 className="text-2xl font-semibold">Live clipping</h1>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        Paste a stream that is live right now, or a finished stream up to 24 hours long. For a finished stream only the
        audio is downloaded first; the best moments are found from the streamer&apos;s reactions, what viewers rewatched
        and what was said, and only those pieces are downloaded in 1080p. Each clip opens on the moment, with captions of
        the streamer&apos;s words only. Only clip streams you own or are allowed to clip.
      </p>

      <form onSubmit={start} className="mt-6 grid gap-4 rounded-xl border bg-background p-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label htmlFor="live-url">Stream link: live now or finished (YouTube or Twitch)</Label>
          <Input id="live-url" value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.youtube.com/watch?v=…  or  https://www.twitch.tv/…" className="mt-1.5" />
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor="live-request">Find moments where… (optional, finished streams)</Label>
          <Input id="live-request" value={request} maxLength={200} onChange={(e) => setRequest(e.target.value)}
            placeholder="e.g. every time he crashes · when he talks about Lucia · funniest fails" className="mt-1.5" />
          <p className="mt-1 text-xs text-muted-foreground">
            Leave empty to get the most viral moments. With a request, the whole stream is read and only matching moments are clipped.
          </p>
        </div>
        <div>
          <Label htmlFor="live-layout">Layout</Label>
          <select id="live-layout" value={layout} onChange={(e) => setLayout(e.target.value)}
            className="mt-1.5 h-9 w-full rounded-md border bg-background px-3 text-sm">
            {LAYOUTS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </div>
        <div>
          <Label htmlFor="live-credit">Credit line (optional)</Label>
          <Input id="live-credit" value={credit} maxLength={40} onChange={(e) => setCredit(e.target.value)}
            placeholder="@streamer" className="mt-1.5" />
        </div>
        <div>
          <Label htmlFor="live-voice">Funny voice</Label>
          <select id="live-voice" value={voiceFx} onChange={(e) => setVoiceFx(e.target.value)}
            className="mt-1.5 h-9 w-full rounded-md border bg-background px-3 text-sm">
            <option value="">Off (original voice)</option>
            <option value="high">High (chipmunk)</option>
            <option value="deep">Deep</option>
          </select>
        </div>
        <div>
          <Label htmlFor="live-count">Clips (finished streams)</Label>
          <Input id="live-count" type="number" min={1} max={30} value={clipCount}
            onChange={(e) => setClipCount(e.target.value)} className="mt-1.5" />
        </div>
        <div>
          <Label htmlFor="live-title">Title on every clip (optional, leave empty for none)</Label>
          <Input id="live-title" value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)}
            placeholder="GTA 6 LIVE" className="mt-1.5" />
        </div>
        {layout && <BoxDraw url={url} layout={layout} onChange={setBoxes} />}
        <div className="sm:col-span-2">
          <Button type="submit" disabled={busy || !url.trim() || anotherRunning}>
            {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Radio className="mr-1.5 h-4 w-4" />}
            Start clipping
          </Button>
          {anotherRunning && <span className="ml-3 text-sm text-muted-foreground">Stop the running session to start another.</span>}
        </div>
      </form>

      {current && (
        <section className="mt-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{current.session.url}</p>
              <StatusLine session={current.session} />
            </div>
            {running && (
              <Button variant="destructive" size="sm" onClick={stop} disabled={busy}>
                <Square className="mr-1.5 h-4 w-4" /> Stop
              </Button>
            )}
          </div>

          {made.length === 0 ? (
            <p className="mt-6 rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
              {running ? "No clips yet. They appear here within a few minutes of a reaction." : "No clips in this session."}
            </p>
          ) : (
            <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {[...made].reverse().map((clip) => <ClipCard key={clip.id} clip={clip} />)}
            </div>
          )}

          {skipped.length > 0 && (
            <details className="mt-6 rounded-xl border p-4 text-sm">
              <summary className="cursor-pointer font-medium">Skipped moments ({skipped.length})</summary>
              <ul className="mt-3 space-y-1 text-muted-foreground">
                {skipped.map((c) => (
                  <li key={c.id}>{clock(c.onset_at)} · {c.reason}</li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      {sessions.length > 0 && (
        <section className="mt-10">
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground">Sessions</h2>
          <ul className="divide-y rounded-xl border">
            {sessions.map((s) => (
              <li key={s.id} className="flex items-center gap-3 p-3 text-sm">
                <button type="button" className="min-w-0 flex-1 truncate text-left hover:underline"
                  onClick={() => { selected.current = s.id; void loadSession(s.id); }}>
                  {s.url}
                </button>
                <span className="shrink-0 text-xs text-muted-foreground">{s.status} · {clock(s.created_at)}</span>
                {!RUNNING.includes(s.status) && (
                  <Button variant="ghost" size="icon" aria-label="Delete session" onClick={() => void remove(s.id)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
