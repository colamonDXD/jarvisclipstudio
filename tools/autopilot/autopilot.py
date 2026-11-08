#!/usr/bin/env python3
"""Jarvis Autopilot - watch-folder queue for JarvisClipStudio.

Drop video files (or .txt files listing video URLs) into an INBOX folder and
this script feeds them to a running JarvisClipStudio/SupoClip instance through
its public REST API - the same endpoints the web UI uses - then files the
source away when the task finishes:

    <root>/INBOX       drop videos / URL lists here
    <root>/PROCESSING  items currently being processed by the app
    <root>/COMPLETED   sources whose tasks completed (+ <name>.result.json)
    <root>/FAILED      sources whose tasks failed    (+ <name>.error.json)
    <root>/ARCHIVE     spare folder for your own long-term storage

Progress is persisted to <root>/state.json (atomic writes), so killing the
script - or the whole PC - mid-task is safe: on the next start, items in
PROCESSING with known task ids resume polling, and items with no recorded
task are returned to INBOX for a clean retry.

Python 3 standard library only. No pip installs. See docs/autopilot.md.

API contract (verified against this repository):
  * Upload a file:  POST /media/upload  (multipart form field "video")
      -> {"video_path": "upload://<uuid>.<ext>"}
      backend/src/api/routes/media.py::upload_video
  * Create a task:  POST /tasks/  with JSON {"source": {"url": ...}, ...}
      -> {"task_id": ..., "job_id": ..., "message": ...}
      backend/src/api/routes/tasks.py::create_task ; payload shape mirrors
      frontend/src/components/home-app.tsx and mcp/src/supoclip_mcp/server.py
  * Poll a task:    GET /tasks/{task_id}  -> {"status": ..., "progress": ...,
      "progress_message": ..., "clips": [...]}
  * List clips:     GET /tasks/{task_id}/clips
      -> {"task_id": ..., "clips": [...], "total_clips": int}
  * Auth: an API key created in the app (Settings -> API Keys), sent as
      "Authorization: Bearer <key>" (backend/src/auth_headers.py). A bare
      "x-supoclip-user-id" header is accepted only when the backend runs
      with unsigned auth allowed.
  * Task statuses (mcp/src/supoclip_mcp/server.py TERMINAL_STATES and the
      SSE progress route in tasks.py): terminal = completed / error /
      cancelled; active = pending / queued / processing. Anything else is
      treated as "still working" - an unknown status NEVER finishes an item.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import logging
import mimetypes
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

# --------------------------------------------------------------------------
# Constants mirrored from the app (see module docstring for sources)
# --------------------------------------------------------------------------

SUBFOLDERS = ("INBOX", "PROCESSING", "COMPLETED", "FAILED", "ARCHIVE")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}
URL_LIST_EXTENSION = ".txt"

STATUS_COMPLETED = "completed"
FAILED_STATUSES = {"error", "cancelled"}

# backend/src/clip_duration.py::CLIP_DURATIONS
CLIP_LENGTHS = {"auto", "short", "medium", "long"}
# backend/src/media/common.py::VALID_OUTPUT_FORMATS
OUTPUT_FORMATS = {"vertical", "vertical_fit", "vertical_pan", "vertical_split", "original"}
# backend/src/api/routes/tasks.py::_create_task
PROCESSING_MODES = {"fast", "balanced", "quality"}
# backend/src/auto_director/niche.py::NICHE_VALUES
NICHE_VALUES = {
    "podcast_news", "travel_vlog", "meme_comedy", "gaming", "sports",
    "education_tutorial", "finance_business", "vlog_lifestyle", "true_crime",
    "hindi_podcast", "music_artist", "football_edit", "japan_travel_cinematic",
    "anime_funk_edit",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "api_url": "http://127.0.0.1:8000",
    "api_key": "",
    "user_id": "",
    "poll_interval_seconds": 15,
    "task_poll_interval_seconds": 20,
    "max_parallel": 1,
    "task": {
        "niche_override": None,
        "music_override": None,
        "clip_length": "auto",
        "output_format": "vertical",
        "processing_mode": "fast",
        "add_subtitles": True,
        "caption_template": "default",
        "include_broll": False,
        "cut_long_pauses": True,
        "remove_filler_words": True,
        "pause_threshold_ms": 1200,
        "filtered_words": [],
    },
}

log = logging.getLogger("autopilot")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Pure helpers (unit-tested, no I/O beyond hashing a file)
# --------------------------------------------------------------------------

def decide_task_outcome(status: Any) -> str:
    """Map an API task status to 'completed' | 'failed' | 'wait'.

    Only the exact terminal statuses from the app finish an item. Unknown or
    ambiguous values (including a missing status) mean "keep waiting".
    """
    if status == STATUS_COMPLETED:
        return "completed"
    if status in FAILED_STATUSES:
        return "failed"
    return "wait"


def parse_url_list(text: str) -> list[str]:
    """Extract http(s) URLs from a .txt file: one per line, '#' = comment."""
    urls: list[str] = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if candidate.lower().startswith(("http://", "https://")):
            urls.append(candidate)
    return urls


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_candidates(folder: Path) -> list[Path]:
    """Pickup candidates in a folder, alphabetical, videos and URL lists."""
    if not folder.is_dir():
        return []
    candidates = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        suffix = entry.suffix.lower()
        if suffix in VIDEO_EXTENSIONS or suffix == URL_LIST_EXTENSION:
            candidates.append(entry)
    return candidates


def build_task_payload(source_url: str, title: Optional[str], task_cfg: dict[str, Any]) -> dict[str, Any]:
    """The exact JSON body POST /tasks/ accepts (mirrors the web UI payload)."""
    source: dict[str, Any] = {"url": source_url}
    if title:
        source["title"] = title
    payload: dict[str, Any] = {
        "source": source,
        "caption_template": task_cfg.get("caption_template") or "default",
        "processing_mode": task_cfg.get("processing_mode", "fast"),
        "output_format": task_cfg.get("output_format", "vertical"),
        "clip_length": task_cfg.get("clip_length", "auto"),
        "add_subtitles": bool(task_cfg.get("add_subtitles", True)),
        "include_broll": bool(task_cfg.get("include_broll", False)),
        "cut_long_pauses": bool(task_cfg.get("cut_long_pauses", True)),
        "pause_threshold_ms": task_cfg.get("pause_threshold_ms", 1200),
        "remove_filler_words": bool(task_cfg.get("remove_filler_words", True)),
        "filtered_words": list(task_cfg.get("filtered_words") or []),
        "niche_override": task_cfg.get("niche_override"),
    }
    music_override = task_cfg.get("music_override")
    if music_override:
        payload["music_override"] = music_override
    return payload


def load_settings(path: Optional[Path]) -> tuple[dict[str, Any], list[str]]:
    """Load settings.json over the defaults, validating against the app enums.

    Returns (settings, warnings). Invalid values fall back to the app's own
    defaults (the same silent-defaulting the backend applies where it can).
    """
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    warnings: list[str] = []
    if path is not None and path.is_file():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Could not read settings file {path}: {exc}. Using defaults.")
            user = {}
        if isinstance(user, dict):
            for key, value in user.items():
                if key == "task" and isinstance(value, dict):
                    settings["task"].update(value)
                elif key in settings:
                    settings[key] = value
                else:
                    warnings.append(f"Ignoring unknown settings key '{key}'.")
    elif path is not None:
        warnings.append(f"Settings file {path} not found. Using defaults; "
                        "copy settings.example.json to settings.json and add your API key.")

    env_key = os.environ.get("SUPOCLIP_API_KEY", "").strip()
    if env_key:
        settings["api_key"] = env_key
    settings["api_url"] = str(settings.get("api_url") or DEFAULT_SETTINGS["api_url"]).rstrip("/")

    try:
        settings["max_parallel"] = int(settings.get("max_parallel", 1))
    except (TypeError, ValueError):
        settings["max_parallel"] = 1
    if not 1 <= settings["max_parallel"] <= 3:
        warnings.append("max_parallel must be 1-3; clamped.")
        settings["max_parallel"] = max(1, min(3, settings["max_parallel"]))
    for key in ("poll_interval_seconds", "task_poll_interval_seconds"):
        try:
            settings[key] = max(1, int(settings.get(key, DEFAULT_SETTINGS[key])))
        except (TypeError, ValueError):
            settings[key] = DEFAULT_SETTINGS[key]

    task = settings["task"]
    if task.get("clip_length") not in CLIP_LENGTHS:
        warnings.append(f"task.clip_length {task.get('clip_length')!r} is not one of "
                        f"{sorted(CLIP_LENGTHS)}; using 'auto'.")
        task["clip_length"] = "auto"
    if task.get("output_format") not in OUTPUT_FORMATS:
        warnings.append(f"task.output_format {task.get('output_format')!r} is invalid; using 'vertical'.")
        task["output_format"] = "vertical"
    if task.get("processing_mode") not in PROCESSING_MODES:
        warnings.append(f"task.processing_mode {task.get('processing_mode')!r} is invalid; using 'fast'.")
        task["processing_mode"] = "fast"
    niche = task.get("niche_override")
    if niche is not None and niche not in NICHE_VALUES:
        warnings.append(f"task.niche_override {niche!r} is not a known niche; using null (auto-detect).")
        task["niche_override"] = None
    try:
        task["pause_threshold_ms"] = max(250, min(3000, int(task.get("pause_threshold_ms", 1200))))
    except (TypeError, ValueError):
        task["pause_threshold_ms"] = 1200
    if not isinstance(task.get("filtered_words"), list):
        task["filtered_words"] = []
    return settings, warnings


# --------------------------------------------------------------------------
# API client (stdlib http.client; streaming multipart for large uploads)
# --------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _detail_from_body(body: bytes) -> str:
    try:
        parsed = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return body.decode("utf-8", "replace")[:500]
    if isinstance(parsed, dict):
        detail = parsed.get("detail", parsed)
        if isinstance(detail, dict):
            return str(detail.get("message") or detail)
        return str(detail)
    return str(parsed)[:500]


class ApiClient:
    """Thin client for the SupoClip/JarvisClipStudio backend REST API."""

    def __init__(self, base_url: str, api_key: str = "", user_id: str = "",
                 timeout: float = 60.0, upload_timeout: float = 3600.0):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"Invalid api_url: {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self._scheme = parts.scheme
        self._host = parts.hostname
        self._port = parts.port or (443 if parts.scheme == "https" else 80)
        self._prefix = parts.path.rstrip("/")
        self.api_key = api_key.strip()
        self.user_id = user_id.strip()
        self.timeout = timeout
        self.upload_timeout = upload_timeout

    def _connect(self, timeout: float) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        return cls(self._host, self._port, timeout=timeout)

    def _auth_headers(self) -> dict[str, str]:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        if self.user_id:
            return {"x-supoclip-user-id": self.user_id}
        return {}

    def _request_json(self, method: str, path: str, payload: Optional[dict] = None,
                      extra_headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", **self._auth_headers()}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        conn = self._connect(self.timeout)
        try:
            conn.request(method, self._prefix + path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise ApiError(f"Could not reach the app at {self.base_url}: {exc}") from exc
        finally:
            conn.close()
        if response.status >= 400:
            raise ApiError(
                f"{method} {path} failed ({response.status}): {_detail_from_body(raw)}",
                status=response.status,
            )
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError:
            return {"raw": raw.decode("utf-8", "replace")}
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    def upload_video(self, file_path: Path, idempotency_key: Optional[str] = None) -> str:
        """POST /media/upload (multipart, field 'video') -> 'upload://...' path."""
        boundary = f"----jarvis-autopilot-{uuid.uuid4().hex}"
        safe_name = file_path.name.replace('"', "").replace("\\", "")
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="video"; filename="{safe_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        total = len(head) + file_path.stat().st_size + len(tail)

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(total),
            **self._auth_headers(),
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        conn = self._connect(self.upload_timeout)
        try:
            conn.putrequest("POST", self._prefix + "/media/upload")
            for key, value in headers.items():
                conn.putheader(key, value)
            conn.endheaders()
            conn.send(head)
            with file_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    conn.send(chunk)
            conn.send(tail)
            response = conn.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise ApiError(f"Upload of {file_path.name} failed: {exc}") from exc
        finally:
            conn.close()
        if response.status >= 400:
            raise ApiError(
                f"Upload of {file_path.name} failed ({response.status}): {_detail_from_body(raw)}",
                status=response.status,
            )
        parsed = json.loads(raw.decode("utf-8"))
        video_path = parsed.get("video_path")
        if not isinstance(video_path, str) or not video_path:
            raise ApiError(f"Upload of {file_path.name} returned no video_path: {parsed!r}")
        return video_path

    def create_task(self, source_url: str, title: Optional[str], task_cfg: dict[str, Any],
                    idempotency_key: Optional[str] = None) -> dict[str, Any]:
        """POST /tasks/ -> {"task_id", "job_id", "message"}."""
        payload = build_task_payload(source_url, title, task_cfg)
        extra = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        result = self._request_json("POST", "/tasks/", payload, extra_headers=extra)
        if not result.get("task_id"):
            raise ApiError(f"Task creation returned no task_id: {result!r}")
        return result

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/tasks/{task_id}")

    def get_clips(self, task_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/tasks/{task_id}/clips")


# --------------------------------------------------------------------------
# Persistent state (state.json, atomic writes)
# --------------------------------------------------------------------------

class StateStore:
    """Item records keyed by file name; saved atomically (tmp + os.replace)."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"version": 1, "items": {}}

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict) and isinstance(parsed.get("items"), dict):
                self.data = parsed
                return
            raise ValueError("state.json has an unexpected shape")
        except (OSError, ValueError) as exc:
            backup = self.path.with_name(f"state.json.bak-{int(time.time())}")
            try:
                shutil.copy2(self.path, backup)
            except OSError:
                backup = None
            log.error("state.json was unreadable (%s); starting fresh%s.",
                      exc, f"; backup saved to {backup.name}" if backup else "")
            self.data = {"version": 1, "items": {}}

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def items(self) -> dict[str, dict[str, Any]]:
        return self.data["items"]

    def put(self, item: dict[str, Any]) -> None:
        item["updated_at"] = utc_now()
        self.items[item["name"]] = item
        self.save()

    def remove(self, name: str) -> None:
        if name in self.items:
            del self.items[name]
            self.save()


