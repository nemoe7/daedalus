# ---
# version: 1.0.1
# surfaces: [on-request, on-prompt]
# author: nemoe7
# title: Auto reasoning effort
# description: The Open WebUI reasoning level and try-again rule.
# license: daedalus Noncommercial License 1.0.0
# ---

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

  A new message takes the level of the read of its newest user turn and model turn, and a value of the
  client keeps the last word. A step of a continuing turn, whose newest message is no user turn, keeps
  the level of the last answer, so a multistep agentic turn never drops to `none`. A try again steps
  the level of the last answer 1 up, above the value of the client, because the regenerate button
  carries the level. A last answer with no recorded level took no reasoning step, so that request
  takes the read and no step, while the tier still steps 1 up. The cap of the ladder is `high`. A
  model that requires reasoning floors the read to `low`, so it never takes `none`.

  :param value: the request values, holding `reasoning_effort`
  :param prompt: the user turns joined, the fallback when the messages do not come
  :param messages: the messages of the request, for the newest user turn and model turn
  :param effort: the value of the client, `None` when it sent none
  :param app: the client app of the request, from its headers
  :param retry: the count of try agains of this message, 0 for its first answer
  :param level: the reasoning level of the last answer of the chat, else None
  :param context: the other values of the surface
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
