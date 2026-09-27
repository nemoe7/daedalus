import json
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import quote, urlsplit

import httpx


class ProviderError(ValueError):
  pass


GEMINI_FIELDS = {
  "temperature": "temperature",
  "top_p": "topP",
  "top_k": "topK",
  "n": "candidateCount",
  "seed": "seed",
  "logprobs": "responseLogprobs",
  "top_logprobs": "logprobs",
}
GEMINI_MODALITIES = {
  "text": "TEXT",
  "image": "IMAGE",
  "audio": "AUDIO",
  "video": "VIDEO",
}
GEMINI_HOSTED_TOOLS = {
  "googleSearch": "googleSearch",
  "google_search": "googleSearch",
  "codeExecution": "codeExecution",
  "code_execution": "codeExecution",
  "urlContext": "urlContext",
  "url_context": "urlContext",
}
GEMINI_SEARCH_TOOLS = {"googleSearch", "urlContext"}
GEMINI_UNSUPPORTED = ("logit_bias", "audio", "function_call")
INTERACTION_UNSUPPORTED = (
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


def first(items: Any) -> dict:
  return (
    items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
  )


def prepare(model: str, payload: dict, config: Mapping) -> tuple[str, dict, dict, str]:
  name, separator, slug = model.partition("/")
  provider = config.get(name)
  if not separator or not slug or not isinstance(provider, dict):
    raise ProviderError(f"Unknown provider model: {model}")
  base = str(provider.get("api_base") or "").rstrip("/")
  parsed = urlsplit(base)
  if (
    parsed.scheme not in {"https", "http"}
    or not parsed.netloc
    or parsed.query
    or parsed.fragment
  ):
    raise ProviderError(f"Invalid api_base for {name}")
  if parsed.username or parsed.password:
    raise ProviderError(f"Credentials in api_base for {name}")
  key = provider.get("api_key")
  if not isinstance(key, str) or not key:
    raise ProviderError(f"Missing api_key for {name}")
  kind = provider.get("api_type", "openai")
  headers = {"content-type": "application/json"}
  if kind == "openai":
    headers["authorization"] = f"Bearer {key}"
    return base + "/chat/completions", {**payload, "model": slug}, headers, kind
  if kind not in {"gemini", "interactions"}:
    raise ProviderError(f"Unknown api_type for {name}: {kind}")
  headers["x-goog-api-key"] = key
  body = native_request(payload, kind, slug)
  if kind == "interactions":
    body.update(model=slug, store=False, stream=bool(payload.get("stream")))
    return base + "/interactions", body, headers, kind
  action = (
    "streamGenerateContent?alt=sse" if payload.get("stream") else "generateContent"
  )
  return f"{base}/models/{quote(slug, safe='')}:{action}", body, headers, kind


def content_parts(content: Any, kind: str) -> list[dict]:
  if content is None:
    return []
  if isinstance(content, str):
    return (
      [{"text": content}] if kind == "gemini" else [{"type": "text", "text": content}]
    )
  if not isinstance(content, list):
    raise ProviderError("Message content must be text or a list")
  parts = []
  for item in content:
    if not isinstance(item, dict):
      raise ProviderError("Invalid content part")
    if item.get("type") == "text":
      parts.extend(content_parts(item.get("text"), kind))
    elif item.get("type") == "image_url":
      url = (item.get("image_url") or {}).get("url", "")
      if url.startswith("data:"):
        prefix, separator, data = url.partition(",")
        if not separator or not prefix.endswith(";base64"):
          raise ProviderError("Images require base64 data URLs")
        mime = prefix[5:-7]
        parts.append(
          {"inlineData": {"mimeType": mime, "data": data}}
          if kind == "gemini"
          else {"type": "image", "mime_type": mime, "data": data}
        )
      elif kind == "interactions" and url.startswith("https://"):
        parts.append({"type": "image", "uri": url})
      else:
        raise ProviderError("This API requires an inline image")
    else:
      raise ProviderError(f"Unsupported content part: {item.get('type')}")
  return parts


def native_request(payload: dict, kind: str, model: str = "") -> dict:
  messages = payload.get("messages")
  if not isinstance(messages, list):
    raise ProviderError("messages must be a list")
  unsupported = GEMINI_UNSUPPORTED if kind == "gemini" else INTERACTION_UNSUPPORTED
  for field in unsupported:
    if payload.get(field) is not None:
      raise ProviderError(f"Unsupported native field: {field}")
  content, systems, names = [], [], {}
  for message in messages:
    if not isinstance(message, dict):
      raise ProviderError("Each message must be an object")
    role = message.get("role")
    parts = content_parts(message.get("content"), kind)
    if role in {"system", "developer"}:
      if any("text" not in part for part in parts):
        raise ProviderError("System instructions must contain text")
      systems.extend(part["text"] for part in parts)
      continue
    if role == "tool":
      call_id = message.get("tool_call_id")
      name = names.get(call_id)
      if not name:
        raise ProviderError("Tool result has no matching function call")
      if kind == "gemini":
        result = {"result": message.get("content")}
        parts = [{"functionResponse": {"name": name, "response": result}}]
      else:
        content.append(
          {
            "type": "function_result",
            "call_id": call_id,
            "name": name,
            "result": {"content": parts},
          }
        )
        continue
    elif role not in {"user", "assistant"}:
      raise ProviderError(f"Unsupported message role: {role}")
    if kind == "interactions" and parts:
      content.append(
        {
          "type": "model_output" if role == "assistant" else "user_input",
          "content": parts,
        }
      )
    for call in message.get("tool_calls") or []:
      function = call["function"]
      arguments = json.loads(function.get("arguments") or "{}")
      if not isinstance(arguments, dict):
        raise ProviderError("Function arguments must be a JSON object")
      names[call["id"]] = function["name"]
      if kind == "gemini":
        part = {"functionCall": {"name": function["name"], "args": arguments}}
        signature = (call.get("extra_content") or {}).get("thought_signature")
        if signature:
          part["thoughtSignature"] = signature
        parts.append(part)
      else:
        content.append(
          {
            "type": "function_call",
            "id": call["id"],
            "name": function["name"],
            "arguments": arguments,
          }
        )
    if kind == "gemini" and parts:
      content.append(
        {"role": "model" if role == "assistant" else "user", "parts": parts}
      )
  body = {"contents" if kind == "gemini" else "input": content}
  if systems:
    body["systemInstruction" if kind == "gemini" else "system_instruction"] = (
      {"parts": [{"text": "\n".join(systems)}]}
      if kind == "gemini"
      else "\n".join(systems)
    )
  body.update(
    gemini_options(payload, model) if kind == "gemini" else interaction_options(payload)
  )
  return body


def interaction_options(payload: dict) -> dict:
  body, generation = {}, {}
  for source, target in (
    ("temperature", "temperature"),
    ("top_p", "top_p"),
    ("max_tokens", "max_output_tokens"),
    ("max_completion_tokens", "max_output_tokens"),
    ("stop", "stop_sequences"),
    ("seed", "seed"),
    ("reasoning_effort", "thinking_level"),
  ):
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


def without(node: Any, key: str, only_false: bool = False) -> Any:
  if isinstance(node, list):
    return [without(item, key, only_false) for item in node]
  if not isinstance(node, dict):
    return node
  return {
    name: without(value, key, only_false)
    for name, value in node.items()
    if name != key or (only_false and value is not False)
  }


def thinking_level(effort: str, model: str) -> dict:
  name = model.lower()
  flash = "flash" in name and "gemini-3" in name
  medium = flash or "gemini-3.1-pro-preview" in name
  levels = {
    "minimal": ("minimal" if flash else "low", True),
    "low": ("low", True),
    "medium": ("medium" if medium else "high", True),
    "high": ("high", True),
    "disable": ("minimal" if flash else "low", False),
    "none": ("minimal" if flash else "low", False),
  }
  if effort not in levels:
    raise ProviderError(f"Unsupported reasoning_effort: {effort}")
  level, include = levels[effort]
  return {"thinkingLevel": level, "includeThoughts": include}


def thinking_budget(effort: str, model: str) -> dict:
  name = model.lower()
  minimal = 128
  if "gemini-2.5-flash-lite" in name:
    minimal = 512
  elif "gemini-2.5-flash" in name:
    minimal = 1
  budgets = {
    "minimal": (minimal, True),
    "low": (1024, True),
    "medium": (2048, True),
    "high": (4096, True),
    "disable": (0, False),
    "none": (0, False),
  }
  if effort not in budgets:
    raise ProviderError(f"Unsupported reasoning_effort: {effort}")
  budget, include = budgets[effort]
  return {"thinkingBudget": budget, "includeThoughts": include}


def thinking_param(value: dict, model: str) -> dict:
  enabled = value.get("type") == "enabled"
  budget = value.get("budget_tokens")
  if "gemini-3" in model:
    return {"includeThoughts": enabled and budget != 0}
  config = {"includeThoughts": True} if enabled and budget not in (0, None) else {}
  if isinstance(budget, int):
    config["thinkingBudget"] = budget
  return config


def gemini_tools(value: list) -> list[dict]:
  declarations, hosted = [], []
  for tool in without(without(value, "additionalProperties", True), "strict"):
    if not isinstance(tool, dict):
      raise ProviderError("Invalid tool")
    if isinstance(tool.get("function"), dict) or (
      "name" in tool and "type" not in tool
    ):
      function = tool.get("function", tool)
      declarations.append(
        {
          key: function[key]
          for key in ("name", "description", "parameters")
          if key in function
        }
      )
    elif tool.get("type") in {"web_search", "web_search_preview"}:
      hosted.append({"googleSearch": {}})
    else:
      native = {key: item for key, item in tool.items() if key != "type"}
      name = next(iter(native), "") if len(native) == 1 else ""
      canonical = GEMINI_HOSTED_TOOLS.get(name)
      if canonical is None:
        raise ProviderError("Unsupported Gemini tool")
      hosted.append({canonical: native[name] or {}})
  return ([{"functionDeclarations": declarations}] if declarations else []) + hosted


def tool_config(choice: str | dict) -> dict:
  modes = {"none": "NONE", "required": "ANY", "auto": "AUTO"}
  if isinstance(choice, dict):
    name = (choice.get("function") or {}).get("name", "")
    return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}}
  if choice not in modes:
    raise ProviderError(f"Unsupported tool_choice: {choice}")
  return {"functionCallingConfig": {"mode": modes[choice]}}


