"""GPU VRAM coordination: telemetry snapshots and cross-process courtesy.

This module is the one place that knows how to ask the card how much
room it has. Two consumers:

- Stage telemetry (:func:`log_vram`): one structured INFO line at each
  pipeline stage boundary — ``vram stage=<name> free_mb=<n>
  total_mb=<n>`` — so a run on the reference 6 GB card (RTX 4050
  Laptop) shows exactly how much headroom each stage had. The
  benchmark pack's VRAM scenario (tools/benchmark-pack/
  vram-scenario.ps1) parses these lines; keep the format stable.
- The Stage-1 build-lite fixes (whisper cache eviction, the Ollama
  ``keep_alive: 0`` unload handshake, the Demucs device gate) read the
  same snapshot instead of each growing their own probe.

Everything here is best-effort by contract: no function in this module
ever raises. A machine without CUDA, without torch, or without a
reachable Ollama simply gets "unavailable" answers, and every caller
keeps its previous behaviour — on a big-VRAM machine nothing in here
changes any decision.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from ..config import get_config

logger = logging.getLogger(__name__)

# The unload handshake must never stall a stage: Ollama is a host
# process that may be stopped, busy, or unreachable, and every answer
# (including silence) is acceptable.
OLLAMA_UNLOAD_TIMEOUT_SECONDS = 2.0


def vram_snapshot() -> Optional[tuple[int, int]]:
    """(free_bytes, total_bytes) for the first CUDA device, or None.

    None means "unknown": torch missing, no CUDA device, or the probe
    itself failed. Callers must treat None as "no information", never
    as zero — a failed probe must not reroute work to the CPU on a
    healthy machine.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        return int(free), int(total)
    except Exception:
        return None


def log_vram(stage: str) -> None:
    """Emit the Stage-0 telemetry line for one pipeline stage boundary.

    INFO when a snapshot is available (the benchmark machine), DEBUG
    otherwise, so CPU-only deployments are not spammed with lines that
    carry no numbers. Never raises.
    """
    try:
        snapshot = vram_snapshot()
        if snapshot is None:
            logger.debug("vram stage=%s free_mb=unavailable", stage)
            return
        free, total = snapshot
        logger.info(
            "vram stage=%s free_mb=%d total_mb=%d",
            stage,
            free // (1024 * 1024),
            total // (1024 * 1024),
        )
    except Exception:  # telemetry must never break a stage
        pass


def _ollama_api_root(base_url: str) -> str:
    """The native Ollama API root for a configured base URL.

    The app configures the OpenAI-compatible endpoint (``.../v1``);
    the native API (``/api/generate``) lives at the root.
    """
    root = (base_url or "").rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def _default_unload_model_name(config) -> Optional[str]:
    """The local model an unload request should name, from config.

    Only a direct ``ollama:<model>`` route names the model outright.
    The gateway alias (``openai-chat:local-qwen``) hides its backing model,
    but the direct fallback's default names that same model, so the
    default deployment resolves here; a deployment that blanked every
    direct route gets None (no request is made).
    """
    for candidate in (
        getattr(config, "analysis_direct_fallback_model", None),
        getattr(config, "local_llm", None),
    ):
        if candidate and candidate.strip().lower().startswith("ollama:"):
            return candidate.split(":", 1)[1].strip()
    return None


def request_ollama_unload(model: Optional[str] = None) -> bool:
    """Ask the host Ollama process to unload a model (``keep_alive: 0``).

    Ollama runs OUTSIDE the app's containers on the host and holds
    ~5.5 GB for qwen3:8b on its own schedule; the app cannot evict it,
    only ask. A POST to ``/api/generate`` with the model name and
    ``keep_alive: 0`` is Ollama's documented unload request. The same
    host process backs both the LiteLLM gateway's local-qwen alias and
    the direct ollama route, so one request serves both.

    Returns True only when Ollama answered 200. Every other outcome —
    refused, model not loaded, Ollama stopped, network unreachable,
    no model name derivable from config — returns False. NEVER raises:
    callers fire this at stage seams where the local model's VRAM is
    about to matter (before transcription) or has just stopped
    mattering (after a local-LLM stage), and a failed courtesy must
    not fail the stage.
    """
    try:
        config = get_config()
        name = model or _default_unload_model_name(config)
        if not name:
            return False
        root = _ollama_api_root(config.resolve_ollama_base_url())
        response = httpx.post(
            f"{root}/api/generate",
            json={"model": name, "keep_alive": 0},
            timeout=OLLAMA_UNLOAD_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            logger.info("Requested Ollama unload of %s (keep_alive=0)", name)
            return True
        logger.debug(
            "Ollama unload of %s answered HTTP %s", name, response.status_code
        )
        return False
    except Exception:
        logger.debug("Ollama unload request failed (best-effort)", exc_info=True)
        return False
