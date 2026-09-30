import asyncio
import json

import httpx

from daedalus import providers
from daedalus.providers import signatures

GEMINI = providers.GeminiProvider(
  "gemini", {"api_base": "https://gemini.test", "api_key": "k"}
)


def body(model: str, **extra: object) -> dict:
  payload = {"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}
  return GEMINI.body(model, payload)


def test_gemini_params() -> None:
  sent = body(
    "gemini-3.8-flash",
    temperature=0.2,
    top_p=0.9,
    top_k=40,
    n=2,
    stop="END",
    max_tokens=10,
    max_completion_tokens=20,
    seed=7,
    logprobs=True,
    top_logprobs=3,
    parallel_tool_calls=False,
    reasoning_effort="minimal",
    modalities=["text", "image", "other"],
    service_tier="auto",
  )
  assert sent["generationConfig"] == {
    "temperature": 0.2,
    "topP": 0.9,
    "topK": 40,
    "candidateCount": 2,
    "stopSequences": ["END"],
    "maxOutputTokens": 20,
    "seed": 7,
    "responseLogprobs": True,
    "logprobs": 3,
    "thinkingConfig": {"thinkingLevel": "minimal"},
    "responseModalities": ["TEXT", "IMAGE", "MODALITY_UNSPECIFIED"],
  }, sent
  assert sent["serviceTier"] == "priority"
  assert body("m", service_tier="default")["serviceTier"] == "standard"

  gemini3 = body("gemini-3.5-flash", frequency_penalty=1, reasoning_effort="medium")
  assert gemini3["generationConfig"] == {
    "thinkingConfig": {"thinkingLevel": "medium"},
    "temperature": 1.0,
  }, gemini3
  pro = body("gemini-3-pro", reasoning_effort={"effort": "none"})["generationConfig"]
  assert pro["thinkingConfig"] == {"thinkingLevel": "low"}
  for model in ("gemma-4-26b-a4b-it", "gemma-4-31b-it"):
    low = body(model, reasoning_effort="low")["generationConfig"]
    assert low["thinkingConfig"] == {"thinkingLevel": "minimal"}, low
    medium = body(model, reasoning_effort="medium")["generationConfig"]
    assert medium["thinkingConfig"] == {"thinkingLevel": "high"}, medium
    high = body(model, reasoning_effort="high")["generationConfig"]
    assert high["thinkingConfig"] == {"thinkingLevel": "high"}, high
    for effort in ("minimal", "none", "disable"):
      disabled = body(model, reasoning_effort=effort)["generationConfig"]
      assert disabled["thinkingConfig"] == {"thinkingLevel": "minimal"}, disabled
    enabled = body(model, thinking={"type": "enabled", "budget_tokens": 0})
    assert enabled["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}, (
      enabled
    )
    disabled = body(model, thinking={"type": "disabled", "budget_tokens": 1024})
    assert disabled["generationConfig"]["thinkingConfig"] == {
      "thinkingLevel": "minimal"
    }, disabled
  thinking = body("gemini-3.8-flash", thinking={"type": "enabled", "budget_tokens": 0})
  assert thinking["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}
  disabled = body(
    "gemini-3.8-flash", thinking={"type": "disabled", "budget_tokens": 99}
  )
  assert disabled["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}
  try:
    body("m", reasoning_effort="extreme")
  except providers.ProviderError:
    pass
  else:
    raise AssertionError("unsupported effort accepted")

  schema = {
    "type": "object",
    "strict": True,
    "additionalProperties": False,
    "properties": {"a": {"type": "string"}, "strict": {"type": "boolean"}},
  }
  fmt = body(
    "m",
    response_format={
      "type": "json_schema",
      "json_schema": {"schema": schema, "strict": True},
    },
  )["generationConfig"]
  assert fmt["responseMimeType"] == "application/json"
  assert fmt["responseJsonSchema"] == {
    "type": "object",
    "additionalProperties": False,
    "properties": {"a": {"type": "string"}, "strict": {"type": "boolean"}},
  }, fmt
  assert body("m", response_format={"type": "text"})["generationConfig"] == {
    "responseMimeType": "text/plain"
  }

  function = {"name": "weather", "parameters": schema, "strict": True}
  tools = body(
    "m",
    tools=[{"type": "function", "function": function}, {"type": "web_search"}],
    tool_choice={"type": "function", "function": {"name": "weather"}},
  )
  assert tools["tools"] == [
    {
      "functionDeclarations": [
        {
          "name": "weather",
          "parametersJsonSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"a": {"type": "string"}, "strict": {"type": "boolean"}},
          },
        }
      ]
    }
  ], tools
  assert tools["toolConfig"] == {
    "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["weather"]}
  }
  search = body("m", web_search_options={}, tool_choice="none")
  assert search["tools"] == [{"googleSearch": {}}]
  assert search["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}
  legacy = body("m", functions=[{"name": "weather"}])
  assert legacy["tools"] == [{"functionDeclarations": [{"name": "weather"}]}]

  answer = {
    "candidates": [
      {
        "content": {"parts": [{"text": "plan", "thought": True}, {"text": "one"}]},
        "finishReason": "STOP",
      },
      {"content": {"parts": [{"text": "two"}]}, "finishReason": "MAX_TOKENS"},
    ]
  }
  result = GEMINI.completion(answer, "gemini/m")
  first, second = result["choices"]
  assert first["message"] == {
    "role": "assistant",
    "content": "one",
    "reasoning_content": "plan",
  }
  assert second["index"] == 1
  assert second["finish_reason"] == "length"


def test_signatures() -> None:
  def call(name: str, extra: dict | None = None) -> dict:
    found = {
      "id": name,
      "type": "function",
      "function": {"name": name, "arguments": "{}"},
    }
    return {**found, "extra_content": extra} if extra else found

  signed = {"google": {"thought_signature": "real"}}
  messages = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": None, "tool_calls": [call("a"), call("b")]},
    {"role": "tool", "tool_call_id": "a", "content": "1"},
    {"role": "tool", "tool_call_id": "b", "content": "2"},
    {"role": "assistant", "content": None, "tool_calls": [call("c", signed)]},
    {"role": "tool", "tool_call_id": "c", "content": "3"},
  ]
  sent = GEMINI.body("gemini-3.7-flash", {"messages": messages})["contents"]
  first, second = (c["parts"] for c in sent if c["role"] == "model")
  assert first[0]["thoughtSignature"] == "skip_thought_signature_validator", (
    "an unsigned first call gets the dummy signature"
  )
  assert "thoughtSignature" not in first[1], "only the first call of a step"
  assert second[0]["thoughtSignature"] == "real", "the Google form of extra_content"
  older = GEMINI.body("gemini-2.5-flash", {"messages": messages})["contents"]
  assert "thoughtSignature" not in older[1]["parts"][0], "Gemini 2.5 needs no signature"


def test_stored_signatures() -> None:
  def answer(identifier: str) -> dict:
    call = {"name": "f", "args": {}, "id": identifier}
    part = {"functionCall": call, "thoughtSignature": "sig-" + identifier}
    return {"candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}]}

  def history(identifier: str) -> list[dict]:
    call = {
      "id": identifier,
      "type": "function",
      "function": {"name": "f", "arguments": "{}"},
    }
    return [
      {"role": "user", "content": "hi"},
      {"role": "assistant", "content": None, "tool_calls": [call]},
      {"role": "tool", "tool_call_id": identifier, "content": "1"},
    ]

  def signature(slug: str, identifier: str) -> str:
    sent = GEMINI.body(slug, {"messages": history(identifier)})["contents"]
    return sent[1]["parts"][0]["thoughtSignature"]

  message = GEMINI.completion(answer("c1"), "gemini/gemini-3.7-flash")["choices"][0]
  assert message["message"]["tool_calls"][0]["id"] == "c1"
  assert signature("gemini-3.7-flash", "c1") == "sig-c1", "the stored signature returns"
  assert signature("gemini-3.8-flash", "c1") == "skip_thought_signature_validator", (
    "another model gets the dummy"
  )

  async def streamed() -> None:
    body = "data: " + json.dumps(answer("c2")) + "\n\n"
    response = httpx.Response(200, content=body.encode())
    async for _ in GEMINI.stream(response, "gemini/gemini-3.7-flash", False):
      pass

  asyncio.run(streamed())
  assert signature("gemini-3.7-flash", "c2") == "sig-c2", "a stream stores it too"
  signatures.IDLE_SECONDS = -1.0
  try:
    assert signature("gemini-3.7-flash", "c2") == "skip_thought_signature_validator", (
      "an expired signature is not used"
    )
  finally:
    signatures.IDLE_SECONDS = 3600.0