def response_format(value: dict, generation: dict) -> None:
  value = without(value, "strict")
  if value.get("type") == "json_object":
    generation["responseMimeType"] = "application/json"
  elif value.get("type") == "text":
    generation["responseMimeType"] = "text/plain"
  schema = None
  if "response_schema" in value:
    schema = value["response_schema"]
  elif value.get("type") == "json_schema":
    schema = (value.get("json_schema") or {}).get("schema")
  if schema is not None:
    generation["responseMimeType"] = "application/json"
  if isinstance(schema, dict) and schema:
    generation["responseJsonSchema"] = schema


def gemini_options(payload: dict, model: str) -> dict:
  gemini3 = "gemini-3" in model
  body, generation, tools = {}, {}, []
  for param, value in payload.items():
    if value is None:
      continue
    if param in GEMINI_FIELDS:
      generation[GEMINI_FIELDS[param]] = value
    elif param == "stop" and isinstance(value, (str, list)):
      generation["stopSequences"] = [value] if isinstance(value, str) else value
    elif param in {"max_tokens", "max_completion_tokens"}:
      generation["maxOutputTokens"] = value
    elif param == "response_format" and isinstance(value, dict):
      response_format(value, generation)
    elif param in {"frequency_penalty", "presence_penalty"}:
      if not gemini3 and model != "gemini-2.5-pro-preview-06-05":
        generation[
          "frequencyPenalty" if param == "frequency_penalty" else "presencePenalty"
        ] = value
    elif param in {"tools", "functions"} and isinstance(value, list) and value:
      tools.extend(gemini_tools(value))
    elif param == "tool_choice" and isinstance(value, (str, dict)):
      body["toolConfig"] = tool_config(value)
    elif param == "reasoning_effort":
      effort = value.get("effort") if isinstance(value, dict) else value
      if isinstance(effort, str):
        generation["thinkingConfig"] = (thinking_level if gemini3 else thinking_budget)(
          effort, model
        )
    elif param == "thinking" and isinstance(value, dict):
      generation["thinkingConfig"] = thinking_param(value, model)
    elif param == "modalities" and isinstance(value, list):
      generation["responseModalities"] = [
        GEMINI_MODALITIES.get(item, "MODALITY_UNSPECIFIED") for item in value
      ]
    elif param == "web_search_options" and isinstance(value, dict):
      tools.append({"googleSearch": {}})
    elif param == "service_tier" and isinstance(value, str):
      tier = "priority" if value.lower() == "auto" else value.lower()
      body["serviceTier"] = "standard" if tier == "default" else tier
  if gemini3 and "temperature" not in generation:
    generation["temperature"] = 1.0
  if any("functionDeclarations" in tool for tool in tools):
    tools = [tool for tool in tools if not set(tool) & GEMINI_SEARCH_TOOLS]
  if tools:
    body["tools"] = tools
  if generation:
    body["generationConfig"] = generation
  return body


