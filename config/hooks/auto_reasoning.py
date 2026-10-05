"""The reasoning ladder of 1 chat: the level of a call, and the next rung of the light-bulb.

1 file holds the 2 intertwined surfaces of the ladder:

  on_prompt   the point `request_hooks.on-prompt`: the level of the message at hand
  on_http     the route `POST /v1/hook/auto_reasoning`: the next rung, for a client

Both surfaces read the heuristics v2 tier of a prompt. The point reads the message at hand,
so each message of a chat gets its own level, and it serves the client of `CLIENT` alone. The route starts from the rung of the last
answer: its pool, or the tier of its `daedalus/auto` slot, and then the read of the prompt
when the chat has no answer yet. A chat that already sits on `TIER-A` keeps that rung, and
`top` says so.
"""

from typing import Any

from daedalus.routing import router
from daedalus.server import api

# The client the `on-prompt` point serves. The base names the app from its headers: `OWUI`,
# `Kilo`, another title, or None. Set it to None to serve every client.
CLIENT = "OWUI"


def _rung(slot: str | None, prompt: str) -> int:
  """The tier of the chat: its slot, else the tier read of its prompt."""
  if slot in router.POOLS:
    return router.POOLS[slot]
  if slot and slot.startswith(f"{router.RESERVED_MODEL}:"):
    name = slot.rpartition(":")[2]
    tier = next(
      (value for value, known in router.TIER_NAMES.items() if known == name), None
    )
    if tier is not None:
      return tier
  return int(router.required_tier(prompt or ""))


def on_prompt(
  value: dict,
  prompt: str = "",
  effort: str | None = None,
  app: str | None = None,
  **context: Any,
) -> None:
  """Set the reasoning level of the call from the heuristics v2 tier of the prompt.

  Only the client of `CLIENT` gets the read, so a Kilo request or a generic client keeps its
  own effort. A client value keeps the last word. This call answers above the catalog
  default, which is what a hook file does, so the read wins over a stored effort.

  :param value: the request values, holding `reasoning_effort`
  :param prompt: the user turns joined
  :param effort: the value of the client, `None` when it sent none
  :param app: the client app of the request, from its headers
  :param context: the other surfaces of the point
  """
  if effort or (CLIENT is not None and app != CLIENT):
    return
  tier = int(router.required_tier(prompt or ""))
  value["reasoning_effort"] = api.EFFORT_OF_TIER[router.TIER_NAMES[tier]]


def on_http(
  body: dict[str, Any], key: str = "", prompt: str = "", headers: dict | None = None
) -> dict[str, Any]:
  """The next rung of the ladder of the chat: pool, tier and effort.

  :param body: the JSON body of the call
  :param key: the session key of the chat, from the bearer token and its first user turn
  :param prompt: the first user turn of the chat
  :param headers: the request headers
  """
  found = api.PENALTIES.last_pin(key) if key else None
  before = _rung(found[0] if found else None, prompt)
  tier = min(before + 1, max(router.TIERS))
  name = router.TIER_NAMES[tier]
  pool = next(
    (
      router.pool_name(pool_name).removeprefix("daedalus/")
      for pool_name, value in router.POOLS.items()
      if value == tier
    ),
    None,
  )
  return {
    "model": f"daedalus/{pool}",
    "pool": pool,
    "tier": tier,
    "tier_name": name,
    "reasoning_effort": api.EFFORT_OF_TIER[name],
    "before": {
      "tier": before,
      "tier_name": router.TIER_NAMES[before],
      "reasoning_effort": api.EFFORT_OF_TIER[router.TIER_NAMES[before]],
    },
    "top": tier == before,
  }
