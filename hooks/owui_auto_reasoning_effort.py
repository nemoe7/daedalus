# ---
# version: 1.0.9
# surfaces: [on-request, on-prompt, on-chunk]
# scope: client
# targets: [OWUI]
# author: nemoe7
# title: Auto reasoning effort
# description: The Open WebUI reasoning level and try-again rule.
# license: nemoe7 Noncommercial and Public-Interest License 1.2.0
# ---

import logging
from functools import cache
from typing import Any

from daedalus import store
from daedalus.routing import classifier, router

logger = logging.getLogger("daedalus.hooks")

# Header that specifies Open WebUI chat. Bundled service sets it through custom request headers.
HEADER = "x-openwebui-chat-id"

# The level of each tier, from `TIER-D` none to `TIER-A` high.
LEVELS = {1: "none", 2: "low", 3: "medium", 4: "high"}
# The tier of each level.
TIER_OF = {level: tier for tier, level in LEVELS.items()}
# The top of the ladder, `TIER-A`.
TOP = max(router.TIERS)

# Characters of model turn that join read. A long answer would bloat read.
ANSWER_CHARS = 500

CONTROL = "_daedalus_auto_effort"
_artifact = cache(classifier.load_artifact)

# The separator of the served model line: `kilo/poolside/laguna-s-2.1:free · high`.
DOT = " · "


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

  :param messages: request messages
  :param prompt: fallback when messages carry no user turn
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
  """Key of turn, and on a repeat code of row.

  :param value: the request values, holding `body`, `digest` and, on a repeat, `count`
  :param model: the requested model
  :param headers: the request headers
  """
  body = value.get("body")
  if isinstance(body, dict) and CONTROL in body:
    controls = body.pop(CONTROL)
    value[CONTROL] = controls if isinstance(controls, dict) else {}
  chat = headers.get(HEADER)
  if not chat:
    return value if CONTROL in value else None
  value["key"] = f"{chat}\x00{value['digest']}"
  count = value.get("count")
  if count:
    value["code"] = f"rt{count}"
    logger.info("try again %s of %s", count, model)
  return value


def _level(context: Any, tier: int, minimum: str = "none") -> str:
  model = context.get("model") or ""
  efforts = router.efforts(
    context.get("config") or {},
    model,
    store.model_limits(model).get("supported_efforts"),
  )
  step = tier - 1
  if minimum in ("low", "medium", "high"):
    rank = router.EFFORT_RANKS.index(minimum)
    floor = next(
      (
        i
        for i, effort in enumerate(efforts)
        if effort in router.EFFORT_RANKS and router.EFFORT_RANKS.index(effort) >= rank
      ),
      len(efforts) - 1,
    )
    step = max(step, floor)
  return router.effort_at(efforts, step)


def _floor(context: Any) -> int:
  config = context.get("config") or {}
  model = context.get("model") or ""
  return 2 if router.model_setting(config, model, "reasoning") == "required" else 1


def on_prompt(
  value: dict,
  prompt: str = "",
  messages: list | None = None,
  effort: str | None = None,
  retry: int = 0,
  level: str | None = None,
  **context: Any,
) -> None:
  """Set the reasoning level of an Open WebUI request: the read of the thread, or step of a repeat.

  A new message takes the level of the read of its newest user turn and model turn, and a value of the
  client keeps the last word. A step of a continuing turn, whose newest message is no user turn, keeps
  the level of the last answer, so a multistep agentic turn never drops to `none`. A try again steps
  the level of the last answer 1 up, above the value of the client, because the regenerate button
  carries the level.

  A last answer with no recorded level took no reasoning step, so that request takes the read
  and no step, while the tier still steps 1 up. The cap of the ladder is `high`. A model that
  requires reasoning floors the read to `low`, so it never takes `none`.

  :param value: the request values, holding `reasoning_effort`
  :param prompt: the user turns joined, the fallback when the messages do not come
  :param messages: the messages of the request, for the newest user turn and model turn
  :param effort: the value of the client, `None` when it sent none
  :param retry: the count of try agains of this message, 0 for its first answer
  :param level: the reasoning level of the last answer of the chat, else None
  :param context: the other values of the surface
  """
  model = context.get("model") or ""
  controls = (context.get("request_values") or {}).get(CONTROL) or {}
  sensitivity = controls.get("sensitivity")
  text = _newest(messages, prompt)
  if type(sensitivity) in (int, float) and 0 <= sensitivity <= 1:
    tier = classifier.predict(text, _artifact(), threshold=sensitivity).required_tier
  else:
    tier = router.required_tier(text)
  read = max(int(tier), _floor(context))
  minimum = controls.get("minimum_effort")
  minimum = minimum if isinstance(minimum, str) else "none"
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
      logger.info("a continuing turn keeps %s for %s", kept_level, model)
      return
    read_level = _level(context, read, minimum)
    value["reasoning_effort"] = read_level
    logger.info("the prompt reads %s for %s", read_level, model)
    return
  if level is None:
    read_level = _level(context, read, minimum)
    value["reasoning_effort"] = read_level
    logger.info("a try again after no reasoned answer: %s", read_level)
    return
  before = max(TIER_OF.get(level, read), TIER_OF.get(effort or "", 0))
  tier = min(before + 1, TOP)
  stepped = _level(context, tier)
  value["reasoning_effort"] = stepped
  logger.info("a try again for %s: %s +1 step -> %s", model, level, stepped)


def on_init() -> list[list[str]]:
  """The legend row of this hook."""
  return [["rtN", "A repeat selected another model, N times"]]


def on_chunk(
  chunk: dict[str, Any], model: str = "", context: dict[str, Any] | None = None
) -> dict[str, Any]:
  """Put routed reasoning at end of served model line of final chunk.

  The served model hook writes the line first, and this surface overrides it. A reasoning of
  `none` leaves the line alone, so the line names a reasoning only when the model reasoned.

  :param chunk: one OpenAI chunk on its way to the client
  :param model: the requested model
  :param context: `effort`, the reasoning effort of the attempt that answered
  """
  effort = str((context or {}).get("effort") or "")
  if not effort or effort == "none":
    return chunk
  usage = chunk.get("usage")
  found = usage.get("daedalus") if isinstance(usage, dict) else None
  if not isinstance(found, dict) or not found.get("line"):
    return chunk
  line = str(found["line"])
  if line.endswith(f"{DOT}{effort}"):
    return chunk
  selection = {**found, "line": f"{line}{DOT}{effort}"}
  chunk["usage"] = {**usage, "daedalus": selection}
  logger.info("served reasoning %s for %s", selection["line"], model)
  return chunk