def usage(answer: dict, kind: str) -> dict:
  counts = answer.get("usageMetadata" if kind == "gemini" else "usage") or {}
  prompt = counts.get(
    "promptTokenCount" if kind == "gemini" else "total_input_tokens", 0
  )
  output = counts.get(
    "candidatesTokenCount" if kind == "gemini" else "total_output_tokens", 0
  )
  thoughts = counts.get(
    "thoughtsTokenCount" if kind == "gemini" else "total_thought_tokens", 0
  )
  return {
    "prompt_tokens": prompt,
    "completion_tokens": output + thoughts,
    "total_tokens": counts.get(
      "totalTokenCount" if kind == "gemini" else "total_tokens",
      prompt + output + thoughts,
    ),
  }


def tool_call(name: str, arguments: Any, identifier: str | None = None) -> dict:
  return {
    "id": identifier or "call_" + uuid.uuid4().hex,
    "type": "function",
    "function": {"name": name, "arguments": json.dumps(arguments)},
  }


def candidate_message(candidate: dict) -> tuple[dict, str]:
  text, thoughts, calls = [], [], []
  finish = candidate.get("finishReason")
  reason = (
    "length"
    if finish == "MAX_TOKENS"
    else "stop"
    if finish in (None, "STOP")
    else "content_filter"
  )
  for part in (candidate.get("content") or {}).get("parts") or []:
    if "text" in part:
      (thoughts if part.get("thought") else text).append(part["text"])
    if "functionCall" in part:
      function = part["functionCall"]
      call = tool_call(function["name"], function.get("args", {}), function.get("id"))
      if part.get("thoughtSignature"):
        call["extra_content"] = {"thought_signature": part["thoughtSignature"]}
      calls.append(call)
  message = {"role": "assistant", "content": "".join(text) or None}
  if thoughts:
    message["reasoning_content"] = "".join(thoughts)
  if calls:
    message["tool_calls"] = calls
    reason = "tool_calls"
  return message, reason


