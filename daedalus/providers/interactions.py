import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from daedalus.providers.base import (
  Chunks,
  OpenAIProvider,
  ProviderError,
  data_url,
  events,
  function_call,
  system_text,
  tool_call,
)

GENERATION_FIELDS = (
  ("temperature", "temperature"),
  ("top_p", "top_p"),
  ("max_tokens", "max_output_tokens"),
  ("max_completion_tokens", "max_output_tokens"),
  ("stop", "stop_sequences"),
  ("seed", "seed"),
  ("reasoning_effort", "thinking_level"),
)


def usage(answer: dict) -> dict:
  counts = answer.get("usage") or {}
  prompt = counts.get("total_input_tokens", 0)
  output = counts.get("total_output_tokens", 0) + counts.get("total_thought_tokens", 0)
  return {
    "prompt_tokens": prompt,
    "completion_tokens": output,
    "total_tokens": counts.get("total_tokens", prompt + output),
  }


def parts(content: Any) -> list[dict]:
  if content is None:
    return []
  if isinstance(content, str):
    return [{"type": "text", "text": content}]
  if not isinstance(content, list):
    raise ProviderError("Message content must be text or a list")
  found = []
  for item in content:
    if not isinstance(item, dict):
      raise ProviderError("Invalid content part")
    if item.get("type") == "text":
      found.extend(parts(item.get("text")))
    elif item.get("type") == "image_url":
      url = (item.get("image_url") or {}).get("url", "")
      if url.startswith("https://"):
        found.append({"type": "image", "uri": url})
      else:
        mime, data = data_url(url)
        found.append({"type": "image", "mime_type": mime, "data": data})
    else:
      raise ProviderError(f"Unsupported content part: {item.get('type')}")
  return found


def options(payload: dict) -> dict:
  body, generation = {}, {}
  for source, target in GENERATION_FIELDS:
    if payload.get(source) is not None:
      value = payload[source]
      generation[target] = (
        [value] if source == "stop" and isinstance(value, str) else value
      )
  fmt = payload.get("response_format") or {}
  if fmt.get("type") in {"json_object", "json_schema"}:
    body["response_format"] = {"type": "text", "mime_type": "application/json"}
    schema = (fmt.get("json_schema") or {}).get("schema")
    if schema:
      body["response_format"]["schema"] = schema
  if generation:
    body["generation_config"] = generation
  functions = []
  for tool in payload.get("tools") or []:
    if tool.get("type") != "function":
      raise ProviderError("Only function tools have an Interactions mapping")
    function = tool["function"]
    functions.append(
      {
        key: function[key]
        for key in ("name", "description", "parameters")
        if key in function
      }
    )
  if functions:
    body["tools"] = [{"type": "function", **function} for function in functions]
  if payload.get("tool_choice") not in (None, "auto"):
    raise ProviderError("Interactions tool_choice mapping is not supported")
  return body


def message_of(answer: dict) -> tuple[dict, str]:
  if answer.get("status") in {"failed", "cancelled"} or answer.get("error"):
    raise ProviderError("Interactions request failed")
  text, calls = [], []
  reason = "length" if answer.get("status") == "incomplete" else "stop"
  for step in answer.get("steps") or []:
    if step.get("type") == "model_output":
      text.extend(
        part["text"] for part in step.get("content") or [] if part.get("type") == "text"
      )
    elif step.get("type") == "function_call":
      calls.append(tool_call(step["name"], step.get("arguments", {}), step.get("id")))
  message = {"role": "assistant", "content": "".join(text) or None}
  if calls:
    message["tool_calls"] = calls
    reason = "tool_calls"
  return message, reason


