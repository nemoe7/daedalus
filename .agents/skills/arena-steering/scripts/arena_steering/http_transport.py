"""HTTP and WebSocket request transport for Arena Steering."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlsplit

if TYPE_CHECKING:
  from .server import SteeringApp


def make_request_handler(app: SteeringApp) -> type[BaseHTTPRequestHandler]:
  """Create a BaseHTTPRequestHandler bound to `app`."""

  from . import server

  class SteeringRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
      return

    def _current_app(self) -> SteeringApp:
      server = getattr(self, "server", None)
      return getattr(server, "_steering_app", app)

    @contextmanager
    def _app_lease(self) -> Iterator[SteeringApp]:
      while True:
        current = self._current_app()
        if current._begin_request():
          break
      try:
        yield current
      finally:
        current._end_request()

    def do_OPTIONS(self) -> None:
      self.send_response(int(HTTPStatus.NO_CONTENT))
      self._send_common_headers("text/plain; charset=utf-8", 0)
      self.end_headers()

    def do_GET(self) -> None:
      if urlparse(self.path).path.rstrip("/") == "/api/ws":
        self._handle_websocket()
        return
      with self._app_lease() as current:
        self._handle_get(current)

    def _handle_get(self, app: SteeringApp) -> None:
      parsed = urlparse(self.path)
      route_path = parsed.path.rstrip("/") or "/"
      if app.is_static_request(route_path):
        asset = app.resolve_asset(route_path)
        if asset is None:
          self._write_json(
            int(HTTPStatus.NOT_FOUND), {"error": f"Asset not found: {parsed.path}"}
          )
          return
        raw_bytes, content_type = asset
        self.send_response(int(HTTPStatus.OK))
        self._send_common_headers(content_type, len(raw_bytes))
        self.end_headers()
        self.wfile.write(raw_bytes)
        return
      handler, _, allowed = app.router.match("GET", route_path)
      if handler is not None or allowed:
        status, payload = app.dispatch("GET", self.path)
        self._write_json(status, payload)
        return
      asset = app.resolve_asset(parsed.path)
      if asset is None:
        self._write_json(
          int(HTTPStatus.NOT_FOUND), {"error": f"Asset not found: {parsed.path}"}
        )
        return
      raw_bytes, content_type = asset
      self.send_response(int(HTTPStatus.OK))
      self._send_common_headers(content_type, len(raw_bytes))
      self.end_headers()
      self.wfile.write(raw_bytes)

    def ws_send(self, opcode: int, data: bytes) -> None:
      size = len(data)
      length = (
        bytes([size])
        if size < 126
        else (
          b"\x7e" + size.to_bytes(2, "big")
          if size < 65536
          else b"\x7f" + size.to_bytes(8, "big")
        )
      )
      self.connection.sendall(bytes([0x80 | opcode]) + length + data)

    def ws_dispatch(self, value: Any) -> None:
      with self._app_lease() as current:
        self._dispatch_websocket(current, value)

    def _dispatch_websocket(self, app: SteeringApp, value: Any) -> None:
      if not isinstance(value, dict):
        raise TypeError("Expected an object")
      if value.get("type") == "pong":
        return
      request_id = value.get("id")
      method = str(value.get("method", "GET")).upper()
      path = str(value.get("path", ""))
      raw_body = value.get("body", "")
      if (
        type(request_id) not in (str, int)
        or len(str(request_id)) > 80
        or method not in ("GET", "POST", "PATCH", "DELETE")
        or not path.startswith("/")
        or urlsplit(path).path == "/api/ws"
        or not isinstance(raw_body, str)
      ):
        raise ValueError("Invalid WebSocket request")
      body_dict: dict[str, Any] = {}
      if raw_body.strip():
        parsed_body = json.loads(raw_body)
        if not isinstance(parsed_body, dict):
          raise ValueError("JSON request body must be an object")
        body_dict = parsed_body
      if server._is_blocked_bash_path(path):
        status, payload = (
          int(HTTPStatus.FORBIDDEN),
          {"error": "Blocked Bash log requires same-origin HTTP"},
        )
      else:
        status, payload = app.dispatch(method, path, body_dict)
      reply = json.dumps(
        {
          "type": "response",
          "id": request_id,
          "status": status,
          "contentType": "application/json; charset=utf-8",
          "body": json.dumps(payload),
        }
      ).encode("utf-8")
      self.ws_send(1, reply)

    def _handle_websocket(self) -> None:
      if (
        self.headers.get("Upgrade", "").lower() != "websocket"
        or "upgrade"
        not in {
          part.strip() for part in self.headers.get("Connection", "").lower().split(",")
        }
        or self.headers.get("Sec-WebSocket-Version") != "13"
      ):
        self._write_json(
          int(HTTPStatus.BAD_REQUEST),
          {"error": "Expected a version 13 WebSocket upgrade"},
        )
        return
      key = self.headers.get("Sec-WebSocket-Key", "")
      try:
        if len(base64.b64decode(key, validate=True)) != 16:
          raise ValueError("Invalid WebSocket key")
      except ValueError as exc:
        self._write_json(int(HTTPStatus.BAD_REQUEST), {"error": str(exc)})
        return
      self.protocol_version = "HTTP/1.1"
      self.send_response(101)
      self.send_header("Upgrade", "websocket")
      self.send_header("Connection", "Upgrade")
      accept = base64.b64encode(
        hashlib.sha1(
          (key + server.WS_GUID).encode("utf-8"),
          usedforsecurity=False,
        ).digest()
      ).decode("ascii")
      self.send_header("Sec-WebSocket-Accept", accept)
      self.end_headers()
      self.close_connection = True
      self.connection.settimeout(server.WS_TICK_SECONDS)
      buffer = bytearray()
      message = bytearray()
      fragmented = False
      last_seen = last_heartbeat = time.monotonic()
      try:
        with self._app_lease() as bound_app:
          version = bound_app.data_version()
          ack_snapshot = bound_app.report_ack_snapshot()
          asset_ver = bound_app.asset_signature_hash()
          restore_notice = bound_app.store.get_meta("state_restore_notice", "")
        self.ws_send(1, b'{"type":"change"}')
        while True:
          try:
            chunk = self.connection.recv(65536)
            if not chunk:
              return
            buffer.extend(chunk)
            last_seen = time.monotonic()
          except TimeoutError:
            pass
          while len(buffer) >= 2:
            first, second = buffer[:2]
            opcode, final = first & 15, bool(first & 128)
            if first & 112 or not (second & 128) or opcode not in (0, 1, 2, 8, 9, 10):
              raise ValueError("Invalid frame")
            size, offset = second & 127, 2
            if size in (126, 127):
              width = 2 if size == 126 else 8
              if len(buffer) < 2 + width:
                break
              size = int.from_bytes(buffer[2 : 2 + width], "big")
              offset += width
            if opcode >= 8 and (not final or size > 125):
              raise ValueError("Invalid control frame")
            if size + (len(message) if opcode < 8 else 0) > server.WS_MAX_MESSAGE:
              self.ws_send(8, (1009).to_bytes(2, "big"))
              return
            if len(buffer) < offset + 4 + size:
              break
            mask = buffer[offset : offset + 4]
            data = bytes(
              v ^ mask[i % 4]
              for i, v in enumerate(buffer[offset + 4 : offset + 4 + size])
            )
            del buffer[: offset + 4 + size]
            if opcode == 8:
              self.ws_send(8, data[:2] if len(data) >= 2 else b"")
              return
            if opcode == 9:
              self.ws_send(10, data)
              continue
            if opcode == 10:
              continue
            if opcode == 2:
              self.ws_send(8, (1003).to_bytes(2, "big"))
              return
            if (opcode == 0) != fragmented:
              raise ValueError("Invalid continuation")
            message.extend(data)
            fragmented = not final
            if final:
              self.ws_dispatch(json.loads(message.decode("utf-8")))
              message.clear()
          with self._app_lease() as current_app:
            if current_app is not bound_app:
              bound_app = current_app
              version = bound_app.data_version()
              ack_events, ack_snapshot = bound_app.report_acked_events(ack_snapshot)
              for ack_event in ack_events:
                self.ws_send(1, json.dumps(ack_event).encode("utf-8"))
              self.ws_send(1, b'{"type":"change"}')
            current = bound_app.data_version()
            if current != version:
              version = current
              ack_events, ack_snapshot = bound_app.report_acked_events(ack_snapshot)
              for ack_event in ack_events:
                self.ws_send(1, json.dumps(ack_event).encode("utf-8"))
              self.ws_send(1, b'{"type":"change"}')
            current_notice = bound_app.store.get_meta("state_restore_notice", "")
            if current_notice and current_notice != restore_notice:
              restore_notice = current_notice
              self.ws_send(
                1,
                json.dumps({"type": "state_restored", "notice": current_notice}).encode(
                  "utf-8"
                ),
              )
            current_assets = bound_app.asset_signature_hash()
            if current_assets != asset_ver:
              asset_ver = current_assets
              self.ws_send(1, b'{"type":"reload"}')
            now = time.monotonic()
            if now - last_seen > server.WS_IDLE_SECONDS:
              self.ws_send(8, (1001).to_bytes(2, "big"))
              return
            if now - last_heartbeat >= server.WS_HEARTBEAT_SECONDS:
              self.ws_send(1, b'{"type":"heartbeat"}')
              last_heartbeat = now
      except (ValueError, TypeError) as error:
        code = 1007 if isinstance(error, UnicodeDecodeError) else 1002
        try:
          self.ws_send(8, code.to_bytes(2, "big"))
        except OSError:
          pass
      except (OSError, sqlite3.Error):
        return

    def do_POST(self) -> None:
      self._handle_json_method("POST")

    def do_PATCH(self) -> None:
      self._handle_json_method("PATCH")

    def do_DELETE(self) -> None:
      self._handle_json_method("DELETE")

    def _handle_json_method(self, method: str) -> None:
      try:
        content_type = self.headers.get("Content-Type", "")
        if content_type.lower().startswith("multipart/form-data"):
          body = self._read_multipart_body(content_type)
        else:
          body = self._read_json_body()
      except (TypeError, ValueError) as exc:
        self._write_json(int(HTTPStatus.BAD_REQUEST), {"error": str(exc)})
        return
      with self._app_lease() as current:
        status, payload = current.dispatch(method, self.path, body)
        self._write_json(status, payload)

    def _read_multipart_body(self, content_type: str) -> dict[str, Any]:
      boundary_match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type)
      if boundary_match is None:
        raise ValueError("Multipart boundary is required")
      boundary = (boundary_match.group(1) or boundary_match.group(2)).strip()
      length = int(self.headers.get("Content-Length", "0") or "0")
      raw = self.rfile.read(length) if length > 0 else b""
      body: dict[str, Any] = {}
      marker = b"--" + boundary.encode("utf-8")
      for part in raw.split(marker):
        if part in (b"", b"--", b"--\r\n", b"\r\n"):
          continue
        if part.startswith(b"\r\n"):
          part = part[2:]
        if part.endswith(b"\r\n"):
          part = part[:-2]
        if part.endswith(b"--"):
          part = part[:-2].rstrip(b"\r\n")
        header_blob, separator, data = part.partition(b"\r\n\r\n")
        if not separator:
          continue
        headers = header_blob.decode("utf-8", errors="replace")
        disposition = ""
        for line in headers.split("\r\n"):
          if line.lower().startswith("content-disposition:"):
            disposition = line
            break
        name_match = re.search(r'name="([^"]+)"', disposition)
        if name_match is None:
          continue
        field_name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        if filename_match is not None and filename_match.group(1):
          body["file"] = {
            "name": filename_match.group(1),
            "content_base64": base64.b64encode(data).decode("ascii"),
          }
        else:
          body[field_name] = data.decode("utf-8")
      return body

    def _read_json_body(self) -> dict[str, Any]:
      length = int(self.headers.get("Content-Length", "0") or "0")
      if length <= 0:
        return {}
      raw = self.rfile.read(length).decode("utf-8")
      if not raw.strip():
        return {}
      try:
        parsed = json.loads(raw)
      except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON request body: {exc}") from exc
      if not isinstance(parsed, dict):
        raise TypeError("JSON request body must be an object")
      return parsed

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
      encoded = json.dumps(payload).encode("utf-8")
      self.send_response(status)
      self._send_common_headers("application/json; charset=utf-8", len(encoded))
      self.end_headers()
      self.wfile.write(encoded)

    def _send_common_headers(self, content_type: str, length: int) -> None:
      if not isinstance(content_type, str) or not server.HTTP_CONTENT_TYPE_RE.fullmatch(
        content_type
      ):
        content_type = "application/octet-stream"
      self.send_header("Content-Type", content_type)
      self.send_header("Content-Length", str(length))
      self.send_header("Cache-Control", "no-store")
      if server._is_blocked_bash_path(self.path):
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
      else:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
          "Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

  return SteeringRequestHandler