def interaction_message(answer: dict) -> tuple[dict, str]:
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


def completion(answer: dict, kind: str, model: str) -> dict:
  if kind == "gemini":
    candidates = [
      item for item in answer.get("candidates") or [] if isinstance(item, dict)
    ]
    if not candidates:
      raise ProviderError("Gemini returned no candidate")
    results = [candidate_message(candidate) for candidate in candidates]
  else:
    results = [interaction_message(answer)]
  choices = [
    {"index": index, "message": message, "finish_reason": reason}
    for index, (message, reason) in enumerate(results)
  ]
  return {
    "id": answer.get("responseId")
    or answer.get("id")
    or "chatcmpl-" + uuid.uuid4().hex,
    "object": "chat.completion",
    "created": int(time.time()),
    "model": model,
    "choices": choices,
    "usage": usage(answer, kind),
  }


def frame(data: dict) -> bytes:
  return b"data: " + json.dumps(data).encode() + b"\n\n"


async def events(response: httpx.Response) -> AsyncIterator[dict]:
  lines = []
  async for line in response.aiter_lines():
    if line.startswith("data:"):
      lines.append(line[5:].lstrip())
    elif not line and lines:
      raw = "\n".join(lines)
      lines = []
      if raw == "[DONE]":
        return
      event = json.loads(raw)
      if not isinstance(event, dict):
        raise ProviderError("Invalid upstream stream event")
      yield event
  if lines:
    raise ProviderError("Incomplete upstream stream event")


