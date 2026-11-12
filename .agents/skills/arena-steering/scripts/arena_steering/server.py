"""HTTP router and local steering server for Arena Steering."""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import importlib
import importlib.util
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from functools import lru_cache
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

from . import __version__
from .blocked_bash import dismiss_blocked_bash, read_blocked_bash
from .browser_retry import RetryCapacityError
from .display_paths import home_relative
from .gates import FileGateStatus
from .git_safety import DirtyTreeError, completion_tree_check
from .http_transport import make_request_handler
from .markdown import render_markdown
from .models import Attachment, Task
from .services import (
  POLL_FRESH_SECONDS,
  MessageService,
  ReportService,
  TaskService,
  serialize_report_blocks,
  with_bible_read,
)
from .store import STATE_TABLES, SqliteStore

RouteHandler = Callable[[dict[str, str], dict[str, list[str]], Any], tuple[int, Any]]

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
HOT_RELOAD_QUIET_SECONDS = 10.0
WS_TICK_SECONDS = 0.25
WS_HEARTBEAT_SECONDS = 10.0
WS_IDLE_SECONDS = 35.0
WS_MAX_MESSAGE = 16 * 1024 * 1024
REPORT_RENDER_CACHE_LIMIT = 128
MERMAID_CACHE_LIMIT = 128
MERMAID_SVG_LIMIT = 512_000
MERMAID_SOURCE_LIMIT = 50_064
MERMAID_SVG_ELEMENTS = frozenset(
  {
    "svg",
    "g",
    "defs",
    "marker",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
    "tspan",
    "style",
    "title",
    "desc",
    "clipPath",
    "mask",
    "pattern",
    "linearGradient",
    "radialGradient",
    "stop",
    "use",
    "filter",
    "feDropShadow",
    "symbol",
  }
)

MIME_OVERRIDES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".gif": "image/gif",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".woff2": "font/woff2",
  ".webmanifest": "application/manifest+json; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
}
HTTP_CONTENT_TYPE_RE = re.compile(
  r"[!#$%&'*+.^_`|~0-9A-Za-z-]+/[!#$%&'*+.^_`|~0-9A-Za-z-]+"
  r"(?:; charset=[A-Za-z0-9._-]+)?"
)
STATIC_PATH_PART_RE = re.compile(r'[^/\\<>:"|?*\x00-\x1f\x7f]+')

REPORT_IMAGE_EXTS = frozenset({".gif", ".png", ".jpg", ".jpeg", ".webp", ".svg"})
REPORT_STATIC_EXTS = REPORT_IMAGE_EXTS | frozenset({".md", ".txt"})
STATIC_ASSETS_PREFIX = "static/assets/"
STATIC_REPORTS_PREFIX = "static/reports/"
STATIC_UPLOADS_PREFIX = "static/uploads/"
STATE_SCHEMA_VERSION = 1
PASTE_ATTACHMENT_CHAR_THRESHOLD = 1500
PASTE_ATTACHMENT_LINE_THRESHOLD = 15
PASTE_ATTACHMENT_NAME = "pasted-text.txt"
PASTE_ATTACHMENT_BODY = "Pasted text attached."
BIBLE_SCAN_SKIP = {
  ".git",
  ".venv",
  "arena-state",
  "build",
  "dist",
  "node_modules",
  "__pycache__",
}


def _is_blocked_bash_path(url: str) -> bool:
  path = urlsplit(url).path.rstrip("/")
  return path == "/blocked-bash" or path.startswith("/blocked-bash/")


VIOLATION_TYPES = {
  "messages": (
    "bash-violation",
    "process-violation",
    "missing-details",
    "out-of-scope",
    "task-not-created",
  ),
  "reports": ("format-violation", "unnecessary-report", "wall-of-text"),
  "tasks": (
    "current-task-violation",
    "empty-task-list",
    "missing-details",
    "out-of-scope",
    "task-not-created",
  ),
}


def _safe_mermaid_svg(svg: str) -> bool:
  if len(svg.encode("utf-8")) > MERMAID_SVG_LIMIT or "<!" in svg:
    return False
  try:
    root = ET.fromstring(svg)
  except ET.ParseError:
    return False
  if root.tag not in {"svg", "{http://www.w3.org/2000/svg}svg"}:
    return False
  for element in root.iter():
    namespace, _, tag = element.tag.rpartition("}")
    if namespace and namespace != "{http://www.w3.org/2000/svg":
      return False
    if tag not in MERMAID_SVG_ELEMENTS:
      return False
    values = list(element.attrib.values())
    if tag == "style":
      values.append(element.text or "")
    for name, value in element.attrib.items():
      local_name = name.rsplit("}", 1)[-1].lower()
      if local_name.startswith("on") or (
        local_name == "href" and not value.startswith("#")
      ):
        return False
    for value in values:
      if "\\" in value or re.search(
        r"javascript:|vbscript:|data:text/html|@import|expression\s*\(|url\s*\(\s*['\"]?\s*(?!#)",
        value,
        re.IGNORECASE,
      ):
        return False
  return True


def _body_bool(value: Any, default: bool) -> bool:
  if isinstance(value, bool):
    return value
  if isinstance(value, str):
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
      return True
    if lowered in {"false", "0", "no", "off", ""}:
      return False
  if value is None:
    return default
  return bool(value)


class Router:
  """Small regex-based HTTP route dispatcher."""

  def __init__(self) -> None:
    self._routes: list[tuple[str, re.Pattern[str], RouteHandler]] = []

  def add(self, method: str, template: str, handler: RouteHandler) -> None:
    pattern_text = re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", r"(?P<\1>[^/]+)", template)
    compiled = re.compile(f"^{pattern_text}$")
    self._routes.append((method.upper(), compiled, handler))

  def match(
    self, method: str, path: str
  ) -> tuple[RouteHandler | None, dict[str, str], list[str]]:
    verb = method.upper()
    allowed: list[str] = []
    for route_method, pattern, handler in self._routes:
      found = pattern.match(path)
      if found is not None:
        if route_method == verb:
          return handler, found.groupdict(), []
        allowed.append(route_method)
    return None, {}, allowed


def _detect_repo_root(store: SqliteStore) -> Path:
  """Find the checkout from the state database or working directory."""
  candidates: list[Path] = []
  db_path = getattr(store, "db_path", "")
  if db_path and db_path != ":memory:":
    candidates.append(Path(db_path).resolve())
  candidates.append(Path.cwd().resolve())
  for start in candidates:
    cur = start if start.is_dir() else start.parent
    for parent in (cur, *cur.parents):
      if (parent / ".git").exists():
        return parent
  return Path.cwd().resolve()


@lru_cache(maxsize=4)
def _read_set_files(root: Path) -> tuple[str, ...]:
  """List the clause files in the checkout bible."""
  files: list[str] = []
  if (root / "ARENA.md").is_file():
    files.append("ARENA.md")
  for directory, names, filenames in os.walk(root, followlinks=False):
    names[:] = sorted(name for name in names if name not in BIBLE_SCAN_SKIP)
    if "AGENTS.md" in filenames:
      path = Path(directory) / "AGENTS.md"
      if path.is_file() and path.resolve().is_relative_to(root):
        files.append(path.relative_to(root).as_posix())
  name = ".agents/skills/arena-steering/SKILL.md"
  if (root / name).is_file():
    files.append(name)
  return tuple(files)


def _detect_repo_name(store: SqliteStore) -> str:
  return _detect_repo_root(store).name or "Workspace"