# --------------------------------------------------------------------------
# The watcher / state machine
# --------------------------------------------------------------------------

class Autopilot:
    def __init__(self, root: Path, settings: dict[str, Any], api: ApiClient):
        self.root = Path(root)
        self.settings = settings
        self.api = api
        self.state = StateStore(self.root / "state.json")

    # -- folders ----------------------------------------------------------
    def folder(self, name: str) -> Path:
        return self.root / name

    def ensure_folders(self) -> None:
        for name in SUBFOLDERS:
            self.folder(name).mkdir(parents=True, exist_ok=True)

    def _unique_dest(self, dest_dir: Path, filename: str) -> Path:
        dest = dest_dir / filename
        counter = 2
        while dest.exists():
            dest = dest_dir / f"{Path(filename).stem} ({counter}){Path(filename).suffix}"
            counter += 1
        return dest

    def _move(self, src: Path, dest_dir: Path) -> Path:
        dest = self._unique_dest(dest_dir, src.name)
        shutil.move(str(src), str(dest))
        return dest

    # -- crash recovery -----------------------------------------------------
    def recover(self) -> None:
        """Reconcile state.json with what is actually on disk. Run once at start.

        - PROCESSING file with recorded task ids -> resume polling it.
        - PROCESSING file with no recorded tasks (crashed mid-submission, or
          dropped there by hand) -> back to INBOX for a safe retry.
        - State item whose file already sits in COMPLETED/FAILED -> mark it
          finished to match reality; file nowhere -> mark 'missing'.
        """
        self.ensure_folders()
        self.state.load()
        processing = {p.name: p for p in self.folder("PROCESSING").iterdir()
                      if p.is_file()} if self.folder("PROCESSING").is_dir() else {}

        for name, path in sorted(processing.items()):
            item = self.state.items.get(name)
            if item and item.get("tasks"):
                log.info("Recovery: %s has %d recorded task(s); resuming.", name, len(item["tasks"]))
                item["folder"] = "PROCESSING"
                if item.get("stage") not in ("awaiting", "submitting"):
                    item["stage"] = "awaiting"
                self.state.put(item)
            else:
                dest = self._move(path, self.folder("INBOX"))
                log.info("Recovery: %s had no recorded task; returned to INBOX as %s "
                         "(safe retry).", name, dest.name)
                if item:
                    self.state.remove(name)

        for name, item in list(self.state.items.items()):
            if item.get("folder") != "PROCESSING" or name in processing:
                continue
            for folder_name, stage in (("COMPLETED", "completed"), ("FAILED", "failed")):
                if (self.folder(folder_name) / name).is_file():
                    log.info("Recovery: %s already in %s; marking %s.", name, folder_name, stage)
                    item["folder"] = folder_name
                    item["stage"] = stage
                    item["finished_at"] = item.get("finished_at") or utc_now()
                    self.state.put(item)
                    break
            else:
                if (self.folder("INBOX") / name).is_file():
                    log.info("Recovery: %s is back in INBOX; dropping stale record.", name)
                    self.state.remove(name)
                elif item.get("stage") != "missing":
                    log.warning("Recovery: %s is recorded but missing from disk; "
                                "marking it 'missing'.", name)
                    item["stage"] = "missing"
                    self.state.put(item)
        self.state.save()

    # -- main cycle ---------------------------------------------------------
    def active_items(self) -> list[dict[str, Any]]:
        return [item for item in self.state.items.values()
                if item.get("folder") == "PROCESSING"
                and item.get("stage") in ("awaiting", "submitting")]

    def run_cycle(self) -> None:
        """One scan cycle: continue submissions, poll tasks, pick up new items."""
        self.ensure_folders()
        self._continue_submissions()
        self._poll_active()
        self._pickup()

    # -- submission ---------------------------------------------------------
    def _pickup(self) -> None:
        capacity = int(self.settings["max_parallel"]) - len(self.active_items())
        if capacity <= 0:
            return
        for candidate in scan_candidates(self.folder("INBOX")):
            if capacity <= 0:
                break
            moved = self._move(candidate, self.folder("PROCESSING"))
            kind = "url_list" if moved.suffix.lower() == URL_LIST_EXTENSION else "video"
            item: dict[str, Any] = {
                "name": moved.name,
                "kind": kind,
                "folder": "PROCESSING",
                "stage": "submitting",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "finished_at": None,
                "settings": dict(self.settings["task"]),
                "tasks": [],
                "error": None,
            }
            if kind == "url_list":
                try:
                    item["urls"] = parse_url_list(moved.read_text(encoding="utf-8", errors="replace"))
                except OSError as exc:
                    item["urls"] = []
                    item["error"] = f"Could not read URL list: {exc}"
            self.state.put(item)
            log.info("Picked up %s (%s) from INBOX.", moved.name, kind)
            capacity -= 1
            self._continue_submission(item)

    def _continue_submissions(self) -> None:
        for item in self.active_items():
            if item.get("stage") == "submitting":
                self._continue_submission(item)

    def _continue_submission(self, item: dict[str, Any]) -> None:
        path = self.folder("PROCESSING") / item["name"]
        if item["kind"] == "video":
            self._submit_video(item, path)
        else:
            self._submit_url_list(item, path)

    def _submit_video(self, item: dict[str, Any], path: Path) -> None:
        if item["tasks"]:  # already created (paranoia; videos have one task)
            item["stage"] = "awaiting"
            self.state.put(item)
            return
        try:
            if not item.get("idempotency_key"):
                # Content-derived key: a crash/retry of the same file reuses the
                # stored upload and the already-queued task on the server side.
                item["idempotency_key"] = f"autopilot-{file_sha256(path)}"
            if not item.get("video_path"):
                log.info("Uploading %s ...", item["name"])
                item["video_path"] = self.api.upload_video(path, item["idempotency_key"])
                self.state.put(item)
                log.info("Uploaded %s as %s.", item["name"], item["video_path"])
            result = self.api.create_task(item["video_path"], item["name"],
                                          item["settings"], item["idempotency_key"])
        except (ApiError, OSError) as exc:
            self._fail_submission(item, path, str(exc))
            return
        item["tasks"] = [{
            "task_id": result["task_id"], "url": item["video_path"],
            "status": "queued", "progress": 0, "message": "",
        }]
        item["stage"] = "awaiting"
        self.state.put(item)
        log.info("Created task %s for %s.", result["task_id"], item["name"])

    def _submit_url_list(self, item: dict[str, Any], path: Path) -> None:
        urls: list[str] = item.get("urls") or []
        if not urls:
            self._fail_submission(item, path, "No valid http(s) URLs found in the list file.")
            return
        created = {t.get("url") for t in item["tasks"]}
        for url in urls:
            if url in created:
                continue
            try:
                result = self.api.create_task(url, None, item["settings"])
            except ApiError as exc:
                self._fail_submission(
                    item, path,
                    f"Task creation failed for {url}: {exc} "
                    f"({len(item['tasks'])} task(s) had already been created and are "
                    "recorded in state.json.)")
                return
            item["tasks"].append({
                "task_id": result["task_id"], "url": url,
                "status": "queued", "progress": 0, "message": "",
            })
            self.state.put(item)
            log.info("Created task %s for URL %s (%s).",
                     result["task_id"], url, item["name"])
        item["stage"] = "awaiting"
        self.state.put(item)

    def _fail_submission(self, item: dict[str, Any], path: Path, error: str) -> None:
        item["error"] = error
        log.error("Submission failed for %s: %s", item["name"], error)
        self._finish(item, path, outcome="failed")

    # -- polling --------------------------------------------------------------
    def _poll_active(self) -> None:
        for item in self.active_items():
            if item.get("stage") != "awaiting":
                continue
            path = self.folder("PROCESSING") / item["name"]
            for task in item["tasks"]:
                if decide_task_outcome(task.get("status")) != "wait":
                    continue  # already terminal from an earlier cycle
                try:
                    info = self.api.get_task(task["task_id"])
                except ApiError as exc:
                    # Network/server hiccup (or the task vanished): never guess.
                    log.warning("Polling task %s failed (%s); will retry next cycle.",
                                task["task_id"], exc)
                    continue
                new_status = info.get("status")
                if new_status != task.get("status"):
                    log.info("Task %s (%s): %s -> %s (%s%%)",
                             task["task_id"], item["name"], task.get("status"),
                             new_status, info.get("progress", 0))
                task["status"] = new_status
                task["progress"] = info.get("progress", task.get("progress", 0))
                task["message"] = info.get("progress_message") or ""
            self.state.put(item)
            # Decide only once EVERY task of the item is terminal: a failed
            # sibling must not orphan a task that is still rendering.
            outcomes = [decide_task_outcome(t.get("status")) for t in item["tasks"]]
            if not item["tasks"] or any(o == "wait" for o in outcomes):
                continue
            if any(o == "failed" for o in outcomes):
                failed = next(t for t in item["tasks"]
                              if decide_task_outcome(t.get("status")) == "failed")
                item["error"] = failed.get("message") or f"Task {failed['task_id']} {failed.get('status')}"
                self._finish(item, path, outcome="failed")
            else:
                self._finish(item, path, outcome="completed")

    # -- finishing ------------------------------------------------------------
    def _collect_results(self, item: dict[str, Any]) -> dict[str, Any]:
        results = []
        for task in item["tasks"]:
            entry: dict[str, Any] = {
                "task_id": task["task_id"],
                "url": task.get("url"),
                "clip_count": 0,
                "clips": [],
                "clip_file_urls": [],
            }
            try:
                clips_payload = self.api.get_clips(task["task_id"])
                clips = clips_payload.get("clips") or []
                entry["clips"] = clips
                entry["clip_count"] = clips_payload.get("total_clips", len(clips))
                entry["clip_file_urls"] = [
                    f"{self.api.base_url}/tasks/{task['task_id']}/clips/{clip.get('id')}/file"
                    for clip in clips if clip.get("id")
                ]
            except ApiError as exc:
                entry["clips_error"] = str(exc)
            results.append(entry)
        return {
            "item": item["name"],
            "kind": item["kind"],
            "status": "completed",
            "submitted_at": item.get("created_at"),
            "finished_at": utc_now(),
            "settings": item.get("settings"),
            "results": results,
            "clip_count": sum(r["clip_count"] for r in results),
        }

    def _finish(self, item: dict[str, Any], path: Path, outcome: str) -> None:
        if outcome == "completed":
            payload = self._collect_results(item)
            dest_dir, suffix = self.folder("COMPLETED"), ".result.json"
        else:
            payload = {
                "item": item["name"],
                "kind": item["kind"],
                "status": "failed",
                "submitted_at": item.get("created_at"),
                "finished_at": utc_now(),
                "settings": item.get("settings"),
                "error": item.get("error") or "Task failed.",
                "tasks": [
                    {"task_id": t["task_id"], "url": t.get("url"),
                     "status": t.get("status"), "message": t.get("message", "")}
                    for t in item["tasks"]
                ],
            }
            dest_dir, suffix = self.folder("FAILED"), ".error.json"
        moved = self._move(path, dest_dir) if path.is_file() else dest_dir / item["name"]
        sidecar = dest_dir / (moved.name + suffix)
        sidecar.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        item["folder"] = dest_dir.name
        item["stage"] = outcome
        item["finished_at"] = utc_now()
        self.state.put(item)
        log.info("%s %s: %s -> %s (details in %s)",
                 item["kind"], item["name"], outcome.upper(), moved.name, sidecar.name)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _setup_logging(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(root / "autopilot.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(file_handler)
    log.addHandler(stream_handler)


def default_root() -> Path:
    env = os.environ.get("JARVIS_AUTOPILOT_ROOT", "").strip()
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(r"D:\Jarvis_Autopilot")
    return Path.home() / "Jarvis_Autopilot"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Jarvis Autopilot: watch-folder queue for JarvisClipStudio.")
    parser.add_argument("root", nargs="?", default=None,
                        help=r"Autopilot root folder (default: D:\Jarvis_Autopilot on Windows)")
    parser.add_argument("--settings", default=None,
                        help="Path to settings.json (default: settings.json beside this script)")
    parser.add_argument("--api-url", default=None, help="Override the app API base URL")
    parser.add_argument("--api-key", default=None,
                        help="Override the API key (or set SUPOCLIP_API_KEY)")
    parser.add_argument("--once", action="store_true",
                        help="Run one scan cycle and exit (for testing)")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else default_root()
    _setup_logging(root)

    settings_path = Path(args.settings) if args.settings else Path(__file__).with_name("settings.json")
    settings, warnings = load_settings(settings_path)
    for warning in warnings:
        log.warning(warning)
    if args.api_url:
        settings["api_url"] = args.api_url.rstrip("/")
    if args.api_key:
        settings["api_key"] = args.api_key

    if not settings.get("api_key") and not settings.get("user_id"):
        log.error("No API key configured. Create one in the app (Settings -> API Keys), "
                  "then put it in settings.json beside this script (\"api_key\": \"sk_...\") "
                  "or set the SUPOCLIP_API_KEY environment variable.")
        return 2

    api = ApiClient(settings["api_url"], api_key=settings.get("api_key", ""),
                    user_id=settings.get("user_id", ""))
    autopilot = Autopilot(root, settings, api)
    autopilot.ensure_folders()
    autopilot.recover()
    log.info("Autopilot watching %s (API %s, max_parallel %s).",
             root, settings["api_url"], settings["max_parallel"])

    if args.once:
        autopilot.run_cycle()
        log.info("--once cycle complete.")
        return 0

    try:
        while True:
            try:
                autopilot.run_cycle()
            except Exception:  # a watcher must survive one bad cycle
                log.exception("Cycle failed; continuing.")
            cycle_seconds = min(int(settings["poll_interval_seconds"]),
                                int(settings["task_poll_interval_seconds"]))
            deadline = time.monotonic() + cycle_seconds
            while time.monotonic() < deadline:
                time.sleep(0.25)
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C). State is saved; restart any time to resume.")
        autopilot.state.save()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