async def stream(
  response: httpx.Response, kind: str, model: str, include_usage: bool
) -> AsyncIterator[bytes]:
  identifier = "chatcmpl-" + uuid.uuid4().hex
  created = int(time.time())
  calls, finished = {}, False
  counts = None

  ended = set()

  def chunk(delta, reason=None, choice=0):
    return {
      "id": identifier,
      "object": "chat.completion.chunk",
      "created": created,
      "model": model,
      "choices": [{"index": choice, "delta": delta, "finish_reason": reason}],
    }

  try:
    async for event in events(response):
      if event.get("error"):
        raise ProviderError("Upstream stream error")
      delta, reason = {}, None
      if kind == "gemini":
        if event.get("usageMetadata"):
          counts = usage(event, kind)
        for position, candidate in enumerate(event.get("candidates") or []):
          if not isinstance(candidate, dict):
            raise ProviderError("Invalid Gemini candidate")
          choice = candidate.get("index", position)
          message, mapped = candidate_message(candidate)
          part = {
            key: message[key]
            for key in ("content", "reasoning_content")
            if message.get(key)
          }
          for call in message.get("tool_calls") or []:
            number = calls.setdefault(choice, 0)
            calls[choice] = number + 1
            part.setdefault("tool_calls", []).append({"index": number, **call})
          done = None
          if candidate.get("finishReason"):
            done = "tool_calls" if calls.get(choice) else mapped
            ended.add(choice)
          if part or done:
            yield frame(chunk(part, done, choice))
        finished = bool(ended)
        continue
      else:
        event_type = event.get("event_type")
        index = event.get("index", 0)
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
            delta["tool_calls"] = [
              {
                "index": calls[index],
                "function": {"arguments": part.get("arguments", "")},
              }
            ]
        elif event_type in {"interaction.completed", "interaction.requires_action"}:
          answer = event.get("interaction") or {}
          if answer.get("status") in {"failed", "cancelled"}:
            raise ProviderError("Interactions request failed")
          reason = (
            "tool_calls"
            if calls
            else "length"
            if answer.get("status") == "incomplete"
            else "stop"
          )
          counts = usage(answer, kind)
          finished = True
        elif event_type in {"interaction.failed", "interaction.cancelled"}:
          raise ProviderError("Interactions stream failed")
      if delta or reason:
        yield frame(chunk(delta, reason))
    if not finished:
      raise ProviderError("Upstream stream ended before completion")
    if include_usage and counts is not None:
      final = chunk({})
      final.update(choices=[], usage=counts)
      yield frame(final)
    yield b"data: [DONE]\n\n"
  finally:
    await response.aclose()
