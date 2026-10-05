"""
title: Chat metadata
author: nemo
description: Inject the date, the clock, the timezone, the place and the language into the chat as 1 system message at the top. It needs no key.
required_open_webui_version: 0.10.0
version: 1.0.0
licence: daedalus Noncommercial License 1.0.0
"""

import os
from datetime import datetime
from typing import Any

# The marker of the block. The filter replaces its own block, so a repeat does not stack them.
MARK = "[chat metadata]"


def _language(__request__: Any, fallback: str) -> str:
  """The language of the reader: the first value of `Accept-Language`, else the given fallback."""
  headers = getattr(__request__, "headers", None)
  header = headers.get("accept-language") if headers is not None else None
  first = str(header or "").split(",")[0].split(";")[0].strip()
  return first or fallback


class Filter:
  """1 system message at the top of the chat, drawn again on each turn so the clock stays fresh."""

  class Valves:
    # Draw the block. A Valve, so the block can be silenced without uninstalling the Filter.
    enabled: bool = True
    # The place, for example `Antipolo, Calabarzon, Philippines`. Empty draws no place line.
    location: str = ""
    # Say that the place is approximate, so the label reads `Approximate location`.
    location_approximate: bool = False
    # The language, for example `en-PH`. Empty reads the request header, then the server locale.
    language: str = ""

  def __init__(self) -> None:
    self.valves = self.Valves()

  def _block(self, __request__: Any) -> str:
    """The lines of the block, from the clock of the server and the Valves."""
    now = datetime.now().astimezone().replace(microsecond=0)
    zone = now.tzname() or os.environ.get("TZ") or ""
    lines = [MARK, f"Current date/time: {now.isoformat()}"]
    if zone:
      lines.append(f"Timezone: {zone}")
    place = str(getattr(self.valves, "location", "") or "").strip()
    if place:
      label = "Approximate location" if self.valves.location_approximate else "Location"
      lines.append(f"{label}: {place}")
    fallback = str(getattr(self.valves, "language", "") or "").strip()
    lines.append(f"Language: {_language(__request__, fallback) or 'unknown'}")
    return "\n".join(lines)

  async def inlet(
    self, body: dict[str, Any], __request__: Any = None
  ) -> dict[str, Any]:
    """Put the block in as 1 system message at the top, and return the body.

    :param body: the JSON body of the chat request
    :param __request__: the request, for its `Accept-Language` header
    """
    if not getattr(self.valves, "enabled", True):
      return body
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
      return body
    kept = [
      message
      for message in messages
      if not (
        isinstance(message, dict) and str(message.get("content") or "").startswith(MARK)
      )
    ]
    block = {"role": "system", "content": self._block(__request__)}
    return {**body, "messages": [block, *kept]}
