"""Open WebUI Filter: the daedalus pick line, as a status above the answer body.

Install this file as a Filter in the Admin Panel and turn it on. It reads `usage.daedalus`
of the stream chunks, which `config/hooks/model_served.py` writes on the chat pools, and draws the
line. The Filter itself starts nothing: no key, no line.
"""

from typing import Any


class Filter:
  """One status line above the answer body, from the pick key of the final chunk."""

  class Valves:
    # Draw the line. A Valve, so the pick can be silenced without uninstalling the Filter.
    enabled: bool = True
    # Text before the line, for example `served`. Empty by default.
    prefix: str = ""

  def __init__(self) -> None:
    self.valves = self.Valves()

  async def stream(
    self, event: dict[str, Any], __event_emitter__: Any = None
  ) -> dict[str, Any]:
    """Read one stream chunk, draw the line when it carries the pick, and pass the chunk on."""
    if getattr(self.valves, "enabled", True) and callable(__event_emitter__):
      usage = event.get("usage") if isinstance(event, dict) else None
      pick = usage.get("daedalus") if isinstance(usage, dict) else None
      line = pick.get("line") if isinstance(pick, dict) else None
      if isinstance(line, str) and line:
        await __event_emitter__(
          {
            "type": "status",
            "data": {
              "description": f"{getattr(self.valves, 'prefix', '')}{line}",
              "done": True,
            },
          }
        )
    return event
