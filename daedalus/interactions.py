"""Translate between the Gemini interactions shape and the OpenAI chat shape."""

import json
import logging
from typing import Any

from daedalus import gemini

logger = logging.getLogger("daedalus")

# Interaction tools with no OpenAI counterpart.
UNSUPPORTED_TOOLS = frozenset(
  {"google_search", "url_context", "mcp_server", "code_execution"}
)

# Generation config keys with no OpenAI field.
UNSUPPORTED_CONFIG = ("thinking_level", "thinkingLevel")

LOCAL_ID = "int_local"


def content_items(source: Any) -> list[dict[str, Any]]:
  """The request input as a list of content items."""
  if isinstance(source, str):
    return [{"type": "text", "text": source}]
  if isinstance(source, list):
    return [item for item in source if isinstance(item, dict)]
  return []


def text_of(items: list[dict[str, Any]]) -> str:
  """Join the text items of one input."""
  found = [item.get("text") for item in items if item.get("type") == "text"]
  return "\n".join(text for text in found if isinstance(text, str) and text)


def image_of(item: dict[str, Any]) -> dict[str, Any] | None:
  """One image item as an OpenAI image content part."""
  mime = str(item.get("mime_type") or item.get("mimeType") or "image/png")
  data = item.get("data")
  if isinstance(data, str) and data:
    return {
      "type": "image_url",
      "image_url": {"url": f"data:{mime};base64,{data}"},
    }
  uri = item.get("uri")
  if isinstance(uri, str) and uri:
    return {"type": "image_url", "image_url": {"url": uri}}
  return None


def to_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
  """The interaction input as OpenAI messages."""
  items = content_items(body.get("input"))
  messages: list[dict[str, Any]] = []
  replies = [item for item in items if item.get("type") == "function_result"]
  rest = [item for item in items if item.get("type") != "function_result"]
  blocks: list[dict[str, Any]] = []
  for item in rest:
    if item.get("type") == "image":
      image = image_of(item)
      if image is not None:
        blocks.append(image)
    else:
      blocks.append({"type": "text", "text": str(item.get("text") or "")})
  text = text_of(rest)
  if blocks and any(block.get("type") == "image_url" for block in blocks):
    messages.append({"role": "user", "content": blocks})
  elif text:
    messages.append({"role": "user", "content": text})
  for reply in replies:
    result = reply.get("result")
    payload = text_of(result if isinstance(result, list) else [])
    messages.append(
      {
        "role": "tool",
        "tool_call_id": str(reply.get("call_id") or "call_0"),
        "content": payload,
      }
    )
  return messages


def lower_schema(node: Any) -> Any:
  """Lowercase the schema types, because Google writes them in capitals."""
  if isinstance(node, dict):
    out = {}
    for key, value in node.items():
      if key == "type" and isinstance(value, str):
        out[key] = value.lower()
      else:
        out[key] = lower_schema(value)
    return out
  if isinstance(node, list):
    return [lower_schema(entry) for entry in node]
  return node


def response_format(body: dict[str, Any]) -> dict[str, Any] | None:
  """The interaction response format as an OpenAI response format."""
  wanted = body.get("response_format")
  entry = gemini.first(wanted) if isinstance(wanted, list) else {}
  if not entry:
    return None
  if entry.get("mime_type") != "application/json":
    return None
  schema = entry.get("schema")
  if not isinstance(schema, dict):
    return {"type": "json_object"}
  return {
    "type": "json_schema",
    "json_schema": {"name": "response", "schema": lower_schema(schema)},
  }


def tools(body: dict[str, Any]) -> list[dict[str, Any]]:
  """The interaction function tools as OpenAI tools."""
  found: list[dict[str, Any]] = []
  for tool in body.get("tools") or []:
    if not isinstance(tool, dict):
      continue
    kind = str(tool.get("type") or "")
    if kind in UNSUPPORTED_TOOLS:
      logger.info("interaction tool %s has no OpenAI field, so it is dropped", kind)
      continue
    if kind != "function":
      continue
    function: dict[str, Any] = {"name": tool.get("name", "")}
    if tool.get("description"):
      function["description"] = tool["description"]
    if tool.get("parameters"):
      function["parameters"] = tool["parameters"]
    found.append({"type": "function", "function": function})
  return found


def model_of(body: dict[str, Any]) -> str:
  """The model the interaction names, or an empty string."""
  name = body.get("model")
  if isinstance(name, str) and name:
    return name
  config = body.get("agent_config") or body.get("agentConfig")
  if isinstance(config, dict):
    nested = config.get("model")
    if isinstance(nested, str):
      return nested
  return ""


