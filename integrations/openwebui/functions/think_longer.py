"""
title: Think longer
author: nemo
description: The light-bulb action of the message toolbar. 1 press asks daedalus to think longer on the pressed turn, 1 reasoning level step up, and answers that turn again at that level. The action holds no daedalus key: it asks Open WebUI, which holds the key in its connection. The rule is the hook file config/hooks/owui_think_longer.py.
required_open_webui_version: 0.11.0
version: 1.0.1
licence: daedalus Noncommercial License 1.0.0
"""

import asyncio
import json
from typing import Any

import aiohttp
from pydantic import BaseModel

# The chat route of Open WebUI. The action asks Open WebUI for the answer, so it needs no
# daedalus key, and the daedalus connection of Open WebUI carries the call.
CHAT = "/api/chat/completions"

# The body field that asks daedalus for the next reasoning level.
FIELD = "think_longer"
STEPS = 1

# The light bulb of the button, as a data URI. Open WebUI draws the action icon from it.
icon_url = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjMDAwIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTkgMThoNiIvPjxwYXRoIGQ9Ik0xMCAyMmg0Ii8+PHBhdGggZD0iTTEyIDJhNyA3IDAgMCAwLTQgMTIuN1YxOGg4di0zLjNBNyA3IDAgMCAwIDEyIDJ6Ii8+PC9zdmc+"


def _context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
  """The list up to and including the last user turn: the turn to answer again."""
  cut = 0
  for index, message in enumerate(messages):
    if message.get("role") == "user":
      cut = index + 1
  return messages[:cut] or list(messages)


def _wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
  """The role and the text of each message, in the shape the chat API takes."""
  return [
    {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
    for m in messages
  ]


def _answer(data: dict[str, Any]) -> str:
  """The text of the first choice of a full chat answer."""
  choices = data.get("choices") if isinstance(data, dict) else None
  first = choices[0] if choices and isinstance(choices[0], dict) else None
  message = first.get("message") if first else None
  text = message.get("content") if isinstance(message, dict) else None
  if not isinstance(text, str) or not text.strip():
    raise ValueError("Open WebUI sent no answer text")
  return text


class Action:
  """The think-longer press of 1 message: 1 press, 1 step, 1 new answer."""

  class Valves(BaseModel):
    timeout_seconds: int = 300
    SHOW_STATUS: bool = True

  def __init__(self) -> None:
    self.valves = self.Valves()

  def _base(self, request: Any) -> str:
    """The base URL of Open WebUI, from the request the action itself handles."""
    base = str(getattr(request, "base_url", "") or "").strip()
    return base.rstrip("/")

  def _headers(self, request: Any) -> dict[str, str]:
    """The caller headers that the chat route of Open WebUI takes: its token or its cookie."""
    found = getattr(request, "headers", None) or {}
    headers = {"Content-Type": "application/json"}
    for name in ("authorization", "cookie"):
      value = found.get(name) if hasattr(found, "get") else None
      if value:
        headers[name.title()] = value
    return headers

  def _line(self, model: str) -> str:
    """The status line of 1 press: the model, and the step the press asked for."""
    return f"{model} · think longer +{STEPS}"

  async def _open_session(self) -> Any:
    """The HTTP session of the call, with the valve timeout."""
    return aiohttp.ClientSession(
      timeout=aiohttp.ClientTimeout(total=float(self.valves.timeout_seconds))
    )

  async def _post(
    self, session: Any, url: str, payload: dict[str, Any], headers: dict[str, str]
  ) -> dict[str, Any]:
    """One JSON POST. The dict answer, or a `ValueError` that names the route."""
    try:
      async with session.post(url, json=payload, headers=headers) as response:
        text = await response.text()
        status = response.status
    except asyncio.TimeoutError as exc:
      raise ValueError(
        f"Open WebUI did not answer {url} within {self.valves.timeout_seconds}s"
      ) from exc
    except aiohttp.ClientError as exc:
      raise ValueError(f"Open WebUI is unreachable on {url}: {exc}") from exc
    if status != 200:
      raise ValueError(f"Open WebUI answered {status} on {url}: {text[:200]}")
    try:
      found = json.loads(text)
    except ValueError as exc:
      raise ValueError(f"Open WebUI sent no JSON on {url}") from exc
    if not isinstance(found, dict):
      raise TypeError(f"Open WebUI sent no JSON object on {url}")
    return found

  async def action(
    self,
    body: dict[str, Any],
    __event_emitter__: Any = None,
    __request__: Any = None,
  ) -> dict[str, Any]:
    """Ask daedalus for the next reasoning level, and answer the pressed turn again.

    :param body: the call of Open WebUI: the messages of the chat and the pressed message id
    """
    if __request__ is None:
      raise ValueError("Open WebUI sent no request")
    messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
    if not messages:
      raise ValueError("no messages in the call")
    pressed = str(body.get("id") or messages[-1].get("id") or "")
    if not pressed:
      raise ValueError("no message id in the call")
    model = str(body.get("model") or "")
    if not model:
      raise ValueError("no model in the call")
    url = f"{self._base(__request__)}{CHAT}"
    async with await self._open_session() as session:
      answer = await self._post(
        session,
        url,
        {
          "model": model,
          "messages": _wire(_context(messages)),
          FIELD: STEPS,
          "stream": False,
        },
        self._headers(__request__),
      )
    if self.valves.SHOW_STATUS and callable(__event_emitter__):
      await __event_emitter__(
        {"type": "status", "data": {"description": self._line(model), "done": True}}
      )
    found = next((m for m in messages if str(m.get("id") or "") == pressed), None)
    kept = {
      key: value
      for key, value in (found or {"role": "assistant"}).items()
      if key != "error"
    }
    return {"messages": [{"id": pressed, **kept, "content": _answer(answer)}]}
