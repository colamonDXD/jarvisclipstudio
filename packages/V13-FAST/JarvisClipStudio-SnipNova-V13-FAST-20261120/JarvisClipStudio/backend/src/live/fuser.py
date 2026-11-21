"""Live moment fuser: chat + audio (+ optional visual) -> confirmed clip candidates.

Rules (Luffy's brief: chat activity must NOT automatically mean a moment):
- A chat spike (``live.chat_signals`` contract: ``peak`` wall time, ``score``
  0-1, ``rejected``) becomes a candidate only when the stream's own audio
  confirms it: excitement z >= ``audio_confirm_z`` somewhere in
  [peak - chat_lag_max, peak + 3 s] (chat reacts AFTER viewers see the
  moment, by the stream delay plus typing time). Rejected spikes (spam,
  bots, raids, copypasta) never count.
- Audio alone can propose a moment when it is strong and sustained
  (z >= ``audio_only_z`` for >= ``audio_only_sec``), e.g. a scream, at a
  lower score, so a quiet chat cannot hide a real reaction.
- An optional ``visual_confirm(candidate) -> (bool, why)`` hook (SigLIP story
  moments, screen detection) can veto or confirm before rendering.
- Window: from ``preroll`` before the reaction onset (the stimulus lives
  there) to ``postroll`` after excitement settles (the payoff), capped at
  ``max_len``; pre/post-roll are per-event-type settings, not a fixed 30 s.
- De-duplication: candidates whose onsets are within ``dedupe_sec`` merge,
  and the higher score wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


@dataclass
class FuserConfig:
    audio_confirm_z: float = 2.5
    audio_only_z: float = 4.0
    audio_only_sec: float = 1.5
    chat_lag_max: float = 25.0
    preroll: float = 20.0
    postroll: float = 6.0
    settle_z: float = 1.0
    settle_sec: float = 3.0
    max_len: float = 60.0
    dedupe_sec: float = 90.0
    reaction_min_score: float = 0.3  # picker items like "event, no reaction" score ~0.1


@dataclass
class Candidate:
    onset: float
    start: float
    end: float
    score: float
    sources: List[str]
    reasons: List[str] = field(default_factory=list)
    chat: Optional[Dict[str, Any]] = None
    peak_z: float = 0.0
    visual: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"onset": round(self.onset, 2), "start": round(self.start, 2), "end": round(self.end, 2),
                "score": round(self.score, 3), "sources": self.sources, "reasons": self.reasons,
                "peak_z": round(self.peak_z, 2), "visual": self.visual,
                "chat_peak": (self.chat or {}).get("peak")}


def _settle_end(z: Sequence[Tuple[float, float]], onset: float, cfg: FuserConfig) -> float:
    """First time after onset where z stays below settle_z for settle_sec."""
    quiet_since = None
    last_t = onset
    for t, v in z:
        if t < onset:
            continue
        last_t = t
        if v < cfg.settle_z:
            quiet_since = t if quiet_since is None else quiet_since
            if t - quiet_since >= cfg.settle_sec:
                return quiet_since
        else:
            quiet_since = None
    return last_t


def _window(onset: float, z, cfg: FuserConfig) -> Tuple[float, float]:
    start = onset - cfg.preroll
    end = min(_settle_end(z, onset, cfg) + cfg.postroll, start + cfg.max_len)
    return start, end


def fuse(chat_spikes: Sequence[Dict[str, Any]], audio_z: Sequence[Tuple[float, float]],
         cfg: Optional[FuserConfig] = None,
         visual_confirm: Optional[Callable[[Candidate], Tuple[bool, str]]] = None,
         reactions: Sequence[Dict[str, Any]] = ()) -> List[Candidate]:
    """``reactions``: moments from the streamer reaction picker on a buffer
    window, as ``{onset, start, end, score 0-1, reason}`` on the wall clock.
    They are confirmed evidence on their own (face + voice + game event), and
    a chat spike landing just after one confirms and boosts it."""
    cfg = cfg or FuserConfig()
    z = sorted(audio_z)
    found: List[Candidate] = []

    # 0) Streamer reaction moments (the Phase 2 picker on the rolling buffer).
    accepted_chat = [s for s in chat_spikes if not s.get("rejected")]
    for r in reactions:
        if float(r.get("score", 0.5)) < cfg.reaction_min_score:
            continue
        onset = float(r["onset"])
        start = min(float(r["start"]), onset - cfg.preroll)
        end = min(max(float(r["end"]), onset + cfg.postroll), start + cfg.max_len)
        score = float(r.get("score", 0.5))
        sources, reasons = ["reaction"], [str(r.get("reason") or "streamer reaction")]
        chat = next((s for s in accepted_chat if onset - 3.0 <= float(s["peak"]) <= onset + cfg.chat_lag_max), None)
        if chat is not None:
            score = min(1.0, score + 0.3 * float(chat.get("score", 0.5)))
            sources.append("chat")
            reasons.append(f"chat spike at {float(chat['peak']):.1f} right after the reaction")
        found.append(Candidate(onset, start, end, score, sources, reasons, chat=chat))

    # 1) Chat spikes confirmed by audio.
    for spike in chat_spikes:
        if spike.get("rejected"):
            continue
        peak = float(spike["peak"])
        near = [(t, v) for t, v in z if peak - cfg.chat_lag_max <= t <= peak + 3.0]
        hot = [(t, v) for t, v in near if v >= cfg.audio_confirm_z]
        if not hot:
            continue  # chat alone never makes a clip
        onset = hot[0][0]
        peak_z = max(v for _, v in hot)
        start, end = _window(onset, z, cfg)
        score = 0.5 * float(spike.get("score", 0.5)) + 0.5 * min(1.0, peak_z / 6.0)
        found.append(Candidate(onset, start, end, score, ["chat", "audio"],
                               [f"chat spike at {peak:.1f} confirmed by audio z={peak_z:.1f} from {onset:.1f}"],
                               chat=dict(spike), peak_z=peak_z))

    # 2) Strong sustained audio alone.
    run_start, run_peak = None, 0.0
    for t, v in z + [(float("inf"), 0.0)]:
        if v >= cfg.audio_only_z:
            run_start = t if run_start is None else run_start
            run_peak = max(run_peak, v)
            continue
        if run_start is not None and t - run_start >= cfg.audio_only_sec and t != float("inf"):
            start, end = _window(run_start, z, cfg)
            found.append(Candidate(run_start, start, end, 0.4 * min(1.0, run_peak / 6.0) + 0.1, ["audio"],
                                   [f"sustained audio excitement z={run_peak:.1f} from {run_start:.1f}"],
                                   peak_z=run_peak))
        run_start, run_peak = None, 0.0

    # 3) Optional visual confirmation.
    kept: List[Candidate] = []
    for cand in found:
        if visual_confirm is not None:
            ok, why = visual_confirm(cand)
            cand.visual = why
            if not ok:
                continue
        kept.append(cand)

    # 4) De-duplicate: one candidate per event.
    kept.sort(key=lambda c: c.score, reverse=True)
    final: List[Candidate] = []
    for cand in kept:
        if all(abs(cand.onset - other.onset) > cfg.dedupe_sec for other in final):
            final.append(cand)
        else:
            twin = next(o for o in final if abs(cand.onset - o.onset) <= cfg.dedupe_sec)
            for s in cand.sources:
                if s not in twin.sources:
                    twin.sources.append(s)
            twin.reasons.extend(cand.reasons)
    return sorted(final, key=lambda c: c.onset)
