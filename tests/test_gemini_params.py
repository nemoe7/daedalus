from daedalus import providers

GEMINI = providers.GeminiProvider(
  "gemini", {"api_base": "https://gemini.test", "api_key": "k"}
)


def body(model: str, **extra: object) -> dict:
  payload = {"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}
  return GEMINI.body(model, payload)


def main() -> None:
  sent = body(
    "gemini-2.5-flash",
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
    frequency_penalty=0.1,
    presence_penalty=0.2,
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
    "frequencyPenalty": 0.1,
    "presencePenalty": 0.2,
    "thinkingConfig": {"thinkingBudget": 1, "includeThoughts": True},
    "responseModalities": ["TEXT", "IMAGE", "MODALITY_UNSPECIFIED"],
  }, sent
  assert sent["serviceTier"] == "priority"
  assert body("m", service_tier="default")["serviceTier"] == "standard"

  gemini3 = body("gemini-3.5-flash", frequency_penalty=1, reasoning_effort="medium")
  assert gemini3["generationConfig"] == {
    "thinkingConfig": {"thinkingLevel": "medium", "includeThoughts": True},
    "temperature": 1.0,
  }, gemini3
  pro = body("gemini-3-pro", reasoning_effort={"effort": "none"})["generationConfig"]
  assert pro["thinkingConfig"] == {"thinkingLevel": "low", "includeThoughts": False}
  for effort, budget in (("low", 1024), ("medium", 2048), ("high", 4096), ("none", 0)):
    config = body("gemini-2.5-pro", reasoning_effort=effort)["generationConfig"]
    assert config["thinkingConfig"]["thinkingBudget"] == budget, config
  assert (
    body("gemini-2.5-pro", reasoning_effort="minimal")["generationConfig"][
      "thinkingConfig"
    ]["thinkingBudget"]
    == 128
  )
  assert (
    body("gemini-2.5-flash-lite", reasoning_effort="minimal")["generationConfig"][
      "thinkingConfig"
    ]["thinkingBudget"]
    == 512
  )
  thinking = body("gemini-2.5-pro", thinking={"type": "enabled", "budget_tokens": 99})
  assert thinking["generationConfig"]["thinkingConfig"] == {
    "includeThoughts": True,
    "thinkingBudget": 99,
  }
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
  check_signatures()
  print("ok: gemini parameter mapping")


def check_signatures() -> None:
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


if __name__ == "__main__":
  main()