class InteractionsProvider(OpenAIProvider):
  """The stateless Gemini Interactions API."""

  unsupported = (
    "logprobs",
    "top_logprobs",
    "logit_bias",
    "audio",
    "modalities",
    "frequency_penalty",
    "presence_penalty",
    "parallel_tool_calls",
    "functions",
    "function_call",
    "thinking",
    "web_search_options",
    "service_tier",
  )

  def headers(self) -> dict[str, str]:
    return {"content-type": "application/json", "x-goog-api-key": self.key}

  def url(self, slug: str, payload: dict) -> str:
    return self.base + "/interactions"

  def body(self, slug: str, payload: dict) -> dict:
    if payload.get("n", 1) != 1:
      raise ProviderError("Interactions returns one answer per request")
    steps, systems, names = [], [], {}
    for message in payload["messages"]:
      if not isinstance(message, dict):
        raise ProviderError("Each message must be an object")
      role = message.get("role")
      found = parts(message.get("content"))
      if role in {"system", "developer"}:
        systems.extend(system_text(found))
        continue
      if role == "tool":
        call_id = message.get("tool_call_id")
        name = names.get(call_id)
        if not name:
          raise ProviderError("Tool result has no matching function call")
        result = {"content": found}
        steps.append(
          {
            "type": "function_result",
            "call_id": call_id,
            "name": name,
            "result": result,
          }
        )
        continue
      if role not in {"user", "assistant"}:
        raise ProviderError(f"Unsupported message role: {role}")
      if found:
        kind = "model_output" if role == "assistant" else "user_input"
        steps.append({"type": kind, "content": found})
      for call in message.get("tool_calls") or []:
        identifier, name, arguments = function_call(call)
        names[identifier] = name
        steps.append(
          {
            "type": "function_call",
            "id": identifier,
            "name": name,
            "arguments": arguments,
          }
        )
    body: dict = {
      "model": slug,
      "input": steps,
      "store": False,
      "stream": bool(payload.get("stream")),
    }
    if systems:
      body["system_instruction"] = "\n".join(systems)
    body.update(options(payload))
    return body

  def completion(self, answer: dict, model: str) -> dict:
    message, reason = message_of(answer)
    return {
      "id": answer.get("id") or "chatcmpl-" + uuid.uuid4().hex,
      "object": "chat.completion",
      "created": int(time.time()),
      "model": model,
      "choices": [{"index": 0, "message": message, "finish_reason": reason}],
      "usage": usage(answer),
    }

  async def stream(
    self, response: httpx.Response, model: str, include_usage: bool
  ) -> AsyncIterator[bytes]:
    chunks, calls, finished, counts = Chunks(model), {}, False, None
    try:
      async for event in events(response):
        if event.get("error"):
          raise ProviderError("Upstream stream error")
        event_type, index = event.get("event_type"), event.get("index", 0)
        delta, reason = {}, None
        if event_type == "step.start":
          step = event.get("step") or {}
          if step.get("type") == "function_call":
            calls[index] = len(calls)
            call = tool_call(step["name"], {}, step.get("id"))
            call["function"]["arguments"] = ""
            delta["tool_calls"] = [{"index": calls[index], **call}]
        elif event_type == "step.delta":
          part = event.get("delta") or {}
          if part.get("type") == "text":
            delta["content"] = part.get("text", "")
          elif part.get("type") == "arguments_delta":
            if index not in calls:
              raise ProviderError("Function delta has no start event")
            arguments = {"arguments": part.get("arguments", "")}
            delta["tool_calls"] = [{"index": calls[index], "function": arguments}]
        elif event_type in {"interaction.completed", "interaction.requires_action"}:
          answer = event.get("interaction") or {}
          if answer.get("status") in {"failed", "cancelled"}:
            raise ProviderError("Interactions request failed")
          incomplete = answer.get("status") == "incomplete"
          reason = "tool_calls" if calls else "length" if incomplete else "stop"
          counts, finished = usage(answer), True
        elif event_type in {"interaction.failed", "interaction.cancelled"}:
          raise ProviderError("Interactions stream failed")
        if delta or reason:
          yield chunks.chunk(delta, reason)
      if not finished:
        raise ProviderError("Upstream stream ended before completion")
      yield chunks.end(counts, include_usage)
    finally:
      await response.aclose()
