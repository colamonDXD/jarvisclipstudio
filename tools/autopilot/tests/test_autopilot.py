"""Autopilot tests against a stdlib fake of the real backend API.

The fake implements exactly the shapes tools/autopilot/autopilot.py calls:

    POST /media/upload        multipart field "video" -> {"video_path": "upload://..."}
    POST /tasks/              {"source": {"url": ...}, ...} -> {"task_id": ...}
    GET  /tasks/{id}          -> {"status": ..., "progress": ..., "progress_message": ...}
    GET  /tasks/{id}/clips    -> {"task_id": ..., "clips": [...], "total_clips": int}

Status sequences are scripted per source URL (or per upload title) so tests can
drive tasks through processing -> completed / error / unknown states.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import autopilot as ap  # noqa: E402


# ---------------------------------------------------------------------------
# Fake SupoClip backend
# ---------------------------------------------------------------------------

class FakeStore:
    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.scripts: dict[str, list[str]] = {}   # key -> status sequence
        self.error_messages: dict[str, str] = {}  # key -> progress_message on error
        self.default_script = ["completed"]
        self.uploads = 0
        self.created_payloads: list[dict] = []

    def script_for(self, key: str | None) -> list[str]:
        if key and key in self.scripts:
            return list(self.scripts[key])
        return list(self.default_script)


class FakeHandler(BaseHTTPRequestHandler):
    store: FakeStore  # set by the fixture via subclass attribute

    def log_message(self, *args):  # silence
        pass

    def _send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path == "/media/upload":
            assert b'name="video"' in body, "upload must use the 'video' form field"
            assert (self.headers.get("Authorization") or "").startswith("Bearer sk_")
            self.store.uploads += 1
            self._send_json({
                "message": "Video uploaded successfully",
                "video_path": f"upload://fake-{self.store.uploads}.mp4",
            })
            return
        if self.path in ("/tasks/", "/tasks"):
            payload = json.loads(body.decode())
            self.store.created_payloads.append(payload)
            source = payload.get("source") or {}
            key = source.get("url")
            task_id = f"task-{len(self.store.tasks) + 1}"
            self.store.tasks[task_id] = {
                "payload": payload,
                "script": self.store.script_for(key) if key not in (None, "")
                            else self.store.script_for(source.get("title")),
                "error_message": self.store.error_messages.get(key, "It broke."),
                "polls": 0,
            }
            if key and key.startswith("upload://"):
                # Upload tasks are scripted by the original filename (title).
                titled = self.store.script_for(source.get("title"))
                if source.get("title") in self.store.scripts:
                    self.store.tasks[task_id]["script"] = titled
                    self.store.tasks[task_id]["error_message"] = \
                        self.store.error_messages.get(source.get("title"), "It broke.")
            self._send_json({"task_id": task_id, "job_id": f"job-{task_id}",
                             "message": "Task created and queued for processing"})
            return
        self._send_json({"detail": "Not found"}, status=404)

    def do_GET(self):
        parts = [p for p in self.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "tasks":
            task_id = parts[1]
            task = self.store.tasks.get(task_id)
            if task is None:
                self._send_json({"detail": "Task not found"}, status=404)
                return
            if len(parts) == 3 and parts[2] == "clips":
                clip = {
                    "id": "clip-1", "filename": "clip_1.mp4",
                    "file_path": "/app/output/clip_1.mp4",
                    "start_time": 0.0, "end_time": 10.0, "duration": 10.0,
                    "text": "hello world",
                }
                self._send_json({"task_id": task_id, "clips": [clip], "total_clips": 1})
                return
            script = task["script"]
            status = script[min(task["polls"], len(script) - 1)]
            task["polls"] += 1
            self._send_json({
                "id": task_id,
                "status": status,
                "progress": 100 if status == "completed" else 10,
                "progress_message": task["error_message"] if status == "error" else "Working",
                "clips": [],
            })
            return
        self._send_json({"detail": "Not found"}, status=404)


@pytest.fixture()
def fake_api():
    store = FakeStore()
    handler = type("BoundHandler", (FakeHandler,), {"store": store})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield store, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def make_autopilot(tmp_path: Path, api_url: str, **overrides) -> ap.Autopilot:
    settings = json.loads(json.dumps(ap.DEFAULT_SETTINGS))
    settings.update({k: v for k, v in overrides.items() if k != "task"})
    if "task" in overrides:
        settings["task"].update(overrides["task"])
    settings["api_url"] = api_url
    client = ap.ApiClient(api_url, api_key="sk_test_key")
    watcher = ap.Autopilot(tmp_path, settings, client)
    watcher.ensure_folders()
    return watcher


def drive(watcher: ap.Autopilot, cycles: int = 12) -> None:
    for _ in range(cycles):
        watcher.run_cycle()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_decide_task_outcome():
    assert ap.decide_task_outcome("completed") == "completed"
    assert ap.decide_task_outcome("error") == "failed"
    assert ap.decide_task_outcome("cancelled") == "failed"
    for other in ("processing", "queued", "pending", "rendering", "", None, "done"):
        assert ap.decide_task_outcome(other) == "wait"


def test_parse_url_list():
    text = "https://youtu.be/a\n\n# comment\n  https://example.com/v.mp4 \nnot-a-url\n"
    assert ap.parse_url_list(text) == ["https://youtu.be/a", "https://example.com/v.mp4"]


# ---------------------------------------------------------------------------
# End-to-end behaviour against the fake API
# ---------------------------------------------------------------------------

def test_video_pickup_to_completed(tmp_path, fake_api):
    store, url = fake_api
    store.scripts["movie.mp4"] = ["processing", "completed"]
    watcher = make_autopilot(tmp_path, url)
    (tmp_path / "INBOX" / "movie.mp4").write_bytes(b"\x00\x01fakevideo")

    drive(watcher)

    completed = tmp_path / "COMPLETED" / "movie.mp4"
    assert completed.is_file()
    result = json.loads((tmp_path / "COMPLETED" / "movie.mp4.result.json").read_text())
    assert result["status"] == "completed"
    assert result["clip_count"] == 1
    assert result["results"][0]["task_id"] == "task-1"
    assert result["results"][0]["clips"][0]["filename"] == "clip_1.mp4"
    assert result["results"][0]["clip_file_urls"][0].endswith("/tasks/task-1/clips/clip-1/file")
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["items"]["movie.mp4"]["stage"] == "completed"
    # The task was created from the uploaded path, titled with the file name.
    payload = store.created_payloads[0]
    assert payload["source"]["url"].startswith("upload://")
    assert payload["source"]["title"] == "movie.mp4"
    assert payload["clip_length"] == "auto"
    assert payload["output_format"] == "vertical"


def test_video_pickup_to_failed(tmp_path, fake_api):
    store, url = fake_api
    store.scripts["bad.mp4"] = ["processing", "error"]
    store.error_messages["bad.mp4"] = "No speech found in this video."
    watcher = make_autopilot(tmp_path, url)
    (tmp_path / "INBOX" / "bad.mp4").write_bytes(b"\x00\x01fakevideo")

    drive(watcher)

    assert (tmp_path / "FAILED" / "bad.mp4").is_file()
    error = json.loads((tmp_path / "FAILED" / "bad.mp4.error.json").read_text())
    assert error["status"] == "failed"
    assert "No speech found" in error["error"]
    assert error["tasks"][0]["task_id"] == "task-1"


def test_crash_resume_from_state(tmp_path, fake_api):
    store, url = fake_api
    # A task that already exists server-side from before the "crash".
    store.tasks["task-9"] = {
        "payload": {}, "script": ["completed"], "error_message": "x", "polls": 0,
    }
    watcher = make_autopilot(tmp_path, url)
    (tmp_path / "PROCESSING" / "resume.mp4").write_bytes(b"\x00\x01fakevideo")
    state = {
        "version": 1,
        "items": {
            "resume.mp4": {
                "name": "resume.mp4", "kind": "video", "folder": "PROCESSING",
                "stage": "awaiting", "created_at": "2026-11-08T00:00:00+00:00",
                "updated_at": "2026-11-08T00:00:00+00:00", "finished_at": None,
                "settings": dict(ap.DEFAULT_SETTINGS["task"]),
                "tasks": [{"task_id": "task-9", "url": "upload://fake-0.mp4",
                           "status": "processing", "progress": 10, "message": ""}],
                "error": None,
            }
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state))

    watcher.recover()
    assert (tmp_path / "PROCESSING" / "resume.mp4").is_file()  # NOT bounced to INBOX
    drive(watcher)
    assert (tmp_path / "COMPLETED" / "resume.mp4").is_file()
    result = json.loads((tmp_path / "COMPLETED" / "resume.mp4.result.json").read_text())
    assert result["results"][0]["task_id"] == "task-9"
    assert store.uploads == 0  # resumed, never re-uploaded


def test_unknown_processing_file_returns_to_inbox(tmp_path, fake_api):
    store, url = fake_api
    watcher = make_autopilot(tmp_path, url)
    (tmp_path / "PROCESSING" / "orphan.mp4").write_bytes(b"\x00\x01fakevideo")

    watcher.recover()

    assert (tmp_path / "INBOX" / "orphan.mp4").is_file()
    assert not (tmp_path / "PROCESSING" / "orphan.mp4").exists()


def test_url_list_creates_one_task_per_url(tmp_path, fake_api):
    store, url = fake_api
    watcher = make_autopilot(tmp_path, url)
    (tmp_path / "INBOX" / "links.txt").write_text(
        "https://youtube.com/watch?v=aaa\nhttps://youtube.com/watch?v=bbb\n")

    drive(watcher)

    assert len(store.created_payloads) == 2
    urls = {p["source"]["url"] for p in store.created_payloads}
    assert urls == {"https://youtube.com/watch?v=aaa",
                    "https://youtube.com/watch?v=bbb"}
    assert (tmp_path / "COMPLETED" / "links.txt").is_file()
    result = json.loads((tmp_path / "COMPLETED" / "links.txt.result.json").read_text())
    assert result["kind"] == "url_list"
    assert result["clip_count"] == 2
    assert {r["task_id"] for r in result["results"]} == {"task-1", "task-2"}


def test_unknown_status_never_completes(tmp_path, fake_api):
    store, url = fake_api
    store.scripts["weird.mp4"] = ["rendering_weird"]  # sticks forever
    watcher = make_autopilot(tmp_path, url)
    (tmp_path / "INBOX" / "weird.mp4").write_bytes(b"\x00\x01fakevideo")

    drive(watcher, cycles=6)

    assert (tmp_path / "PROCESSING" / "weird.mp4").is_file()
    assert not list((tmp_path / "COMPLETED").iterdir())
    assert not list((tmp_path / "FAILED").iterdir())
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["items"]["weird.mp4"]["stage"] == "awaiting"
    assert state["items"]["weird.mp4"]["tasks"][0]["status"] == "rendering_weird"


def test_max_parallel_one_holds_second_item(tmp_path, fake_api):
    store, url = fake_api
    store.default_script = ["processing"]  # never finishes
    watcher = make_autopilot(tmp_path, url, max_parallel=1)
    (tmp_path / "INBOX" / "a_first.mp4").write_bytes(b"\x00\x01fakevideo")
    (tmp_path / "INBOX" / "b_second.mp4").write_bytes(b"\x00\x01fakevideo")

    drive(watcher, cycles=4)

    assert (tmp_path / "PROCESSING" / "a_first.mp4").is_file()
    assert (tmp_path / "INBOX" / "b_second.mp4").is_file()  # alphabetical, still queued
    assert len(store.created_payloads) == 1
