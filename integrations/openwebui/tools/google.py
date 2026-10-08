"""
title: Google
author: nemo
description: Gmail, Calendar, Drive, Docs for Open WebUI. Reads run freely. Writes need a confirmed gate. Stdlib only.
required_open_webui_version: 0.10.0
version: 1.0.1
licence: daedalus Noncommercial License 1.0.0
"""

import asyncio
import base64
import json
import time
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3"
DRIVE = "https://www.googleapis.com/drive/v3"
DOCS = "https://docs.googleapis.com/v1"
USER_AGENT = "daedalus-openwebui-google"
JSON_ACCEPT = "application/json"


class GoogleError(Exception):
  """A Google call failed. The message keeps the status and the API text."""

  def __init__(self, status: int, message: str, method: str = "", url: str = ""):
    self.status = status
    self.message = message
    self.method = method
    self.url = url
    super().__init__(f"Google {status} on {method} {url}: {message}")


class Tools:
  class Valves(BaseModel):
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    calendar_id: str = "primary"
    max_results: int = 10
    permissions: Literal["Always ask", "Allow reads", "Always allow"] = "Always ask"
    timeout_seconds: int = 60
    http_timeout_seconds: int = 30

  class UserValves(BaseModel):
    mode: str = "default"
    timeout_seconds: int = 0

  def __init__(self):
    self.valves = self.Valves()
    self._access = ""
    self._expires_at = 0.0

  # ---------------------------------------------------------------- gate

  @staticmethod
  def _user_valve(__user__: dict | None, name: str) -> Any:
    """Read one user valve, from the model or from a dict."""
    valves = (__user__ or {}).get("valves") or {}
    value = getattr(valves, name, None)
    if value is None and isinstance(valves, dict):
      value = valves.get(name)
    return value

  def _permissions(self, __user__: dict | None = None) -> str:
    """The permission level: the user valve wins, then the tool valve.

    Always ask holds every call, reads included. Allow reads lets a read run
    and holds the gated calls. Always allow holds nothing.
    """
    user_mode = self._user_valve(__user__, "mode")
    if user_mode and user_mode != "default":
      return str(user_mode)
    return str(self.valves.permissions or "Always ask")

  async def _read(
    self,
    action: str,
    detail: str,
    fn: Callable,
    *,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> Any:
    """Run one read behind the read gate.

    The read_mode valve is ask or allow. ask holds every read at the same
    confirmation as a write, so a one time code or a private line cannot reach
    the chat without a click. allow lets the read run at once.
    """
    if self._permissions(__user__) == "Always ask" and not await self._ask(
      action, detail, __event_call__, __user__
    ):
      return {
        "result": {
          "denied": True,
          "action": action,
          "reason": (
            "You did not confirm, so I read nothing. A missing dialog, a closed "
            f"tab or an answer later than {self._wait_seconds(__user__)}s all "
            "read as no."
          ),
        }
      }
    return await fn()

  def _wait_seconds(self, __user__: dict | None = None) -> int:
    """The confirmation wait: the user value wins, then the tool value. Zero keeps the tool value."""
    user_wait = self._user_valve(__user__, "timeout_seconds")
    value = int(user_wait or 0) or int(self.valves.timeout_seconds or 0) or 60
    return max(1, value)

  async def _ask(
    self,
    action: str,
    detail: str,
    __event_call__: Callable | None = None,
    __user__: dict | None = None,
  ) -> bool:
    """Ask the user to confirm one write. Returns False when it cannot be asked.

    The confirmation travels over the socket of the tab that started the chat. A
    page refresh drops the dialog and the server would wait forever, because
    WEBSOCKET_EVENT_CALLER_TIMEOUT starts unset. The wait_for below is the only
    timeout that always exists, so a refresh costs one wait, then a deny.
    """
    if not callable(__event_call__):
      return False
    try:
      answer = await asyncio.wait_for(
        __event_call__(
          {
            "type": "confirmation",
            "data": {"title": f"Confirm: {action}", "message": detail},
          }
        ),
        timeout=self._wait_seconds(__user__),
      )
    except asyncio.TimeoutError:
      return False
    except Exception:
      return False
    return answer is True

  async def _write(
    self,
    action: str,
    detail: str,
    fn: Callable,
    *,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> Any:
    """Run one write behind the gate. deny => never asked, timeout => never sent."""
    if self._permissions(__user__) != "Always allow" and not await self._ask(
      action, detail, __event_call__, __user__
    ):
      return {
        "result": {
          "denied": True,
          "action": action,
          "reason": (
            "You did not confirm, so I sent nothing. A missing dialog, a closed tab "
            f"or an answer later than {self._wait_seconds(__user__)}s all read as no."
          ),
        }
      }
    return await fn()

  # ---------------------------------------------------------------- http

  def _refresh(self) -> str:
    """Trade the refresh token for an access token. Blocking."""
    if not (self.valves.google_client_id and self.valves.google_refresh_token):
      raise GoogleError(
        401,
        "Set google_client_id, google_client_secret and google_refresh_token.",
        "POST",
        TOKEN_URL,
      )
    body = urlencode(
      {
        "grant_type": "refresh_token",
        "client_id": self.valves.google_client_id,
        "client_secret": self.valves.google_client_secret,
        "refresh_token": self.valves.google_refresh_token,
      }
    ).encode()
    request = Request(
      TOKEN_URL,
      data=body,
      method="POST",
      headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": JSON_ACCEPT,
        "User-Agent": USER_AGENT,
      },
    )
    try:
      with urlopen(request, timeout=self.valves.http_timeout_seconds) as response:
        data = json.loads(response.read().decode() or "{}")
    except HTTPError as error:
      raise GoogleError(error.code, self._detail(error), "POST", TOKEN_URL) from error
    except URLError as error:
      raise GoogleError(0, str(error.reason), "POST", TOKEN_URL) from error
    token = data.get("access_token") or ""
    if not token:
      raise GoogleError(
        401, "The token response holds no access_token.", "POST", TOKEN_URL
      )
    self._access = token
    self._expires_at = time.monotonic() + float(data.get("expires_in") or 3600) - 60
    return token

  async def _access_token(self) -> str:
    """The cached access token, refreshed when it is close to expiry."""
    if self._access and time.monotonic() < self._expires_at:
      return self._access
    return await asyncio.to_thread(self._refresh)

  def _http(
    self,
    method: str,
    url: str,
    params: dict | None = None,
    payload: dict | None = None,
    raw: bool = False,
  ) -> Any:
    """One blocking call. The caller holds the token."""
    target = url
    if params:
      clean = {key: value for key, value in params.items() if value is not None}
      if clean:
        target = f"{url}?{urlencode(clean, doseq=True)}"
    data = None
    headers = {
      "Authorization": f"Bearer {self._access}",
      "Accept": JSON_ACCEPT if not raw else "*/*",
      "User-Agent": USER_AGENT,
    }
    if payload is not None:
      data = json.dumps({k: v for k, v in payload.items() if v is not None}).encode()
      headers["Content-Type"] = "application/json"
    request = Request(target, data=data, method=method, headers=headers)
    try:
      with urlopen(request, timeout=self.valves.http_timeout_seconds) as response:
        body = response.read().decode("utf-8", "replace")
    except HTTPError as error:
      raise GoogleError(error.code, self._detail(error), method, target) from error
    except URLError as error:
      raise GoogleError(0, str(error.reason), method, target) from error
    if raw:
      return body
    if not body:
      return {}
    try:
      return json.loads(body)
    except json.JSONDecodeError:
      return {"content": body}

  async def _request(
    self,
    method: str,
    url: str,
    params: dict | None = None,
    payload: dict | None = None,
    raw: bool = False,
  ) -> Any:
    await self._access_token()
    return await asyncio.to_thread(self._http, method, url, params, payload, raw)

  @staticmethod
  def _ok(payload: Any) -> dict:
    return {"result": payload}

  @staticmethod
  def _detail(error: HTTPError) -> str:
    """The error body as text, whatever the error carries."""
    body = error.read()
    if isinstance(body, bytes):
      return body.decode("utf-8", "replace")[:500]
    return str(body or error.reason or "")[:500]

  # ------------------------------------------------------------- pointers

  def _limit(self, value: int) -> int:
    return min(max(1, int(value or self.valves.max_results)), 50)

  @staticmethod
  def _header(message: dict, name: str) -> str:
    for item in (message.get("payload") or {}).get("headers") or []:
      if (item.get("name") or "").lower() == name.lower():
        return item.get("value") or ""
    return ""

  @classmethod
  def _body(cls, payload: dict) -> str:
    """The text of one message payload, plain text first, walking the parts."""
    data = (payload.get("body") or {}).get("data")
    if data and (payload.get("mimeType") or "").startswith("text/"):
      return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
    for part in payload.get("parts") or []:
      text = cls._body(part)
      if text:
        return text
    return ""

  @classmethod
  def _message(cls, message: dict) -> dict:
    text = cls._body(message.get("payload") or {})
    if len(text) > 8000:
      text = text[:8000] + "\n[truncated]"
    return {
      "id": message.get("id"),
      "thread_id": message.get("threadId"),
      "from": cls._header(message, "From"),
      "to": cls._header(message, "To"),
      "subject": cls._header(message, "Subject"),
      "date": cls._header(message, "Date"),
      "snippet": message.get("snippet"),
      "text": text,
    }

  # ---------------------------------------------------------------- mail

  async def search_mail(
    self,
    query: str,
    max_results: int = 0,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Search Gmail and return the newest matches with their headers and snippets.

    :param query: the Gmail search, for example from:ana newer_than:7d or subject:invoice
    :param max_results: how many messages to read, up to 50. Zero uses the valve
    """

    async def run():
      limit = self._limit(max_results)
      found = await self._request(
        "GET", f"{GMAIL}/messages", params={"q": query, "maxResults": limit}
      )
      out = []
      for item in (found.get("messages") or [])[:limit]:
        message = await self._request(
          "GET",
          f"{GMAIL}/messages/{item['id']}",
          params={
            "format": "metadata",
            "metadataHeaders": ["From", "To", "Subject", "Date"],
          },
        )
        out.append(self._message(message))
      return self._ok({"messages": out, "count": len(out)})

    return await self._read(
      "read Gmail",
      str(query),
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def read_thread(
    self,
    thread_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read one Gmail thread, every message in order, with the text of each.

    :param thread_id: the thread id from search_mail
    """

    async def run():
      data = await self._request(
        "GET", f"{GMAIL}/threads/{thread_id}", params={"format": "full"}
      )
      messages = [self._message(item) for item in data.get("messages") or []]
      return self._ok(
        {"thread_id": thread_id, "messages": messages, "count": len(messages)}
      )

    return await self._read(
      "read a Gmail thread",
      str(thread_id),
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def send_mail(
    self,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Send one mail from the signed-in account. The gate asks first.

    :param to: the recipient, comma separated for several
    :param subject: the subject line
    :param body: the plain text body
    :param cc: the cc list
    :param bcc: the bcc list
    """
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if cc:
      message["Cc"] = cc
    if bcc:
      message["Bcc"] = bcc
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    async def run():
      data = await self._request("POST", f"{GMAIL}/messages/send", payload={"raw": raw})
      return self._ok(
        {
          "id": data.get("id"),
          "thread_id": data.get("threadId"),
          "to": to,
          "subject": subject,
        }
      )

    return await self._write(
      f"send mail to {to}",
      f"subject: {subject}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # ------------------------------------------------------------ calendar

  async def agenda(
    self,
    days: int = 7,
    calendar_id: str | None = None,
    max_results: int = 0,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read the next events of a calendar, the primary one by default.

    :param days: how many days ahead to read, from 1 to 60
    :param calendar_id: the calendar id, primary when absent
    :param max_results: how many events, up to 50. Zero uses the valve
    """

    async def run():
      span = min(max(1, int(days or 7)), 60)
      now = time.time()
      start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
      end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + span * 86400))
      data = await self._request(
        "GET",
        f"{CALENDAR}/calendars/{calendar_id or self.valves.calendar_id or 'primary'}/events",
        params={
          "timeMin": start,
          "timeMax": end,
          "singleEvents": "true",
          "orderBy": "startTime",
          "maxResults": self._limit(max_results),
        },
      )
      events = [
        {
          "id": item.get("id"),
          "summary": item.get("summary"),
          "start": (item.get("start") or {}).get("dateTime")
          or (item.get("start") or {}).get("date"),
          "end": (item.get("end") or {}).get("dateTime")
          or (item.get("end") or {}).get("date"),
          "location": item.get("location"),
          "status": item.get("status"),
          "link": item.get("htmlLink"),
        }
        for item in data.get("items") or []
      ]
      return self._ok(
        {"events": events, "count": len(events), "from": start, "to": end}
      )

    return await self._read(
      "read the calendar",
      str(days),
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def create_event(
    self,
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    calendar_id: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create one calendar event. The gate asks first.

    :param summary: the event title
    :param start: the start in RFC 3339, for example 2026-10-06T09:00:00+08:00
    :param end: the end in RFC 3339
    :param description: the event notes
    :param location: the place
    :param calendar_id: the calendar id, primary when absent
    """

    async def run():
      data = await self._request(
        "POST",
        f"{CALENDAR}/calendars/{calendar_id or self.valves.calendar_id or 'primary'}/events",
        payload={
          "summary": summary,
          "start": {"dateTime": start},
          "end": {"dateTime": end},
          "description": description,
          "location": location,
        },
      )
      return self._ok(
        {
          "id": data.get("id"),
          "summary": summary,
          "start": start,
          "end": end,
          "link": data.get("htmlLink"),
        }
      )

    return await self._write(
      f"create the event {summary}",
      f"{start} to {end}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # --------------------------------------------------------------- drive

  async def search_files(
    self,
    query: str,
    max_results: int = 0,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Search Drive and return the matching files.

    :param query: a Drive query, or plain words. Plain words search the full text
    :param max_results: how many files, up to 50. Zero uses the valve
    """

    async def run():
      text = query or ""
      if "contains" not in text and "=" not in text:
        text = f"fullText contains '{text.replace(chr(39), chr(92) + chr(39))}'"
      data = await self._request(
        "GET",
        f"{DRIVE}/files",
        params={
          "q": text,
          "pageSize": self._limit(max_results),
          "fields": "files(id,name,mimeType,modifiedTime,webViewLink,owners(displayName))",
        },
      )
      return self._ok(
        {"files": data.get("files") or [], "count": len(data.get("files") or [])}
      )

    return await self._read(
      "search Drive",
      str(query),
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def read_document(
    self,
    file_id: str,
    max_chars: int = 20000,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read the text of a Drive file. The tool exports a Google Doc, and downloads any other file.

    :param file_id: the file id from search_files
    :param max_chars: the text limit, up to 200000
    """

    async def run():
      meta = await self._request(
        "GET", f"{DRIVE}/files/{file_id}", params={"fields": "id,name,mimeType"}
      )
      kind = meta.get("mimeType") or ""
      limit = min(max(1000, int(max_chars or 20000)), 200000)
      if kind == "application/vnd.google-apps.document":
        text = await self._request(
          "GET",
          f"{DRIVE}/files/{file_id}/export",
          params={"mimeType": "text/plain"},
          raw=True,
        )
      else:
        text = await self._request(
          "GET", f"{DRIVE}/files/{file_id}", params={"alt": "media"}, raw=True
        )
      truncated = len(text) > limit
      return self._ok(
        {
          "file_id": file_id,
          "name": meta.get("name"),
          "mime_type": kind,
          "text": text[:limit],
          "truncated": truncated,
        }
      )

    return await self._read(
      "read a Drive file",
      str(file_id),
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def append_to_document(
    self,
    document_id: str,
    text: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Append text to the end of a Google Doc. The gate asks first.

    :param document_id: the document id from search_files
    :param text: the text to add, with a leading newline when wanted
    """

    async def run():
      await self._request(
        "POST",
        f"{DOCS}/documents/{document_id}:batchUpdate",
        payload={
          "requests": [{"insertText": {"endOfSegmentLocation": {}, "text": text}}]
        },
      )
      return self._ok({"document_id": document_id, "appended": len(text)})

    return await self._write(
      f"append {len(text)} characters to a document",
      f"document: {document_id}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )
