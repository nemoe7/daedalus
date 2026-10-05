"""
title: Reasoning bump
author: nemo
description: The light-bulb action of the message toolbar. 1 press moves the chat 1 rung up the reasoning ladder of daedalus and answers the pressed turn again on that rung. The ladder is the hook file config/hooks/owui_auto_reasoning.py.
required_open_webui_version: 0.11.0
version: 1.0.0
licence: MIT
"""

import asyncio
import json
from typing import Any

import aiohttp
from pydantic import BaseModel, Field

# The light bulb of the button, as a data URI. Open WebUI draws the action icon from it.
icon_url = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjMDAwIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTkgMThoNiIvPjxwYXRoIGQ9Ik0xMCAyMmg0Ii8+PHBhdGggZD0iTTEyIDJhNyA3IDAgMCAwLTQgMTIuN1YxOGg4di0zLjNBNyA3IDAgMCAwIDEyIDJ6Ii8+PC9zdmc+"

# The hook file of the ladder, and the chat endpoint of daedalus.
LADDER = "/v1/hook/owui_auto_reasoning"
CHAT = "/v1/chat/completions"


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
    raise ValueError("daedalus sent no answer text")
  return text


class Action:
  """The reasoning ladder of the pressed message: 1 press, 1 rung, 1 new answer."""

  class Valves(BaseModel):
    DAEDALUS_API_BASE: str = "http://127.0.0.1:3357"
    DAEDALUS_API_KEY: str = Field(
      default="", json_schema_extra={"input": {"type": "password"}}
    )
    timeout_seconds: int = 300
    SHOW_STATUS: bool = True

  def __init__(self) -> None:
    self.valves = self.Valves()

  def _base(self) -> str:
    """The daedalus base URL, from the valve, with no trailing slash."""
    base = (self.valves.DAEDALUS_API_BASE or "").strip() or "http://127.0.0.1:3357"
    return base.rstrip("/")

  def _headers(self) -> dict[str, str]:
    """The bearer header of the daedalus key."""
    return {
      "Authorization": f"Bearer {self.valves.DAEDALUS_API_KEY}",
      "Content-Type": "application/json",
    }

  def _line(self, plan: dict[str, Any]) -> str:
    """The status line of 1 bump: the pool, the effort, and the top mark."""
    line = f"{plan.get('model', '')} · {plan.get('reasoning_effort', '')}"
    return f"{line} · top" if plan.get("top") else line

  async def _open_session(self) -> Any:
    """The HTTP session of the call, with the valve timeout."""
    return aiohttp.ClientSession(
      timeout=aiohttp.ClientTimeout(total=float(self.valves.timeout_seconds))
    )

  async def _post(
    self, session: Any, path: str, payload: dict[str, Any]
  ) -> dict[str, Any]:
    """One JSON POST. The dict answer, or a `ValueError` that names the route."""
    try:
      async with session.post(
        f"{self._base()}{path}", json=payload, headers=self._headers()
      ) as response:
        text = await response.text()
        status = response.status
    except asyncio.TimeoutError as exc:
      raise ValueError(
        f"daedalus did not answer {path} within {self.valves.timeout_seconds}s"
      ) from exc
    except aiohttp.ClientError as exc:
      raise ValueError(f"daedalus is unreachable on {path}: {exc}") from exc
    if status != 200:
      raise ValueError(f"daedalus answered {status} on {path}: {text[:200]}")
    try:
      found = json.loads(text)
    except ValueError as exc:
      raise ValueError(f"daedalus sent no JSON on {path}") from exc
    if not isinstance(found, dict):
      raise TypeError(f"daedalus sent no JSON object on {path}")
    return found

  async def action(
    self, body: dict[str, Any], __event_emitter__: Any = None
  ) -> dict[str, Any]:
    """Move the chat 1 rung up the ladder, and answer the pressed turn again on it.

    :param body: the call of Open WebUI: the messages of the chat and the pressed message id
    :param __event_emitter__: the Open WebUI event emitter
    """
    if not self.valves.DAEDALUS_API_KEY:
      raise ValueError("set the DAEDALUS_API_KEY valve")
    messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
    if not messages:
      raise ValueError("no messages in the call")
    pressed = str(body.get("id") or messages[-1].get("id") or "")
    if not pressed:
      raise ValueError("no message id in the call")
    async with await self._open_session() as session:
      plan = await self._post(
        session, LADDER, {"messages": _wire(messages), "model": body.get("model")}
      )
      if not plan.get("model"):
        raise ValueError("the ladder hook answered no model")
      answer = await self._post(
        session,
        CHAT,
        {
          "model": plan["model"],
          "messages": _wire(_context(messages)),
          "reasoning_effort": plan.get("reasoning_effort"),
          "stream": False,
        },
      )
    if self.valves.SHOW_STATUS and callable(__event_emitter__):
      await __event_emitter__(
        {"type": "status", "data": {"description": self._line(plan), "done": True}}
      )
    found = next((m for m in messages if str(m.get("id") or "") == pressed), None)
    kept = {
      key: value
      for key, value in (found or {"role": "assistant"}).items()
      if key != "error"
    }
    return {"messages": [{"id": pressed, **kept, "content": _answer(answer)}]}