def to_openai(body: dict[str, Any]) -> dict[str, Any]:
  """An interactions request as an OpenAI chat request."""
  payload: dict[str, Any] = {"model": model_of(body), "messages": to_messages(body)}
  config = body.get("generation_config") or body.get("generationConfig") or {}
  config = config if isinstance(config, dict) else {}
  for key, target in gemini.CONFIG.items():
    if config.get(key) is not None:
      payload[target] = config[key]
  for key in UNSUPPORTED_CONFIG:
    if config.get(key) is not None:
      logger.info("interaction %s has no OpenAI field, so it is dropped", key)
  fmt = response_format(body)
  if fmt is not None:
    payload["response_format"] = fmt
  declared = tools(body)
  if declared:
    payload["tools"] = declared
  if body.get("previous_interaction_id") or body.get("previousInteractionId"):
    logger.warning("previous_interaction_id needs stored state, so it is ignored")
  return payload


def usage(answer: dict[str, Any]) -> dict[str, Any]:
  """OpenAI usage counts as interaction usage."""
  counts = answer.get("usage") or {}
  return {
    "prompt_tokens": counts.get("prompt_tokens", 0),
    "completion_tokens": counts.get("completion_tokens", 0),
    "total_tokens": counts.get("total_tokens", 0),
  }


def user_step(body: dict[str, Any]) -> dict[str, Any]:
  """The request input as a user input step."""
  content = [
    {"type": "text", "text": str(item.get("text") or "")}
    for item in content_items(body.get("input"))
    if item.get("type") != "image"
  ]
  return {"type": "user_input", "status": "done", "content": content}


def model_steps(message: dict[str, Any]) -> list[dict[str, Any]]:
  """One OpenAI message as interaction steps."""
  steps: list[dict[str, Any]] = []
  text = message.get("content")
  if isinstance(text, str) and text:
    steps.append(
      {
        "type": "model_output",
        "status": "done",
        "content": [{"type": "text", "text": text}],
      }
    )
  for call in message.get("tool_calls") or []:
    if not isinstance(call, dict):
      continue
    function = call.get("function") or {}
    steps.append(
      {
        "type": "function_call",
        "id": call.get("id") or "call_0",
        "name": function.get("name", ""),
        "arguments": gemini.loads(function.get("arguments")),
      }
    )
  return steps


def status_of(choice: dict[str, Any], steps: list[dict[str, Any]]) -> str:
  """The interaction status for one OpenAI choice."""
  if any(step.get("type") == "function_call" for step in steps):
    return "requires_action"
  if choice.get("finish_reason") == "length":
    return "incomplete"
  return "completed"


def to_interaction(
  answer: dict[str, Any], body: dict[str, Any], model: str
) -> dict[str, Any]:
  """An OpenAI chat answer as an interaction."""
  choice = gemini.first(answer.get("choices"))
  steps = [user_step(body)]
  steps.extend(model_steps(choice.get("message") or {}))
  return {
    "id": answer.get("id") or LOCAL_ID,
    "object": "interaction",
    "model": answer.get("model") or model,
    "status": status_of(choice, steps),
    "steps": steps,
    "usage": usage(answer),
  }


class Stream:
  """Emit the interactions SSE events for one OpenAI stream."""

  def __init__(self, model: str, interaction_id: str = LOCAL_ID) -> None:
    self.model = model
    self.id = interaction_id
    self.index = 0
    self.opened = False

  def open(self) -> list[tuple[str, dict[str, Any]]]:
    """The events that start the stream, once."""
    if self.opened:
      return []
    self.opened = True
    return [
      (
        "interaction.created",
        {
          "interaction": {
            "id": self.id,
            "status": "in_progress",
            "object": "interaction",
            "model": self.model,
          },
          "event_type": "interaction.created",
        },
      ),
      (
        "interaction.in_progress",
        {"interaction_id": self.id, "event_type": "interaction.in_progress"},
      ),
      (
        "step.start",
        {
          "index": self.index,
          "step": {"type": "model_output", "content": []},
          "event_type": "step.start",
        },
      ),
    ]

  def delta(self, text: str) -> list[tuple[str, dict[str, Any]]]:
    """One text delta as a step delta."""
    events = self.open()
    if text:
      events.append(
        (
          "step.delta",
          {
            "index": self.index,
            "delta": {"type": "text", "text": text},
            "event_type": "step.delta",
          },
        )
      )
    return events

  def close(self, answer: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The events that end the stream."""
    events = self.open()
    events.append(
      (
        "step.stop",
        {"index": self.index, "status": "done", "event_type": "step.stop"},
      )
    )
    interaction: dict[str, Any] = {"id": self.id, "status": "completed"}
    if answer.get("usage"):
      interaction["usage"] = usage(answer)
    events.append(
      (
        "interaction.completed",
        {"interaction": interaction, "event_type": "interaction.completed"},
      )
    )
    return events


def frame(events: list[tuple[str, dict[str, Any]]]) -> bytes:
  """Interaction events as SSE bytes."""
  out = b""
  for name, data in events:
    out += f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
  return out
