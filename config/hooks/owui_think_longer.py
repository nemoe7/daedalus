"""The think-longer ladder of 1 chat: the reasoning level of a call, and the step of a press.

1 file holds the 2 surfaces of the ladder:

  on_prompt   the point `request_hooks.on-prompt`: the level of the message at hand
  on_http     the route `POST /v1/hook/owui_think_longer`: the next level, for a client

The point reads the heuristics v2 tier of a prompt, which is the classified level of the message,
so each message of a chat gets its own level, and it serves the client of `CLIENT` alone. 3 things
step that level 1 up or more: a `think_longer` count of the request body, a try again of the
message, and the level of the last answer. The route serves a press: the same model of the chat,
and the level of its last answer 1 step up. The cap of the ladder is `high`, and `top` says that
the chat sits there.
"""

import logging
from typing import Any

from daedalus.routing import router

logger = logging.getLogger("daedalus.hooks")

# The client the `on-prompt` point serves. The base names the app from its headers: `OWUI`,
# `Kilo`, another title, or None. Set it to None to serve every client.
CLIENT = "OWUI"

# The body field that asks for more thinking: the count of steps above the last answer.
FIELD = "think_longer"

# The level of each tier, from `TIER-D` none to `TIER-A` high.
LEVELS = {1: "none", 2: "low", 3: "medium", 4: "high"}
# The tier of each level.
TIER_OF = {level: tier for tier, level in LEVELS.items()}
# The top of the ladder, `TIER-A`.
TOP = max(router.TIERS)


def _bump(body: dict | None) -> int:
  """The steps that the body asks for, from its `think_longer` field. 0 when it asks for none."""
  field = body.get(FIELD) if isinstance(body, dict) else None
  if isinstance(field, bool):
    return int(field)
  if isinstance(field, int) and not isinstance(field, bool) and field > 0:
    return field
  if isinstance(field, str) and field.isdigit():
    return int(field)
  return 0


def _tier_of(level: str | None, prompt: str) -> int:
  """The tier of a reasoning level, else the read of the prompt."""
  return TIER_OF.get(level or "", int(router.required_tier(prompt or "")))


def _prompt(body: dict[str, Any], prompt: str) -> str:
  """The user turns of the call joined, else the prompt of the route."""
  messages = body.get("messages")
  if isinstance(messages, list):
    joined = "\n".join(
      str(message.get("content") or "")
      for message in messages
      if isinstance(message, dict) and message.get("role") == "user"
    )
    if joined.strip():
      return joined
  return prompt or ""


def on_prompt(
  value: dict,
  prompt: str = "",
  effort: str | None = None,
  app: str | None = None,
  retry: int = 0,
  level: str | None = None,
  body: dict | None = None,
  **context: Any,
) -> None:
  """Set the reasoning level of the call: the read of the prompt, the steps asked, the try again.

  A `think_longer` field of the body steps the level of the last answer by its count, for any
  client, and it wins over a client value. A try again steps the same level 1 up, so a repeat of a
  message reasons more than the answer before it. With neither, the level is the read of the
  prompt, and only the client of `CLIENT` gets it, so a Kilo request or a generic client keeps its
  own effort. A client value keeps the last word over the read of the prompt. This call answers
  above the catalog default and above the client value, which is what a hook file does.

  :param value: the request values, holding `reasoning_effort`
  :param prompt: the user turns joined
  :param effort: the value of the client, `None` when it sent none
  :param app: the client app of the request, from its headers
  :param retry: the count of try agains of this message, 0 for its first answer
  :param level: the reasoning level of the last answer of the chat, else None
  :param body: the body of the request, which may hold the `think_longer` field
  :param context: the other surfaces of the point
  """
  steps = _bump(body)
  if steps:
    # A press steps the level of the last answer. A value of the client is a floor, so a press
    # never lowers the level of a chat.
    before = max(_tier_of(level, prompt), TIER_OF.get(effort or "", 0))
    chosen = LEVELS[min(before + steps, TOP)]
    logger.info(
      "think longer for %s: %s +%s step(s) -> %s",
      app or "-",
      level or "the prompt",
      steps,
      chosen,
    )
    value["reasoning_effort"] = chosen
    return
  if CLIENT is not None and app != CLIENT:
    return
  tier = int(router.required_tier(prompt or ""))
  if retry:
    tier = min(_tier_of(level, prompt) + 1, TOP)
    logger.info("think longer for %s: a try again -> %s", app or "-", LEVELS[tier])
  else:
    logger.info("think longer for %s: the prompt reads %s", app or "-", LEVELS[tier])
  value["reasoning_effort"] = LEVELS[tier]


def on_http(
  body: dict[str, Any],
  key: str = "",
  prompt: str = "",
  headers: dict | None = None,
  level: str | None = None,
) -> dict[str, Any]:
  """The next level of the ladder of the chat: the same model, and 1 step up.

  :param body: the JSON body of the call, which names the model of the chat
  :param key: the session key of the chat, from the bearer token and its first user turn
  :param prompt: the first user turn of the chat
  :param headers: the request headers
  :param level: the reasoning level of the last answer of the chat, else None
  """
  before = _tier_of(level, _prompt(body, prompt))
  tier = min(before + 1, TOP)
  return {
    "model": body.get("model"),
    "reasoning_effort": LEVELS[tier],
    "tier": tier,
    "tier_name": router.TIER_NAMES[tier],
    "before": {
      "tier": before,
      "tier_name": router.TIER_NAMES[before],
      "reasoning_effort": LEVELS[before],
    },
    "top": before >= TOP,
  }
