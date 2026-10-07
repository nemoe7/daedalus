"""The Open WebUI reasoning level and try-again rule, as a request hook.

1 file holds the 2 surfaces of the rule:

  on_request  the point `request_hooks.on-request`: the key of the turn, and the code of a repeat
  on_prompt   the point `request_hooks.on-prompt`: the reasoning level of the request

Open WebUI sends `x-openwebui-chat-id` when `ENABLE_FORWARD_USER_INFO_HEADERS` is true. A repeat of
a message that a model answered is a try again: daedalus steps 1 tier up for `daedalus/auto`, keeps
a named pool, and drops the models that answered, then this file writes the code of the row, `rt1`,
`rt2`. The prompt point reads the heuristics v2 tier of the newest user turn and the newest model
turn, which is the classified level of the thread, so each turn of a chat gets its own level. A try again steps the
level of the last answer 1 up. A last answer with no level took no reasoning step, so that request
takes the read of the newest turn instead. The cap of the ladder is `high`. The tier of the
conversation stays with the routing of `daedalus/auto`, so this file sets the level alone.

`config/daedalus.yml` names this file in both groups. An empty value turns that point off.
"""

import logging
from typing import Any

from daedalus import store
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

# The characters of the model turn that join the read. A long answer would bloat the read.
ANSWER_CHARS = 500


def _text(content: Any) -> str:
  """The text of one message content, which is a string or a list of parts."""
  if isinstance(content, str):
    return content
  if isinstance(content, list):
    return " ".join(
      part.get("text", "")
      for part in content
      if isinstance(part, dict) and isinstance(part.get("text"), str)
    )
  return ""


def _newest(messages: Any, prompt: str) -> str:
  """The newest user turn and model turn in reading order, the model part capped, else the prompt.

  :param messages: the messages of the request
  :param prompt: the fallback when the messages carry no user turn
  """
  if not isinstance(messages, list):
    return prompt or ""
  found: dict[str, tuple[int, str]] = {}
  for index in range(len(messages) - 1, -1, -1):
    message = messages[index]
    if not isinstance(message, dict):
      continue
    role = message.get("role")
    if role in found or role not in ("user", "assistant"):
      continue
    text = _text(message.get("content"))
    if role == "assistant":
      text = text[-ANSWER_CHARS:]
    if text:
      found[role] = (index, text)
    if len(found) == 2:
      break
  if "user" not in found:
    return prompt or ""
  return "\n".join(text for _, text in sorted(found.values()))


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


def _level(context: Any, tier: int) -> str:
  """The effort of one tier on the ladder of the efforts list of the model.

  The list of the catalog of the model, when it holds one, narrows the coded set.
  """
  model = context.get("model") or ""
  return router.effort_at(
    router.efforts(
      context.get("config") or {},
      model,
      store.model_limits(model).get("supported_efforts"),
    ),
    tier - 1,
  )


def _floor(context: Any) -> int:
  """The lowest tier this request may take: 2 when the model requires reasoning, else 1.

  A model that rejects a request with reasoning off, such as kilo lfm-2.5, names
  `reasoning: required` in its config entry. That model never takes the `none` level.
  """
  config = context.get("config") or {}
  model = context.get("model") or ""
  return 2 if router.model_setting(config, model, "reasoning") == "required" else 1


def on_prompt(
  value: dict,
  prompt: str = "",
  messages: list | None = None,
  effort: str | None = None,
  app: str | None = None,
  retry: int = 0,
  level: str | None = None,
  **context: Any,
) -> None:
  """Set the reasoning level of an Open WebUI request: the read of the thread, or the step of a repeat.

  A new message takes the level of the read of its newest user turn and model turn, and a value of
  the client keeps the last word. A step of a continuing turn, whose newest message is no user
  turn, keeps the level of the last answer, so a multistep agentic turn never drops to `none`. A try again steps the level of the last answer 1 up, above the
  value of the client, because the regenerate button carries the level. A last answer with no
  recorded level took no reasoning step, so that request takes the read and no step, while the tier
  still steps 1 up. The cap of the ladder is `high`. A model that requires reasoning floors the
  read to `low`, so it never takes `none`.

  :param value: the request values, holding `reasoning_effort`
  :param prompt: the user turns joined, the fallback when the messages do not come
  :param messages: the messages of the request, for the newest user turn and model turn
  :param effort: the value of the client, `None` when it sent none
  :param app: the client app of the request, from its headers
  :param retry: the count of try agains of this message, 0 for its first answer
  :param level: the reasoning level of the last answer of the chat, else None
  :param context: the other surfaces of the point
  """
  if app != CLIENT:
    return
  read = max(int(router.required_tier(_newest(messages, prompt))), _floor(context))
  if not retry:
    if effort:
      return
    last = messages[-1] if isinstance(messages, list) and messages else None
    if isinstance(last, dict) and last.get("role") != "user" and level:
      # A step of an agentic turn carries no new user text: its read would drop the level of
      # the thread, so the step keeps the level of the last answer instead.
      kept = max(TIER_OF.get(level, read), _floor(context))
      kept_level = _level(context, kept)
      value["reasoning_effort"] = kept_level
      logger.info("a continuing turn keeps %s for %s", kept_level, app)
      return
    read_level = _level(context, read)
    value["reasoning_effort"] = read_level
    logger.info("the prompt reads %s for %s", read_level, app)
    return
  if level is None:
    read_level = _level(context, read)
    value["reasoning_effort"] = read_level
    logger.info("a try again after no reasoned answer: %s", read_level)
    return
  before = max(TIER_OF.get(level, read), TIER_OF.get(effort or "", 0))
  tier = min(before + 1, TOP)
  stepped = _level(context, tier)
  value["reasoning_effort"] = stepped
  logger.info("a try again for %s: %s +1 step -> %s", app, level, stepped)


def on_init() -> list[list[str]]:
  """The legend row of this hook."""
  return [["rtN", "A repeat picked another model, N times"]]
