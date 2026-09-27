import time
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any, ClassVar
from urllib.parse import quote

import httpx

from daedalus import signatures
from daedalus.providers.base import (
  Chunks,
  OpenAIProvider,
  ProviderError,
  data_url,
  error_text,
  events,
  function_call,
  limits,
  system_text,
  tool_call,
)

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

# Google's placeholder for a function call that has no signature from Gemini 3.
DUMMY_SIGNATURE = "skip_thought_signature_validator"


def without_strict(node: Any, names: bool = False) -> Any:
  """Drop `strict` keywords, but keep schema properties that use that name."""
  if isinstance(node, list):
    return [without_strict(item) for item in node]
  if not isinstance(node, dict):
    return node
  return {
    key: without_strict(value, key in {"properties", "$defs", "definitions"})
    for key, value in node.items()
    if names or key != "strict"
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
  for tool in without_strict(value):
    if not isinstance(tool, dict):
      raise ProviderError("Invalid tool")
    if isinstance(tool.get("function"), dict) or (
      "name" in tool and "type" not in tool
    ):
      function = tool.get("function", tool)
      declaration = {
        key: function[key] for key in ("name", "description") if key in function
      }
      if function.get("parameters") is not None:
        declaration["parametersJsonSchema"] = function["parameters"]
      declarations.append(declaration)
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
  value = without_strict(value)
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


def usage(answer: dict) -> dict:
  counts = answer.get("usageMetadata") or {}
  prompt = counts.get("promptTokenCount", 0)
  output = counts.get("candidatesTokenCount", 0) + counts.get("thoughtsTokenCount", 0)
  return {
    "prompt_tokens": prompt,
    "completion_tokens": output,
    "total_tokens": counts.get("totalTokenCount", prompt + output),
  }


def thought_signature(call: dict) -> str | None:
  """The signature from `extra_content`, in the Daedalus or the Google form."""
  extra = call.get("extra_content") or {}
  return extra.get("thought_signature") or (extra.get("google") or {}).get(
    "thought_signature"
  )


def keep_signatures(message: dict, model: str) -> None:
  """Store the signature of each tool call, for clients that drop `extra_content`."""
  for call in message.get("tool_calls") or []:
    signature = thought_signature(call)
    if signature:
      signatures.save(call["id"], model, signature)


def parts(content: Any) -> list[dict]:
  if content is None:
    return []
  if isinstance(content, str):
    return [{"text": content}]
  if not isinstance(content, list):
    raise ProviderError("Message content must be text or a list")
  found = []
  for item in content:
    if not isinstance(item, dict):
      raise ProviderError("Invalid content part")
    if item.get("type") == "text":
      found.extend(parts(item.get("text")))
    elif item.get("type") == "image_url":
      mime, data = data_url((item.get("image_url") or {}).get("url", ""))
      found.append({"inlineData": {"mimeType": mime, "data": data}})
    else:
      raise ProviderError(f"Unsupported content part: {item.get('type')}")
  return found


class GeminiProvider(OpenAIProvider):
  """Gemini generateContent, with LiteLLM's OpenAI parameter mapping."""

  unsupported = ("logit_bias", "audio", "function_call")
  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "gemini",
    "api_base": "https://generativelanguage.googleapis.com/v1beta",
    "discovery_url": "https://generativelanguage.googleapis.com/v1beta/models",
  }

  @staticmethod
  def columns(row: dict) -> dict[str, Any]:
    """Store columns from one Gemini row. It shows `thinking` only when true."""
    methods = row.get("supportedGenerationMethods") or []
    mode = "embedding" if "embedContent" in methods else None
    return {
      "mode": "chat" if "generateContent" in methods else mode,
      **limits(row.get("inputTokenLimit"), row.get("outputTokenLimit")),
      "supports_reasoning": row.get("thinking") is True or None,
    }

  @staticmethod
  def auth(key: str) -> dict[str, str]:
    """The header that carries the API key."""
    return {"x-goog-api-key": key}

  def url(self, slug: str, payload: dict) -> str:
    action = (
      "streamGenerateContent?alt=sse" if payload.get("stream") else "generateContent"
    )
    return f"{self.base}/models/{quote(slug, safe='')}:{action}"

  def body(self, slug: str, payload: dict) -> dict:
    contents, systems, names = [], [], {}
    for message in payload["messages"]:
      if not isinstance(message, dict):
        raise ProviderError("Each message must be an object")
      role = message.get("role")
      found = parts(message.get("content"))
      if role in {"system", "developer"}:
        systems.extend(system_text(found))
        continue
      if role == "tool":
        name = names.get(message.get("tool_call_id"))
        if not name:
          raise ProviderError("Tool result has no matching function call")
        response = {"result": message.get("content")}
        found = [{"functionResponse": {"name": name, "response": response}}]
      elif role not in {"user", "assistant"}:
        raise ProviderError(f"Unsupported message role: {role}")
      for number, call in enumerate(message.get("tool_calls") or []):
        identifier, name, arguments = function_call(call)
        names[identifier] = name
        part = {"functionCall": {"name": name, "args": arguments}}
        signature = thought_signature(call) or signatures.find(
          identifier, f"{self.name}/{slug}"
        )
        if not signature and number == 0 and "gemini-3" in slug.lower():
          signature = DUMMY_SIGNATURE
        if signature:
          part["thoughtSignature"] = signature
        found.append(part)
      if found:
        contents.append(
          {"role": "model" if role == "assistant" else "user", "parts": found}
        )
    body: dict = {"contents": contents}
    if systems:
      body["systemInstruction"] = {"parts": [{"text": "\n".join(systems)}]}
    body.update(gemini_options(payload, slug))
    return body

  def completion(self, answer: dict, model: str) -> dict:
    candidates = [
      item for item in answer.get("candidates") or [] if isinstance(item, dict)
    ]
    if not candidates:
      raise ProviderError("Gemini returned no candidate")
    choices = []
    for index, candidate in enumerate(candidates):
      message, reason = candidate_message(candidate)
      keep_signatures(message, model)
      choices.append({"index": index, "message": message, "finish_reason": reason})
    return {
      "id": answer.get("responseId") or "chatcmpl-" + uuid.uuid4().hex,
      "object": "chat.completion",
      "created": int(time.time()),
      "model": model,
      "choices": choices,
      "usage": usage(answer),
    }

  async def stream(
    self, response: httpx.Response, model: str, include_usage: bool
  ) -> AsyncIterator[bytes]:
    chunks, calls, ended, counts = Chunks(model), {}, set(), None
    try:
      async for event in events(response):
        if event.get("error"):
          raise ProviderError(f"Upstream stream error: {error_text(event)}")
        if event.get("usageMetadata"):
          counts = usage(event)
        for position, candidate in enumerate(event.get("candidates") or []):
          if not isinstance(candidate, dict):
            raise ProviderError("Invalid Gemini candidate")
          choice = candidate.get("index", position)
          message, mapped = candidate_message(candidate)
          keep_signatures(message, model)
          delta = {
            key: message[key]
            for key in ("content", "reasoning_content")
            if message.get(key)
          }
          for call in message.get("tool_calls") or []:
            number = calls.get(choice, 0)
            calls[choice] = number + 1
            delta.setdefault("tool_calls", []).append({"index": number, **call})
          reason = None
          if candidate.get("finishReason"):
            reason = "tool_calls" if calls.get(choice) else mapped
            ended.add(choice)
          if delta or reason:
            yield chunks.chunk(delta, reason, choice)
      if not ended:
        raise ProviderError("Upstream stream ended before completion")
      yield chunks.end(counts, include_usage)
    finally:
      await response.aclose()
