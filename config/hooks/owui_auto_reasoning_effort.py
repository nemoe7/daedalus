"""The Open WebUI reasoning level and try-again rule, as a request hook.

1 file holds the 2 surfaces of the rule:

  on_request  the point `request_hooks.on-request`: the key of the turn, and the code of a repeat
  on_prompt   the point `request_hooks.on-prompt`: the reasoning level of the request

Open WebUI sends `x-openwebui-chat-id` when `ENABLE_FORWARD_USER_INFO_HEADERS` is true. A repeat of
a message that a model answered is a try again: daedalus steps 1 tier up for `daedalus/auto`, keeps
a named pool, and drops the models that answered, then this file writes the code of the row, `rt1`,
`rt2`. The prompt point reads the heuristics v2 tier of the message, which is the classified level
of the message, so each message of a chat gets its own level. A try again steps the level of the
last answer 1 up. A last answer with no level took no reasoning step, so that request takes the read
of the message instead. The cap of the ladder is `high`.

`config/daedalus.yml` names this file in both groups. An empty value turns that point off.
"""

import logging
from typing import Any

from daedalus.routing import router

logger = logging.getLogger("daedalus.hooks")

# The client this rule serves. The base names the app from its headers: `OWUI`, `Kilo`, another
# title, or None. Set it to None to serve every client.
CLIENT = "OWUI"

# The header that names the chat. Open WebUI sends it when `ENABLE_FORWARD_USER_INFO_HEADERS` is
# true, and no other client sends it.
HEADER = "x-openwebui-chat-id"

# The level of each tier, from `TIER-D` none to `TIER-A` high.
LEVELS = {1: "none", 2: "low", 3: "medium", 4: "high"}
# The tier of each level.
TIER_OF = {level: tier for tier, level in LEVELS.items()}
# The top of the ladder, `TIER-A`.
TOP = max(router.TIERS)


def on_request(value: dict, model: str, headers: dict) -> dict | None:
  """The key of the turn, and on a repeat the code of the row.

  :param value: the request values, holding `digest` and, on a repeat, `count`
  :param model: the requested model
  :param headers: the request headers
  """
  chat = headers.get(HEADER)
  if not chat:
    return None
  value["key"] = f"{chat}\x00{value['digest']}"
  count = value.get("count")
  if count:
    value["code"] = f"rt{count}"
    logger.info("try again %s of %s", count, model)
  return value


def on_prompt(
  value: dict,
  prompt: str = "",
  effort: str | None = None,
  app: str | None = None,
  retry: int = 0,
  level: str | None = None,
  **context: Any,
) -> None:
  """Set the reasoning level of an Open WebUI request: the read of the message, or the step of a repeat.

  A new message takes the level of the read of its prompt, and a value of the client keeps the last
  word. A try again steps the level of the last answer 1 up, above the value of the client, because
  the regenerate button carries the level. A last answer with no recorded level took no reasoning
  step, so that request takes the read of the message and no step, while the tier still steps 1 up.
  The cap of the ladder is `high`.

  :param value: the request values, holding `reasoning_effort`
  :param prompt: the user turns joined
  :param effort: the value of the client, `None` when it sent none
  :param app: the client app of the request, from its headers
  :param retry: the count of try agains of this message, 0 for its first answer
  :param level: the reasoning level of the last answer of the chat, else None
  :param context: the other surfaces of the point
  """
  if app != CLIENT:
    return
  read = int(router.required_tier(prompt or ""))
  if not retry:
    if effort:
      return
    value["reasoning_effort"] = LEVELS[read]
    logger.info("the prompt reads %s for %s", LEVELS[read], app)
    return
  if level is None:
    value["reasoning_effort"] = LEVELS[read]
    logger.info("a try again after no reasoned answer: %s", LEVELS[read])
    return
  before = max(TIER_OF.get(level, read), TIER_OF.get(effort or "", 0))
  tier = min(before + 1, TOP)
  value["reasoning_effort"] = LEVELS[tier]
  logger.info("a try again for %s: %s +1 step -> %s", app, level, LEVELS[tier])


def on_init() -> list[list[str]]:
  """The legend row of this hook."""
  return [["rtN", "A repeat picked another model, N times"]]