class SteeringApp:
  """Application facade wiring services and REST endpoints."""

  def __init__(
    self,
    store: SqliteStore,
    assets_dir: str | Path | None = None,
    repo_name: str | None = None,
  ) -> None:
    self.store = store
    self._request_lock = threading.Lock()
    self._active_requests = 0
    self._retired = False
    self._mermaid_cache: OrderedDict[str, str] = OrderedDict()
    self._mermaid_cache_dir = (
      Path(store.db_path).parent / "mermaid-cache"
      if store.db_path != ":memory:"
      else None
    )
    self._mermaid_lock = threading.Lock()
    self._browser_write = threading.local()
    self._clean_uncommitted_uploads()
    default_assets = Path(__file__).resolve().parents[2] / "assets"
    self.assets_dir = Path(assets_dir) if assets_dir is not None else default_assets
    initial_assets = assets_signature(self.assets_dir)
    self._asset_tracker = {
      "lock": threading.Lock(),
      "observed": initial_assets,
      "stable": initial_assets,
      "changed_at": None,
    }
    self.repo_name = repo_name or _detect_repo_name(store)
    self._read_set = _read_set_files(_detect_repo_root(store))
    uploads_dir = self._uploads_dir()
    self.messages = MessageService(store, store, store, store, uploads_dir=uploads_dir)
    state_dir = Path(store.db_path).parent if store.db_path != ":memory:" else None
    tree_check = completion_tree_check(state_dir) if state_dir is not None else None
    self.tasks = TaskService(
      store,
      store,
      messages=store,
      gates=FileGateStatus(state_dir) if state_dir is not None else None,
      clean_tree_verifier=tree_check,
      on_dirty_finish=self.messages.flag_dirty_finish
      if tree_check is not None
      else None,
      uploads_dir=uploads_dir,
    )
    self.reports = ReportService(
      store,
      store,
      on_answer_callback=self.messages.notify_new_work,
      on_ack_callback=self.messages.reset_gate_if_clear,
      messages=store,
    )
    self.router = Router()
    self._register_routes()
    self._maybe_reseed_demo(store)

  def _begin_request(self) -> bool:
    with self._request_lock:
      if self._retired:
        return False
      self._active_requests += 1
      return True

  def _end_request(self) -> None:
    with self._request_lock:
      self._active_requests -= 1
      if self._retired and not self._active_requests:
        self.store.close()

  def _retire_store(self) -> None:
    with self._request_lock:
      if self._retired:
        return
      self._retired = True
      if not self._active_requests:
        self.store.close()

  @staticmethod
  def _maybe_reseed_demo(store: SqliteStore) -> None:
    """Re-seed a repo demo SQLite store when its script changes on disk."""
    db_path_str = str(getattr(store, "db_path", ""))
    demo_script = (
      Path(__file__).resolve().parents[4] / "maintenance" / "demo-arena-steer.py"
    )
    if (
      "arena-steering-demo-" not in db_path_str
      or not demo_script.is_file()
      or getattr(store, "_seeding_demo", False)
    ):
      return
    try:
      mtime = demo_script.stat().st_mtime_ns
    except OSError:
      return
    if getattr(store, "_demo_mtime_ns", None) == mtime:
      return
    store._seeding_demo = True  # type: ignore[attr-defined]
    store._demo_mtime_ns = mtime  # type: ignore[attr-defined]
    try:
      spec = importlib.util.spec_from_file_location("_arena_steering_demo", demo_script)
      if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "reseed_demo_store"):
          mod.reseed_demo_store(store)
    finally:
      store._seeding_demo = False  # type: ignore[attr-defined]

  def _register_routes(self) -> None:
    self.router.add("GET", "/health", self._handle_health)
    self.router.add("GET", "/blocked-bash", self._handle_blocked_bash_log)
    self.router.add("DELETE", "/blocked-bash/{id}", self._handle_dismiss_blocked_bash)
    self.router.add("GET", "/messages", self._handle_list_messages)
    self.router.add("POST", "/messages", self._handle_create_message)
    self.router.add(
      "POST", "/messages/with-file", self._handle_create_message_with_file
    )
    self.router.add("POST", "/browser-write-token", self._handle_browser_write_token)
    self.router.add("POST", "/uploads", self._handle_upload_file)
    self.router.add("GET", "/messages/hidden", self._handle_hidden_message_window)
    self.router.add("GET", "/messages/{id}", self._handle_get_message)
    self.router.add("POST", "/messages/{id}/ack", self._handle_ack_message)
    self.router.add("POST", "/messages/{id}/replies-seen", self._handle_replies_seen)
    self.router.add("GET", "/poll", self._handle_poll)
    self.router.add("POST", "/poll/skip", self._handle_skip_poll)
    self.router.add("DELETE", "/poll/skip", self._handle_cancel_skip_poll)

    self.router.add("GET", "/tasks", self._handle_list_tasks)
    self.router.add("POST", "/tasks", self._handle_create_task)
    self.router.add("GET", "/tasks/{id}", self._handle_get_task)
    self.router.add("PATCH", "/tasks/{id}", self._handle_patch_task)
    self.router.add("DELETE", "/tasks/{id}", self._handle_delete_task)

    self.router.add("GET", "/reports", self._handle_list_reports)
    self.router.add("POST", "/reports", self._handle_publish_report)
    self.router.add("GET", "/reports/{id}", self._handle_get_report)
    self.router.add("PATCH", "/reports/{id}", self._handle_patch_report)
    self.router.add("DELETE", "/reports/{id}", self._handle_delete_report)
    self.router.add("POST", "/reports/{id}/seen", self._handle_mark_report_seen)
    self.router.add("POST", "/reports/{id}/ack-seen", self._handle_mark_ack_seen)
    self.router.add("POST", "/reports/{id}/answers", self._handle_submit_answer)
    self.router.add(
      "GET", "/reports/{id}/answer-requests/{request_id}", self._handle_answer_request
    )
    self.router.add(
      "POST",
      "/reports/{id}/answers/{answer_id}/ack",
      self._handle_ack_report_answer,
    )
    self.router.add("GET", "/survey", self._handle_get_survey)
    self.router.add("POST", "/survey", self._handle_submit_survey)
    self.router.add("POST", "/survey/interrupt", self._handle_survey_interrupt)
    self.router.add("POST", "/agent-interrupt", self._handle_agent_interrupt)
    self.router.add("POST", "/timezone", self._handle_set_timezone)
    self.router.add("POST", "/markdown", self._handle_render_markdown)
    self.router.add("POST", "/mermaid-cache", self._handle_cache_mermaid)
    self.router.add("GET", "/mermaid-cache/{key}", self._handle_get_mermaid)
    self.router.add("GET", "/state/snapshot", self._handle_state_snapshot)
    self.router.add("GET", "/state/changes", self._handle_state_changes)
    self.router.add("POST", "/state/merge", self._handle_state_merge)
    self.router.add("GET", "/state/assets", self._handle_state_assets)
    self.router.add("POST", "/state/assets", self._handle_state_asset_push)

  def dispatch(
    self, method: str, raw_url: str, body: dict[str, Any] | None = None
  ) -> tuple[int, dict[str, Any]]:
    """Dispatch a request and return (status_code, json_payload)."""
    parsed = urlparse(raw_url)
    path = parsed.path.rstrip("/") or "/"
    query = parse_qs(parsed.query)
    handler, params, allowed = self.router.match(method, path)
    if handler is None:
      if allowed:
        return (
          int(HTTPStatus.METHOD_NOT_ALLOWED),
          {"error": f"Method {method} not allowed; expected {allowed}"},
        )
      return (int(HTTPStatus.NOT_FOUND), {"error": f"Unknown route: {path}"})
    try:
      request_body = dict(body or {})
      request_id = request_body.pop("_request_id", None)
      if request_id is not None:
        if method.upper() not in {"POST", "PATCH", "DELETE"}:
          raise ValueError("Request ID requires a write action")
        if not isinstance(request_id, str) or not re.fullmatch(
          r"[a-zA-Z0-9_-]{1,128}", request_id
        ):
          raise ValueError("Invalid request ID")
        canonical = json.dumps(
          [method.upper(), path, parsed.query, request_body],
          sort_keys=True,
          separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        created_files: list[Path] = []
        overwritten_files: dict[Path, bytes | None] = {}

        def rollback_files() -> None:
          for file_path in created_files:
            file_path.unlink(missing_ok=True)
          for file_path, contents in overwritten_files.items():
            if contents is None:
              file_path.unlink(missing_ok=True)
            else:
              file_path.write_bytes(contents)

        deferred_flag = False

        def perform() -> tuple[int, dict[str, Any]]:
          nonlocal deferred_flag
          self._browser_write.request_id = request_id
          self._browser_write.files = created_files
          self._browser_write.overwritten_files = overwritten_files
          try:
            try:
              status, payload = handler(params, query, request_body)
            except DirtyTreeError as exc:
              # The browser-write savepoint rolls a failed action back, so the
              # handler records the flag afterwards, never inside the action.
              deferred_flag = True
              status, payload = int(HTTPStatus.BAD_REQUEST), {"error": str(exc)}
            if not 200 <= status < 300:
              rollback_files()
            if 200 <= status < 300:
              payload = self._compact_browser_result(payload)
            return status, payload
          except BaseException:
            rollback_files()
            raise
          finally:
            del self._browser_write.request_id
            del self._browser_write.files
            del self._browser_write.overwritten_files

        try:
          status, payload = self.store.execute_browser_write_once(
            request_id, fingerprint, perform
          )
        except BaseException:
          rollback_files()
          raise
        finally:
          if deferred_flag:
            self.messages.flag_dirty_finish()
      else:
        status, payload = handler(params, query, request_body)
      if (
        method.upper() in {"POST", "PATCH", "DELETE"}
        and path not in {"/mermaid-cache", "/browser-write-token", "/timezone"}
        and 200 <= status < 300
      ):
        self._state_version = getattr(self, "_state_version", 0) + 1
      return (status, payload)
    except DirtyTreeError as exc:
      return (int(HTTPStatus.BAD_REQUEST), {"error": str(exc)})
    except KeyError as exc:
      return (
        int(HTTPStatus.NOT_FOUND),
        {"error": str(exc.args[0] if exc.args else exc)},
      )
    except (TypeError, ValueError) as exc:
      return (int(HTTPStatus.BAD_REQUEST), {"error": str(exc)})
    except RetryCapacityError as exc:
      return (
        int(HTTPStatus.SERVICE_UNAVAILABLE),
        {"error": str(exc), "retryable": True},
      )
    except RuntimeError as exc:
      return (int(HTTPStatus.INTERNAL_SERVER_ERROR), {"error": str(exc)})

  @staticmethod
  def _compact_browser_result(payload: dict[str, Any]) -> dict[str, Any]:
    fields = {
      "message": (
        "id",
        "modified_at",
        "status",
        "attachments",
        "replies_revision",
        "replies_seen",
      ),
      "task": ("id", "modified_at", "status", "finished_at"),
      "report": ("id", "modified_at", "status", "seen_at", "ack_seen"),
      "answer": ("id", "report_id", "created_at", "modified_at", "status"),
    }
    result = {"_receipt": True, **payload}
    for key, names in fields.items():
      resource = payload.get(key)
      if not isinstance(resource, dict):
        continue
      result[key] = {name: resource[name] for name in names if name in resource}
      if "acks" in resource:
        result[key]["reply_revision"] = hashlib.sha256(
          json.dumps(resource["acks"], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    return result

  def data_version(self) -> tuple[int, int, int]:
    """Return a version tuple covering SQLite, in-process writes, runs, and uploads."""
    in_proc = int(getattr(self, "_state_version", 0))
    aux_ver = self.run_stamp() ^ self.uploads_stamp()
    try:
      external, local = self.store.data_version()
      return (external, in_proc + local, aux_ver)
    except sqlite3.Error:
      return (0, in_proc, aux_ver)

  def uploads_stamp(self) -> int:
    """Stamp committed upload files so missing-file status updates live."""
    from .services import STAGED_UPLOAD_RE

    db_path = str(getattr(self.store, "db_path", ""))
    if not db_path or db_path == ":memory:":
      uploads = getattr(self, "_memory_uploads_dir", None)
      if uploads is None or not Path(uploads).is_dir():
        return 0
      uploads_dir = Path(uploads)
    else:
      uploads_dir = Path(db_path).parent / "uploads"
      if not uploads_dir.is_dir():
        return 0
    try:
      entries = sorted(
        path.relative_to(uploads_dir).as_posix()
        for path in uploads_dir.rglob("*")
        if path.is_file() and not STAGED_UPLOAD_RE.match(path.name)
      )
    except OSError:
      return 0
    if not entries:
      return 0
    digest = hashlib.sha256("\n".join(entries).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")

  def run_stamp(self) -> int:
    """Stamp finished runs so a gate completion reaches clients without polling."""
    db_path = str(getattr(self.store, "db_path", ""))
    if not db_path or db_path == ":memory:":
      return 0
    runs = Path(db_path).parent / "runs"
    try:
      entries = list(runs.glob("*.exit"))
    except OSError:
      return 0
    stamp = len(entries)
    for entry in entries:
      try:
        stamp += entry.stat().st_mtime_ns % 1000000
      except OSError:
        continue
    return stamp

  def report_ack_snapshot(self) -> dict[str, tuple[str, str, int]]:
    """Return the latest answer ID, status, and ack count for each live report."""
    snapshot: dict[str, tuple[str, str, int]] = {}
    for report in self.reports.list_reports():
      if not report.answers:
        continue
      latest = report.answers[-1]
      snapshot[report.id] = (latest.id, latest.status, len(latest.acks))
    return snapshot

  def report_acked_events(
    self, previous: dict[str, tuple[str, str, int]]
  ) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str, int]]]:
    """Return one `report.acked` event per report whose latest ack changed."""
    current_reports = {report.id: report for report in self.reports.list_reports()}
    current: dict[str, tuple[str, str, int]] = {}
    for report in current_reports.values():
      if not report.answers:
        continue
      latest = report.answers[-1]
      current[report.id] = (latest.id, latest.status, len(latest.acks))
    events: list[dict[str, Any]] = []
    for report_id, snap in current.items():
      if snap[1] != "acked":
        continue
      prev = previous.get(report_id)
      if (
        prev is not None
        and prev[0] == snap[0]
        and prev[1] == "acked"
        and prev[2] >= snap[2]
      ):
        continue
      events.append(
        {
          "type": "report.acked",
          "report": self._serialize_report(current_reports[report_id]),
        }
      )
    return (events, current)

  def resolve_asset(self, request_path: str) -> tuple[bytes, str] | None:
    """Resolve a static file inside assets_dir or state files without path traversal."""
    clean = request_path.split("?", 1)[0].lstrip("/")
    if clean.startswith(STATIC_ASSETS_PREFIX):
      rel = clean[len(STATIC_ASSETS_PREFIX) :] or "index.html"
      candidate = self._resolve_under(self.assets_dir, rel)
      return self._file_response(candidate) if candidate is not None else None
    if clean.startswith(STATIC_REPORTS_PREFIX):
      rel = clean[len(STATIC_REPORTS_PREFIX) :]
      if Path(rel).suffix.lower() not in REPORT_STATIC_EXTS:
        return None
      candidate = self._resolve_state_file("reports", rel)
      if candidate is not None:
        return self._file_response(candidate)
      markdown = self._report_markdown_bytes(rel)
      return (markdown, MIME_OVERRIDES[".md"]) if markdown is not None else None
    if clean.startswith(STATIC_UPLOADS_PREFIX):
      rel = clean[len(STATIC_UPLOADS_PREFIX) :]
      candidate = self._resolve_state_file("uploads", rel)
      return self._file_response(candidate) if candidate is not None else None

    if not clean or clean in {"index.html", "messages", "tasks", "reports"}:
      clean = "index.html"
    elif clean.startswith("assets/"):
      clean = clean[len("assets/") :]
    candidate = self._resolve_under(self.assets_dir, clean)
    if candidate is None:
      candidate = self._resolve_report_asset(clean)
      if candidate is None:
        return None
    return self._file_response(candidate)

  @staticmethod
  def is_static_request(request_path: str) -> bool:
    """Return True for file paths that must not be routed as JSON resources."""
    clean = request_path.split("?", 1)[0].lstrip("/")
    if clean.startswith("static/"):
      return True
    if clean.startswith("uploads/"):
      return True
    if clean.startswith("reports/"):
      return Path(clean).suffix.lower() in REPORT_STATIC_EXTS
    return False

  def _file_response(self, candidate: Path) -> tuple[bytes, str]:
    suffix = candidate.suffix.lower()
    content_type = (
      MIME_OVERRIDES.get(suffix)
      or mimetypes.guess_type(candidate.name)[0]
      or "application/octet-stream"
    )
    if candidate.name == "index.html":
      page = candidate.read_text(encoding="utf-8").replace(
        "<title>Arena Steering</title>",
        f"<title>{html.escape(self.repo_name)} - Steering</title>",
      )
      return page.encode("utf-8"), content_type
    return candidate.read_bytes(), content_type

  @staticmethod
  def _resolve_under(root: Path, rel_path: str) -> Path | None:
    if not isinstance(rel_path, str) or not rel_path:
      return None
    parts = rel_path.split("/")
    if any(
      part in {"", ".", ".."} or not STATIC_PATH_PART_RE.fullmatch(part)
      for part in parts
    ):
      return None
    safe_parts = [os.path.basename(part) for part in parts]
    if safe_parts != parts:
      return None
    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*safe_parts).resolve()
    try:
      candidate.relative_to(root_resolved)
    except ValueError:
      return None
    return candidate if candidate.is_file() else None

  def _resolve_state_file(self, directory_name: str, rel_path: str) -> Path | None:
    db_path = getattr(self.store, "db_path", "")
    if not db_path or db_path == ":memory:":
      return None
    state_dir = Path(db_path).resolve().parent
    return self._resolve_under(state_dir / directory_name, rel_path)

  def _uploads_dir(self) -> Path:
    db_path = getattr(self.store, "db_path", "")
    if db_path and db_path != ":memory:":
      uploads_dir = Path(db_path).resolve().parent / "uploads"
    else:
      cached = getattr(self, "_memory_uploads_dir", None)
      if cached is None:
        cached = Path(tempfile.mkdtemp(prefix="arena-steering-uploads-"))
        self._memory_uploads_dir = cached  # type: ignore[attr-defined]
      uploads_dir = cached
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir

  def _clean_uncommitted_uploads(self) -> None:
    """Remove staged request files left by a crash before database commit."""
    with self.store.transaction():
      committed = {
        hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        for request_id in self.store.browser_write_ids()
      }
      candidates = list(self._uploads_dir().glob("request-*"))
      references = {path: f"uploads/{path.name}" for path in candidates}
      referenced = self.store.referenced_uploads(list(references.values()))
      for file_path in candidates:
        match = re.fullmatch(r"request-([0-9a-f]{64})-[0-9]+-.*", file_path.name)
        if (
          match
          and match.group(1) not in committed
          and references[file_path] not in referenced
        ):
          file_path.unlink(missing_ok=True)

  @staticmethod
  def _safe_upload_name(
    name: Any, *, content_type: Any = None, epoch_ms: int | None = None
  ) -> str:
    from .services import sanitize_upload_filename

    cleaned = sanitize_upload_filename(name)
    if cleaned:
      return cleaned
    raw_name = Path(str(name or "")).name.strip()
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", Path(raw_name).suffix)[:20].lower()
    if suffix in {"", "."}:
      mime = str(content_type or "").split(";", 1)[0].strip().lower()
      suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
        "image/avif": ".avif",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "application/json": ".json",
        "application/pdf": ".pdf",
      }.get(mime, ".bin")
    stamp = int(time.time() * 1000) if epoch_ms is None else epoch_ms
    return f"{stamp}{suffix}"

  @staticmethod
  def _upload_bytes(file_body: dict[str, Any]) -> bytes:
    for field_name in ("content_base64", "base64", "data_base64"):
      encoded = file_body.get(field_name)
      if isinstance(encoded, str) and encoded:
        try:
          return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
          raise ValueError("Upload content_base64 is invalid") from exc
    for field_name in ("text", "content"):
      text = file_body.get(field_name)
      if isinstance(text, str):
        return text.encode("utf-8")
    raise ValueError("Upload file content is required")

  def _store_upload(self, file_body: dict[str, Any]) -> Attachment:
    if not isinstance(file_body, dict):
      raise TypeError("Upload file must be an object")
    safe_name = self._safe_upload_name(
      file_body.get("name") or file_body.get("filename"),
      content_type=file_body.get("content_type")
      or file_body.get("mime_type")
      or file_body.get("type"),
    )
    data = self._upload_bytes(file_body)
    request_id = getattr(self._browser_write, "request_id", None)
    if request_id is not None:
      digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
      prefix = f"request-{digest}-{len(self._browser_write.files)}-"
      suffix = Path(safe_name).suffix[:20]
      stem = safe_name[: -len(suffix)] if suffix else safe_name
      stored_name = prefix + stem[: 255 - len(prefix) - len(suffix)] + suffix
    else:
      stored_name = f"{secrets.token_hex(3)}-{int(time.time())}-{safe_name}"
    stored_path = self._uploads_dir() / stored_name
    if request_id is not None:
      self._browser_write.files.append(stored_path)
    stored_path.write_bytes(data)
    return Attachment(
      name=safe_name,
      path=str(stored_path),
      size_bytes=len(data),
      present=True,
    )

  def _serialize_attachment(self, attachment: Attachment) -> dict[str, Any]:
    from .services import is_attachment_present

    data = attachment.to_dict()
    exists = is_attachment_present(attachment)
    data["present"] = exists
    data["lost"] = not exists
    if not exists or not attachment.path:
      return data
    stored_path = Path(attachment.path).expanduser()
    try:
      relative = stored_path.resolve().relative_to(self._uploads_dir().resolve())
    except ValueError:
      return data
    data["url"] = f"/static/uploads/{relative.as_posix()}"
    return data

  @staticmethod
  def _paste_needs_attachment(text: str) -> bool:
    return (
      len(text) > PASTE_ATTACHMENT_CHAR_THRESHOLD
      or len(re.split(r"\r\n|\r|\n", text)) >= PASTE_ATTACHMENT_LINE_THRESHOLD
    )

  def _message_attachments_from_body(
    self, body: dict[str, Any]
  ) -> tuple[str, list[Attachment]]:
    attachments = [Attachment.from_dict(raw) for raw in body.get("attachments", [])]
    message_body = str(body.get("body", ""))
    file_body = body.get("file") or body.get("upload")
    if file_body is not None:
      attachments.append(self._store_upload(file_body))
    pasted_text = body.get("pasted_text", body.get("paste_text"))
    if isinstance(pasted_text, str) and pasted_text:
      if self._paste_needs_attachment(pasted_text):
        line_count = len(re.split(r"\r\n|\r|\n", pasted_text))
        epoch_ms = int(time.time() * 1000)
        attachments.append(
          self._store_upload(
            {
              "name": f"{line_count}-pasted-lines-{epoch_ms}.txt",
              "text": pasted_text,
            }
          )
        )
        if not message_body.strip():
          message_body = PASTE_ATTACHMENT_BODY
      elif not message_body.strip():
        message_body = pasted_text
    return message_body, attachments

  def _report_markdown_bytes(self, rel_path: str) -> bytes | None:
    rel = Path(rel_path)
    if rel.suffix.lower() != ".md" or rel.parent != Path("."):
      return None
    try:
      report = self.reports.get_report(rel.stem)
    except (KeyError, ValueError):
      return None
    return report.body.encode("utf-8")

  def _resolve_report_asset(self, clean_path: str) -> Path | None:
    if clean_path.startswith("uploads/"):
      return self._resolve_state_file("uploads", clean_path[len("uploads/") :])
    return self._resolve_report_image(clean_path)

  def _resolve_report_image(self, clean_path: str) -> Path | None:
    """Resolve an image or GIF inside <state_dir>/reports/ without path traversal."""
    rel = clean_path.removeprefix("reports/")
    suffix = Path(rel).suffix.lower()
    if suffix not in REPORT_IMAGE_EXTS:
      return None
    candidate = self._resolve_state_file("reports", rel)
    if candidate is not None:
      return candidate
    db_path = getattr(self.store, "db_path", "")
    if not db_path or db_path == ":memory:":
      return None
    return self._resolve_under(Path(db_path).resolve().parent, rel)

  def asset_signature_hash(self) -> str:
    """Return a hash after asset changes have been quiet for ten seconds."""
    tracker = self._asset_tracker
    with tracker["lock"]:
      sig = assets_signature(self.assets_dir)
      now = time.monotonic()
      if sig != tracker["observed"]:
        tracker["observed"] = sig
        tracker["changed_at"] = now
      elif (
        tracker["changed_at"] is not None
        and now - tracker["changed_at"] >= HOT_RELOAD_QUIET_SECONDS
      ):
        tracker["stable"] = sig
        tracker["changed_at"] = None
      digest = hashlib.sha256(repr(tracker["stable"]).encode("utf-8")).hexdigest()
    return digest[:12]

  def _handle_blocked_bash_log(
    self, _params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    state_dir = (
      Path(self.store.db_path).parent if self.store.db_path != ":memory:" else None
    )
    entries = read_blocked_bash(state_dir) if state_dir is not None else []
    return int(HTTPStatus.OK), {"entries": entries}

  def _handle_dismiss_blocked_bash(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    if self.store.db_path == ":memory:":
      raise KeyError("Blocked Bash entry no longer present")
    dismiss_blocked_bash(Path(self.store.db_path).parent, params["id"])
    return int(HTTPStatus.OK), {"dismissed": True}

  def _handle_health(
    self, _params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    survey = self.messages.get_survey()
    violation = self.messages.pending_violation()
    heartbeat = self.store.get_meta("polling_at", "") or ""
    try:
      stamp = datetime.fromisoformat(heartbeat)
      if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
      age = (datetime.now(UTC) - stamp).total_seconds()
      polling = 0 <= age < POLL_FRESH_SECONDS
    except ValueError:
      polling = False
    return (
      int(HTTPStatus.OK),
      {
        "ok": True,
        "version": __version__,
        "repo": self.repo_name,
        "asset_version": self.asset_signature_hash(),
        "timezone": self.store.get_meta("timezone", "") or "",
        "agent_interrupt_pending": violation is not None,
        "violation_type": violation.violation_type if violation else "",
        "violation_types": VIOLATION_TYPES,
        "read_set": self._read_set,
        "survey": survey,
        "polling": polling,
        "polling_gate": polling and self.store.get_meta("polling_gate") == "1",
        "poll_completed": self.store.get_meta("poll_completed") == "1",
        "skip_poll": self.messages.skip_poll_armed(),
        "polling_since": self.store.get_meta("polling_since") if polling else None,
        "agent_seen_at": self.store.get_meta("agent_seen_at") or None,
        "turn_ended_at": self.store.get_meta("turn_ended_at") or None,
        "last_tool": self.messages.tool_activity(),
        "message_revision": self.store.message_revision(),
        "visible_message_revision": self.store.visible_message_revision(),
        "message_total": self.store.message_count(),
        "calls_since_message": self.messages.calls_since_message(),
      },
    )

  def _handle_set_timezone(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    timezone_name = str(body.get("timezone", "")).strip()
    if not timezone_name:
      raise ValueError("timezone is required")
    try:
      from zoneinfo import ZoneInfo

      ZoneInfo(timezone_name)
    except Exception as exc:
      raise ValueError(f"Unknown timezone: {timezone_name}") from exc
    if self.store.db_path != ":memory:":
      state_dir = Path(self.store.db_path).parent
      try:
        with tempfile.TemporaryDirectory(dir=state_dir) as staged_dir:
          staged = Path(staged_dir) / "display_tz"
          staged.write_text(f"{timezone_name}\n", encoding="utf-8")
          staged.replace(state_dir / "display_tz")
      except OSError as exc:
        raise RuntimeError(
          "Could not save browser timezone for the agent shell"
        ) from exc
    self.store.set_meta("timezone", timezone_name)
    return (int(HTTPStatus.OK), {"timezone": timezone_name})

  def _handle_state_snapshot(
    self, _params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    rows, state_version = self.store.export_state()
    return (
      int(HTTPStatus.OK),
      {
        "schema": STATE_SCHEMA_VERSION,
        "state_version": state_version,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **rows,
      },
    )

  def _handle_state_changes(
    self, _params: dict[str, str], query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    since = (query.get("since") or [""])[0].strip()
    rows, state_version = self.store.export_state(since=since or None)
    return (
      int(HTTPStatus.OK),
      {
        "schema": STATE_SCHEMA_VERSION,
        "since": since,
        "state_version": state_version,
        **rows,
      },
    )

  def _handle_state_merge(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: Any
  ) -> tuple[int, Any]:
    if not isinstance(body, dict):
      raise TypeError("A merge document must be a JSON object")
    schema = body.get("schema", STATE_SCHEMA_VERSION)
    if schema != STATE_SCHEMA_VERSION:
      raise ValueError(f"Unknown state schema: {schema!r}")
    source = str(body.get("source") or "unknown container")
    removed_assets: list[str] = []
    with self.store.transaction():
      before = self.store.export_rows() if body.get("deleted") else None
      counts = self.store.merge_rows(body)
      total = len(counts["added"]) + len(counts["updated"]) + len(counts["deleted"])
      if total:
        self._state_version = int(getattr(self, "_state_version", 0)) + 1
        self.store.set_meta(
          "state_restore_notice",
          json.dumps(
            {
              "source": source,
              "added": len(counts["added"]),
              "updated": len(counts["updated"]),
              "deleted": len(counts["deleted"]),
            }
          ),
        )
      if counts["deleted"] and before is not None:
        after = self.store.export_rows()
        deleted_ids = set(counts["deleted"])
        gone = [
          row
          for table in STATE_TABLES
          for row in before[table]
          if row["id"] in deleted_ids
          and not any(item["id"] == row["id"] for item in after[table])
        ]
        uploads = self._uploads_dir()
        files = [path for path in uploads.rglob("*") if path.is_file()]
        candidates = [
          (path, f"uploads/{path.relative_to(uploads).as_posix()}") for path in files
        ]
        referenced = self.store.referenced_uploads([rel for _, rel in candidates])
        for path, rel in candidates:
          if rel not in referenced and any(
            self.store.row_references_upload(row, rel) for row in gone
          ):
            path.unlink(missing_ok=True)
            removed_assets.append(rel)
    return (
      int(HTTPStatus.OK),
      {
        "schema": STATE_SCHEMA_VERSION,
        "state_version": self.store.state_stamp(),
        "source": source,
        "added": counts["added"],
        "updated": counts["updated"],
        "deleted": counts["deleted"],
        "assets_removed": removed_assets,
        "skipped": counts["skipped"],
      },
    )

  def _handle_state_assets(
    self, _params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    uploads = self._uploads_dir()
    files = [path for path in sorted(uploads.rglob("*")) if path.is_file()]
    candidates = [
      (path, f"uploads/{path.relative_to(uploads).as_posix()}") for path in files
    ]
    referenced = self.store.referenced_uploads([rel for _, rel in candidates])
    assets = [
      {
        "path": rel,
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
      }
      for path, rel in candidates
      if rel in referenced
    ]
    return (int(HTTPStatus.OK), {"assets": assets})

  def _handle_state_asset_push(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: Any
  ) -> tuple[int, Any]:
    if not isinstance(body, dict):
      raise TypeError("An asset push must be a JSON object")
    rel = str(body.get("path") or "").strip()
    if not rel.startswith("uploads/") or ".." in rel.split("/"):
      raise ValueError("An asset path must live under uploads/")
    encoded = body.get("content")
    if not isinstance(encoded, str) or not encoded:
      raise ValueError("An asset push needs base64 content")
    try:
      data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
      raise ValueError(f"Invalid base64 content: {exc}") from exc
    digest = hashlib.sha256(data).hexdigest()
    expected = str(body.get("sha256") or "").strip()
    if expected and expected != digest:
      raise ValueError("The asset hash does not match its content")
    uploads = self._uploads_dir()
    target = (uploads / rel[len("uploads/") :]).resolve()
    if uploads.resolve() not in target.parents and target.parent != uploads.resolve():
      raise ValueError("An asset path must stay inside the uploads directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
      return (int(HTTPStatus.OK), {"path": rel, "sha256": digest, "stored": False})
    if hasattr(self._browser_write, "overwritten_files"):
      self._browser_write.overwritten_files[target] = (
        target.read_bytes() if target.is_file() else None
      )
    target.write_bytes(data)
    self._state_version = int(getattr(self, "_state_version", 0)) + 1
    return (int(HTTPStatus.OK), {"path": rel, "sha256": digest, "stored": True})

  def _handle_get_survey(
    self, _params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    return (int(HTTPStatus.OK), {"survey": self.messages.get_survey()})

  def _handle_submit_survey(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: Any
  ) -> tuple[int, Any]:
    values = body.get("answers", body) if isinstance(body, dict) else body
    survey = self.messages.submit_survey(values)
    return (int(HTTPStatus.OK), {"survey": survey})

  def _handle_survey_interrupt(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    active = body.get("active") if isinstance(body, dict) and "active" in body else None
    survey = self.messages.trigger_bible_check(active=active)
    return (int(HTTPStatus.OK), {"survey": survey})

  def _handle_agent_interrupt(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    armed = body.get("armed", True)
    if not isinstance(armed, bool):
      raise TypeError("armed must be true or false")
    if not armed:
      with self.store.transaction():
        pending = self.messages.pending_violation()
        if pending is not None:
          self.messages.ack(pending.id, "Disarmed on the page.")
      return (int(HTTPStatus.OK), {"agent_interrupt_pending": False})

    tab = body.get("tab")
    if not isinstance(tab, str) or tab not in VIOLATION_TYPES:
      raise ValueError("tab must name a preview tab")
    reason = body.get("reason")
    if not isinstance(reason, str) or reason not in VIOLATION_TYPES[tab]:
      raise ValueError("reason must name a violation type for this tab")
    clause_file = body.get("clause_file")
    if clause_file not in (None, "") and (
      not isinstance(clause_file, str) or clause_file not in self._read_set
    ):
      raise ValueError("clause_file must name a path in the bible")
    clause_file = clause_file or ""
    if "note" in body:
      raise ValueError("Use msg instead of the legacy note field")
    msg = body.get("msg")
    if msg is not None and not isinstance(msg, str):
      raise ValueError("msg must be text")
    cited = " ".join((msg or "").split())
    report_id = body.get("report_id")
    if report_id is not None and tab != "reports":
      raise ValueError("report_id is only valid for reports")
    report = None
    if report_id is not None:
      try:
        report = self.reports.get_report(report_id)
      except KeyError as exc:
        raise ValueError("report_id must name an existing report") from exc
      if report.status != "published":
        raise ValueError("report_id must name a published report")

    label = (
      "Task missing details"
      if reason == "missing-details"
      else reason.removesuffix("-violation").replace("-", " ").capitalize()
    )
    lines = [f"VIOLATION: {label}"]
    if cited:
      lines.append(f"Cited: {cited}")
    if clause_file:
      lines.append(f"Book: {clause_file}")
    if report is not None:
      lines.append(f"Report: {report.id}")
      source = Path(report.source_file) if report.source_file else None
      if source is not None and not source.is_absolute():
        source = _detect_repo_root(self.store) / source
      listed = (
        home_relative(source) if source is not None else "unavailable (not recorded)"
      )
      lines.append(f"Source file: {listed}")
    elif tab == "tasks":
      current = next(
        (task for task, blocked in self.tasks.list_tasks() if not blocked), None
      )
      if current is not None and reason != "empty-task-list":
        lines.append(f"Current task: {current.id}. Is this what you are working on?")
      else:
        lines.append("The task list is empty. Maybe a task should have spawned.")
    with self.store.transaction():
      pending = self.messages.pending_violation()
      if report is not None and pending is not None:
        raise ValueError(
          "A violation is already pending; ack it before flagging a report"
        )
      if pending is None:
        if report is not None:
          self.reports.unpublish_report(report.id)
        pending = self.messages.send_message(
          with_bible_read("\n".join(lines)),
          hidden=True,
          blocking=True,
          violation_type=reason,
          violation_clause_file=clause_file,
        )
    return (
      int(HTTPStatus.OK),
      {"agent_interrupt_pending": True, "violation_type": pending.violation_type},
    )

  def _handle_render_markdown(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    markdown = str(body.get("markdown", ""))
    breaks = bool(body.get("breaks", True))
    headings: list[dict[str, Any]] = []
    rendered = render_markdown(
      markdown,
      breaks=breaks,
      pre_wrap=bool(body.get("pre_wrap", False)),
      headings=headings,
    )
    return (int(HTTPStatus.OK), {"html": rendered, "headings": headings})

  def _handle_cache_mermaid(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    source, svg = body.get("source"), body.get("svg")
    if not isinstance(source, str) or not source or len(source) > MERMAID_SOURCE_LIMIT:
      raise ValueError("Invalid Mermaid source")
    if not isinstance(svg, str) or not _safe_mermaid_svg(svg):
      raise ValueError("Invalid Mermaid SVG")
    key = hashlib.sha256(source.encode("utf-8")).hexdigest()
    with self._mermaid_lock:
      if self._mermaid_cache_dir is not None:
        self._mermaid_cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
          mode="w", encoding="utf-8", dir=self._mermaid_cache_dir, delete=False
        ) as temporary:
          temporary_path = Path(temporary.name)
          try:
            temporary.write(svg)
          except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        try:
          os.replace(temporary_path, self._mermaid_cache_dir / f"{key}.svg")
        finally:
          temporary_path.unlink(missing_ok=True)
        files = sorted(
          (
            path
            for path in self._mermaid_cache_dir.glob("*.svg")
            if re.fullmatch(r"[0-9a-f]{64}\.svg", path.name) and not path.is_symlink()
          ),
          key=lambda path: path.stat().st_mtime_ns,
        )
        for old in files[:-MERMAID_CACHE_LIMIT]:
          old.unlink(missing_ok=True)
      self._mermaid_cache[key] = svg
      self._mermaid_cache.move_to_end(key)
      if len(self._mermaid_cache) > MERMAID_CACHE_LIMIT:
        self._mermaid_cache.popitem(last=False)
    return (int(HTTPStatus.OK), {"key": key})

  def _handle_get_mermaid(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    key = params["key"]
    if not re.fullmatch(r"[0-9a-f]{64}", key):
      raise ValueError("Invalid Mermaid cache key")
    with self._mermaid_lock:
      svg = self._mermaid_cache.get(key)
      if svg is None and self._mermaid_cache_dir is not None:
        path = self._mermaid_cache_dir / f"{key}.svg"
        try:
          if not path.is_symlink() and path.stat().st_size <= MERMAID_SVG_LIMIT:
            candidate = path.read_text(encoding="utf-8")
            if _safe_mermaid_svg(candidate):
              svg = candidate
              self._mermaid_cache[key] = svg
              if len(self._mermaid_cache) > MERMAID_CACHE_LIMIT:
                self._mermaid_cache.popitem(last=False)
        except (OSError, UnicodeError):
          pass
      if svg is not None:
        self._mermaid_cache.move_to_end(key)
    if svg is None:
      raise KeyError("Mermaid cache entry not found")
    return (int(HTTPStatus.OK), {"svg": svg})

  def _handle_hidden_message_window(
    self, _params: dict[str, str], query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    first = (query.get("first") or [""])[0]
    last = (query.get("last") or [""])[0]
    if not first or not last:
      raise ValueError("Both loaded message window boundaries are required")
    items, revision, order = self.store.hidden_message_window(
      first, last, (query.get("q") or [""])[0].strip()
    )
    return int(HTTPStatus.OK), {
      "messages": [self._serialize_message(item) for item in items],
      "revision": revision,
      "order": order,
    }

  def _handle_list_messages(
    self, _params: dict[str, str], query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    include_all = query.get("all", ["0"])[0] in {"1", "true"}
    include_hidden = query.get("hidden", ["0"])[0] in {"1", "true"}
    revision = self.store.message_revision()
    items, total, has_more = self.messages.list_message_page(
      unread_only=not include_all,
      include_hidden=include_hidden,
      query=(query.get("q", [""])[0] or "").strip(),
      before=(query.get("before", [""])[0] or "").strip(),
      limit=20,
    )
    result = {
      "messages": [self._serialize_message(item) for item in items],
      "total": total,
      "has_more": has_more,
      "revision": revision,
      "visible_revision": self.store.visible_message_revision(),
    }
    request_id = query.get("request_id", [""])[0]
    if request_id:
      if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", request_id):
        raise ValueError("Invalid request ID")
      result["pending_message_id"] = self.store.message_id_for_request(request_id)
    return (int(HTTPStatus.OK), result)

  def _message_request_identity(
    self, body: dict[str, Any], route: str
  ) -> tuple[str | None, str]:
    request_id = body.get("request_id")
    if request_id is None:
      return None, ""
    if request_id != getattr(self._browser_write, "request_id", None):
      raise ValueError("Message retry ID requires its signed browser write token")
    if not isinstance(request_id, str) or not re.fullmatch(
      r"[a-zA-Z0-9_-]{1,128}", request_id
    ):
      raise ValueError("Invalid request ID")
    content = {key: value for key, value in body.items() if key != "request_id"}
    canonical = json.dumps([route, content], sort_keys=True, separators=(",", ":"))
    return request_id, hashlib.sha256(canonical.encode("utf-8")).hexdigest()

  def _handle_create_message(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    request_id, fingerprint = self._message_request_identity(body, "/messages")
    attachments = [Attachment.from_dict(raw) for raw in body.get("attachments", [])]
    msg = self.messages.send_message(
      str(body.get("body", "")),
      hidden=bool(body.get("hidden", False)),
      blocking=bool(body.get("blocking", True)),
      attachments=attachments,
      parent_ids=[str(item) for item in (body.get("parent_ids") or [])],
      parent_refs=body.get("parent_refs"),
      msg_id=body.get("id"),
      request_id=request_id,
      fingerprint=fingerprint,
    )
    return (int(HTTPStatus.CREATED), {"message": self._serialize_message(msg)})

  def _handle_browser_write_token(
    self, _params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, dict[str, str]]:
    return (
      int(HTTPStatus.CREATED),
      {"request_id": self.store.issue_browser_write_token()},
    )

  def _handle_upload_file(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    file_body = body.get("file") or body.get("upload") or body
    attachment = self._store_upload(file_body)
    return (
      int(HTTPStatus.CREATED),
      {"attachment": self._serialize_attachment(attachment)},
    )

  def _handle_create_message_with_file(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    request_id, fingerprint = self._message_request_identity(
      body, "/messages/with-file"
    )
    message_body, attachments = self._message_attachments_from_body(body)
    msg = self.messages.send_message(
      message_body,
      hidden=_body_bool(body.get("hidden", False), False),
      blocking=_body_bool(body.get("blocking", True), True),
      attachments=attachments,
      parent_ids=[str(item) for item in (body.get("parent_ids") or [])],
      parent_refs=body.get("parent_refs"),
      msg_id=body.get("id"),
      request_id=request_id,
      fingerprint=fingerprint,
    )
    return (int(HTTPStatus.CREATED), {"message": self._serialize_message(msg)})

  def _handle_get_message(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    msg = self.messages.get_message(params["id"])
    return (int(HTTPStatus.OK), {"message": self._serialize_message(msg)})

  def _handle_ack_message(
    self, params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    markdown = str(body.get("markdown", body.get("ack", "")))
    target_type, acked = self.messages.ack(params["id"], markdown)
    if target_type == "report_answer":
      payload = {"answer": acked.to_dict()}
      if acked.task_details_added:
        payload["task_details_added"] = acked.task_details_added
      return (int(HTTPStatus.OK), payload)
    return (int(HTTPStatus.OK), {"message": self._serialize_message(acked)})

  def _handle_replies_seen(
    self, params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    revision = body.get("revision")
    matched = self.messages.mark_replies_seen(params["id"], revision)
    msg = self.messages.get_message(params["id"])
    return (
      int(HTTPStatus.OK if matched else HTTPStatus.CONFLICT),
      {"message": self._serialize_message(msg), "matched": matched},
    )

  def _handle_poll(
    self, _params: dict[str, str], query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    timeout_raw = query.get("timeout", ["0"])[0]
    timeout_sec = float(timeout_raw) if timeout_raw else 0.0
    delivered = self.messages.poll(timeout_seconds=timeout_sec, track_agent=False)
    return (
      int(HTTPStatus.OK),
      {
        "messages": [item.to_dict() for item in delivered["messages"]],
        "report_answers": [
          {"report_id": rep.id, "title": rep.title, "answer": ans.to_dict()}
          for rep, ans in delivered["report_answers"]
        ],
      },
    )

  def _handle_skip_poll(
    self, _params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    self.messages.trigger_skip_poll()
    return (int(HTTPStatus.OK), {"skip_poll": True})

  def _handle_cancel_skip_poll(
    self, _params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    self.messages.cancel_skip_poll()
    return (int(HTTPStatus.OK), {"skip_poll": False})

  def _handle_list_tasks(
    self, _params: dict[str, str], query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    include_finished = query.get("full", ["1"])[0] not in {"0", "false"}
    items = self.tasks.list_tasks(include_finished=include_finished)
    return (
      int(HTTPStatus.OK),
      {"tasks": [self._serialize_task(task, blocked) for task, blocked in items]},
    )

  def _handle_create_task(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    raw_links = body.get("links")
    task = self.tasks.create_task(
      str(body.get("id", "")),
      str(body.get("title", "")),
      size=body.get("size"),
      priority=body.get("priority"),
      type=str(body.get("type", "code")),
      types=body.get("types"),
      status=str(body.get("status", "upcoming")),
      scope=str(body.get("scope", "")),
      details=[str(x) for x in body.get("details", [])],
      links=raw_links if isinstance(raw_links, dict) else None,
      msg_ids=[str(x) for x in body["msg_ids"]] if "msg_ids" in body else None,
      report_ids=[str(x) for x in body["report_ids"]] if "report_ids" in body else None,
      task_ids=[str(x) for x in body["task_ids"]] if "task_ids" in body else None,
      deps=[str(x) for x in body["deps"]] if "deps" in body else None,
      gate_names=[str(x) for x in body["gate_names"]] if "gate_names" in body else None,
    )
    _, blocked = self.tasks.get_task(task.id)
    return (int(HTTPStatus.CREATED), {"task": self._serialize_task(task, blocked)})

  def _handle_get_task(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    task, blocked = self.tasks.get_task(params["id"])
    return (int(HTTPStatus.OK), {"task": self._serialize_task(task, blocked)})

  def _handle_patch_task(
    self, params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    task = self.tasks.patch_task(params["id"], body)
    _, blocked = self.tasks.get_task(task.id)
    return (int(HTTPStatus.OK), {"task": self._serialize_task(task, blocked)})

  def _handle_delete_task(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    removed = self.tasks.remove_task(params["id"])
    if not removed:
      raise KeyError(f"Unknown task ID: {params['id']}")
    return (int(HTTPStatus.OK), {"deleted": params["id"]})

  def _handle_list_reports(
    self, _params: dict[str, str], query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    stale_only = query.get("stale", ["0"])[0] in {"1", "true"}
    summary_only = query.get("summary", ["0"])[0] in {"1", "true"}
    items = self.reports.list_reports(stale_only=stale_only)
    return (
      int(HTTPStatus.OK),
      {
        "reports": [
          self._serialize_report(item, include_body=not summary_only) for item in items
        ]
      },
    )

  def _handle_publish_report(
    self, _params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    override = body.get("allow_unparsed_markers", False)
    if not isinstance(override, bool):
      raise TypeError("allow_unparsed_markers must be a boolean")
    report = self.reports.publish_report(
      str(body.get("id", "")),
      str(body.get("title", "")),
      str(body.get("body", "")),
      allow_unparsed_markers=override,
    )
    return (int(HTTPStatus.CREATED), {"report": self.verify_published_report(report)})

  def verify_published_report(self, report: Any) -> dict[str, Any]:
    """Compare the saved report and independently rendered GET response."""
    expected = self._serialize_report(report)
    _REPORT_RENDER_CACHE.pop(report.id, None)
    status, payload = self._handle_get_report({"id": report.id}, {}, None)
    actual = payload.get("report", {})
    keys = ("id", "modified_at", "body", "fields", "blocks")
    if status != HTTPStatus.OK or any(
      actual.get(key) != expected.get(key) for key in keys
    ):
      raise RuntimeError("Report publication verification failed after saving")
    return actual

  def _handle_get_report(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    report = self.reports.get_report(params["id"])
    return (int(HTTPStatus.OK), {"report": self._serialize_report(report)})

  def _handle_answer_request(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    result = self.store.browser_write_result(params["request_id"]) or {}
    report = result.get("report") or {}
    answer = result.get("answer") or {}
    answer_id = answer.get("id") if report.get("id") == params["id"] else None
    return (int(HTTPStatus.OK), {"answer_id": answer_id})

  def _handle_patch_report(
    self, params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    report = self.reports.get_report(params["id"])
    if "pinned" in body and not isinstance(body["pinned"], bool):
      raise ValueError("Report pinned must be a boolean")
    new_status = str(body.get("status", "")).strip()
    if new_status == "dismissed":
      self.reports.remove_report(report.id)
      _REPORT_RENDER_CACHE.pop(report.id, None)
      report.status = "dismissed"
      return (
        int(HTTPStatus.OK),
        {"report": self._serialize_report(report, include_body=False)},
      )
    if "pinned" in body:
      report = self.reports.pin_report(report.id, body["pinned"])
    return (int(HTTPStatus.OK), {"report": self._serialize_report(report)})

  def _handle_delete_report(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    removed = self.reports.remove_report(params["id"])
    _REPORT_RENDER_CACHE.pop(params["id"], None)
    return (int(HTTPStatus.OK), {"dismissed": removed})

  def _handle_mark_report_seen(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    report = self.reports.mark_seen(params["id"])
    return (int(HTTPStatus.OK), {"report": self._serialize_report(report)})

  def _handle_mark_ack_seen(
    self, params: dict[str, str], _query: dict[str, list[str]], _body: Any
  ) -> tuple[int, Any]:
    report = self.reports.mark_ack_seen(params["id"])
    return (int(HTTPStatus.OK), {"report": self._serialize_report(report)})

  def _handle_submit_answer(
    self, params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    values = body.get("values", body)
    answer = self.reports.submit_answer(
      params["id"],
      values,
      answer_id=body.get("id") if isinstance(body, dict) else None,
    )
    report = self.reports.get_report(params["id"])
    return (
      int(HTTPStatus.CREATED),
      {"answer": answer.to_dict(), "report": self._serialize_report(report)},
    )

  def _handle_ack_report_answer(
    self, params: dict[str, str], _query: dict[str, list[str]], body: dict[str, Any]
  ) -> tuple[int, Any]:
    markdown = str(body.get("markdown", body.get("ack", "")))
    answer = self.reports.ack_answer(params["id"], params["answer_id"], markdown)
    payload = {"answer": answer.to_dict()}
    if answer.task_details_added:
      payload["task_details_added"] = answer.task_details_added
    return (int(HTTPStatus.OK), payload)

  def _serialize_task(self, task: Task, blocked: bool) -> dict[str, Any]:
    payload = task.to_dict(blocked=blocked)
    payload["details"] = self.tasks.format_task_details(task)
    payload["attachments"] = [
      self._serialize_attachment(item) for item in task.attachments
    ]
    payload["effective_priority"] = task.effective_priority
    payload["running_gate"] = self.tasks.has_running_gate(task)
    return payload

  def _serialize_message(self, msg: Any) -> dict[str, Any]:
    payload = msg.to_dict()
    payload["attachments"] = [
      self._serialize_attachment(attachment) for attachment in msg.attachments
    ]
    payload["body_html"] = render_markdown(msg.body, breaks=True, pre_wrap=True)
    payload["acks_html"] = [
      render_markdown(ack, breaks=True, pre_wrap=True) for ack in msg.acks
    ]
    return payload

  @staticmethod
  def _serialize_report(report: Any, *, include_body: bool = True) -> dict[str, Any]:
    payload = report.to_dict()
    for ans in payload.get("answers", []):
      ans["ack"] = "\n\n".join(ans.get("acks", []))
      ans["acks_html"] = [render_markdown(a, breaks=True) for a in ans.get("acks", [])]
      ans["ack_html"] = "\n".join(ans["acks_html"])
    cache_lock, cache = _REPORT_RENDER_CACHE_STATE
    dismissed = getattr(report, "status", "") == "dismissed"
    with cache_lock:
      if dismissed:
        cache.pop(report.id, None)
      if not include_body:
        payload.pop("body", None)
        return payload
      cached = cache.get(report.id)
      if cached is not None and cached[0] == report.body:
        cache.move_to_end(report.id)
        payload["blocks"] = cached[1]
        return payload
    serialized_blocks = serialize_report_blocks(report.body)
    if not dismissed:
      with cache_lock:
        cache[report.id] = (report.body, serialized_blocks)
        cache.move_to_end(report.id)
        while len(cache) > REPORT_RENDER_CACHE_LIMIT:
          cache.popitem(last=False)
    payload["blocks"] = serialized_blocks
    return payload


_REPORT_RENDER_CACHE: OrderedDict[str, tuple[str, list[dict[str, Any]]]] = OrderedDict()
_REPORT_RENDER_CACHE_STATE = (threading.Lock(), _REPORT_RENDER_CACHE)


def file_signature(
  paths: Iterable[Path],
) -> tuple[tuple[str, int | None, int | None], ...]:
  """Return a tuple of (path, mtime_ns, size) for each target file."""
  entries: list[tuple[str, int | None, int | None]] = []
  for target in paths:
    try:
      st = target.stat()
      entries.append((str(target), st.st_mtime_ns, st.st_size))
    except OSError:
      entries.append((str(target), None, None))
  return tuple(entries)


def assets_signature(
  assets_dir: str | Path | None = None,
) -> tuple[tuple[str, int | None, int | None], ...]:
  """Return the hot-reload signature for bundled web assets."""
  base = (
    Path(assets_dir)
    if assets_dir is not None
    else Path(__file__).resolve().parents[2] / "assets"
  )
  if not base.is_dir():
    return ()
  targets = sorted(
    p
    for p in base.iterdir()
    if p.suffix.lower() in {".html", ".css", ".js", ".mjs", ".webmanifest"}
  )
  return file_signature(targets)


def scripts_signature(
  scripts_dir: str | Path | None = None,
) -> tuple[tuple[str, int | None, int | None], ...]:
  """Return the hot-reload signature for Arena Steering Python modules."""
  base = (
    Path(scripts_dir) if scripts_dir is not None else Path(__file__).resolve().parent
  )
  if not base.is_dir():
    return ()
  targets = sorted(base.glob("*.py"))
  if len(base.parents) > 3:
    demo_py = base.parents[3] / "maintenance" / "demo-arena-steer.py"
    if demo_py.is_file():
      targets.append(demo_py)
  return file_signature(targets)


def reload_server_handler(server: ThreadingHTTPServer, app: SteeringApp) -> SteeringApp:
  """Reload arena_steering modules and swap server.RequestHandlerClass in-process."""
  pkg = __package__ or "arena_steering"
  for sub in (
    "models",
    "ports",
    "store",
    "markdown_fields",
    "services",
    "http_transport",
    "server",
  ):
    mod_name = f"{pkg}.{sub}"
    if mod_name in sys.modules:
      importlib.reload(sys.modules[mod_name])
  server_mod = sys.modules.get(f"{pkg}.server")
  store_mod = sys.modules.get(f"{pkg}.store")
  db_path = getattr(app.store, "db_path", ":memory:")
  new_store = (
    store_mod.SqliteStore(db_path)
    if store_mod is not None and db_path != ":memory:"
    else app.store
  )
  app_cls = getattr(server_mod, "SteeringApp", SteeringApp)
  handler_fn = getattr(server_mod, "make_request_handler", make_request_handler)
  new_app = app_cls(
    new_store,
    assets_dir=app.assets_dir,
    repo_name=app.repo_name,
  )
  new_app._asset_tracker = app._asset_tracker
  with app.messages._cond, new_app.messages._cond:
    new_app.messages._survey_answers = dict(app.messages._survey_answers)
    new_app.messages._survey_results = list(app.messages._survey_results)
    new_app.messages._survey_checked_at = app.messages._survey_checked_at
    new_app.messages._survey_pending = app.messages._survey_pending
  after_reload = getattr(app, "_after_reload", None)
  if after_reload is not None:
    after_reload(new_app)
  new_handler = handler_fn(new_app)
  server._steering_app = new_app  # type: ignore[attr-defined]
  server.RequestHandlerClass = new_handler
  retire_store = getattr(app, "_retire_store", None)
  if new_store is not app.store and retire_store is not None:
    retire_store()
  return new_app


def watch_hot_reload(
  server: ThreadingHTTPServer,
  app: SteeringApp,
  stop: threading.Event | None = None,
  interval: float = 1.0,
  scripts_dir: str | Path | None = None,
  quiet_seconds: float = HOT_RELOAD_QUIET_SECONDS,
) -> None:
  """Swap changed Python modules after a quiet period without closing the TCP socket."""
  baseline = scripts_signature(scripts_dir)
  current_app = app
  quiet_since: float | None = None
  while stop is None or not stop.is_set():
    if stop is None:
      time.sleep(interval)
    elif stop.wait(interval):
      return
    current = scripts_signature(scripts_dir)
    now = time.monotonic()
    if current != baseline:
      baseline = current
      quiet_since = now
      continue
    if quiet_since is None or now - quiet_since < quiet_seconds:
      continue
    try:
      current_app = reload_server_handler(server, current_app)
    except Exception as exc:
      print(f"Hot-reload skipped: {exc}", file=sys.stderr, flush=True)
    quiet_since = None


def create_http_server(
  app: SteeringApp,
  host: str = "0.0.0.0",
  port: int = 8100,
) -> ThreadingHTTPServer:
  """Create a ThreadingHTTPServer bound to host:port for SteeringApp."""
  server = ThreadingHTTPServer((host, port), make_request_handler(app))
  server._steering_app = app  # type: ignore[attr-defined]
  if not os.environ.get("PYTEST_CURRENT_TEST"):
    stop_event = threading.Event()
    server._hot_reload_stop = stop_event  # type: ignore[attr-defined]
    threading.Thread(
      target=watch_hot_reload,
      args=(server, app, stop_event),
      daemon=True,
    ).start()
  return server
