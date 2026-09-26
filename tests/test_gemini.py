"""Runnable check for the Gemini translator. Run: python tests/test_gemini.py"""

import sys

from daedalus import gemini


def check_text_request() -> None:
  """Contents, the system instruction, and the config all cross over."""
  body = {
    "system_instruction": {"parts": [{"text": "Answer in one line."}]},
    "contents": [
      {"role": "user", "parts": [{"text": "first"}]},
      {"role": "model", "parts": [{"text": "second"}]},
    ],
    "generation_config": {
      "temperature": 0.2,
      "topP": 0.9,
      "maxOutputTokens": 64,
      "stopSequences": ["END"],
      "candidateCount": 2,
      "top_k": 40,
    },
  }
  payload = gemini.to_openai("gemini-2.5-flash", body)
  assert payload["model"] == "gemini-2.5-flash"
  assert payload["messages"] == [
    {"role": "system", "content": "Answer in one line."},
    {"role": "user", "content": "first"},
    {"role": "assistant", "content": "second"},
  ], payload["messages"]
  assert payload["temperature"] == 0.2
  assert payload["top_p"] == 0.9
  assert payload["max_tokens"] == 64
  assert payload["stop"] == ["END"]
  assert payload["n"] == 2
  assert "top_k" not in payload


def check_snake_case_request() -> None:
  """The snake_case names work too."""
  body = {
    "systemInstruction": {"parts": [{"text": "Be terse."}]},
    "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    "generation_config": {"max_output_tokens": 8},
  }
  payload = gemini.to_openai("gemini-2.5-flash", body)
  assert payload["messages"][0] == {"role": "system", "content": "Be terse."}
  assert payload["max_tokens"] == 8


def check_image_request() -> None:
  """Inline data becomes an image content part."""
  body = {
    "contents": [
      {
        "role": "user",
        "parts": [
          {"text": "What is this?"},
          {"inline_data": {"mime_type": "image/png", "data": "AAAA"}},
        ],
      }
    ]
  }
  payload = gemini.to_openai("gemini-2.5-flash", body)
  content = payload["messages"][0]["content"]
  assert content[0] == {"type": "text", "text": "What is this?"}
  assert content[1]["image_url"]["url"] == "data:image/png;base64,AAAA"


def check_tool_request() -> None:
  """Function declarations become OpenAI tools."""
  body = {
    "contents": [{"role": "user", "parts": [{"text": "weather"}]}],
    "generation_config": {
      "tools": [
        {
          "function_declarations": [
            {
              "name": "get_weather",
              "description": "Read the weather",
              "parameters": {"type": "object"},
            }
          ]
        }
      ]
    },
  }
  payload = gemini.to_openai("gemini-2.5-flash", body)
  assert payload["tools"] == [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Read the weather",
        "parameters": {"type": "object"},
      },
    }
  ], payload["tools"]


def check_tool_history() -> None:
  """A function response keeps the id of the call it answers."""
  body = {
    "contents": [
      {"role": "user", "parts": [{"text": "weather in manila"}]},
      {
        "role": "model",
        "parts": [
          {"functionCall": {"name": "get_weather", "args": {"city": "Manila"}}}
        ],
      },
      {
        "role": "user",
        "parts": [
          {"functionResponse": {"name": "get_weather", "response": {"temp": 30}}}
        ],
      },
    ]
  }
  payload = gemini.to_openai("gemini-2.5-flash", body)
  call = payload["messages"][1]["tool_calls"][0]
  assert call["function"] == {
    "name": "get_weather",
    "arguments": '{"city": "Manila"}',
  }, call
  reply = payload["messages"][2]
  assert reply["role"] == "tool"
  assert reply["tool_call_id"] == call["id"]
  assert reply["content"] == '{"temp": 30}'


def check_response_format() -> None:
  """A JSON mime type becomes a response format, with or without a schema."""
  plain = gemini.to_openai(
    "m", {"generation_config": {"responseMimeType": "application/json"}}
  )
  assert plain["response_format"] == {"type": "json_object"}
  schema = {"type": "object", "properties": {"a": {"type": "string"}}}
  shaped = gemini.to_openai(
    "m",
    {
      "generation_config": {
        "response_mime_type": "application/json",
        "response_json_schema": schema,
      }
    },
  )
  assert shaped["response_format"] == {
    "type": "json_schema",
    "json_schema": {"name": "response", "schema": schema},
  }
  text = gemini.to_openai(
    "m", {"generation_config": {"responseMimeType": "text/plain"}}
  )
  assert "response_format" not in text


def check_answer() -> None:
  """An OpenAI answer comes back in the Gemini shape."""
  answer = {
    "id": "chatcmpl-1",
    "model": "gpt-4o",
    "choices": [
      {
        "index": 0,
        "message": {"role": "assistant", "content": "It is sunny."},
        "finish_reason": "stop",
      }
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
  }
  result = gemini.to_gemini(answer, "gemini-2.5-flash")
  assert result["candidates"][0]["content"] == {
    "parts": [{"text": "It is sunny."}],
    "role": "model",
  }
  assert result["candidates"][0]["finishReason"] == "STOP"
  assert result["usageMetadata"] == {
    "promptTokenCount": 11,
    "candidatesTokenCount": 4,
    "totalTokenCount": 15,
  }
  assert result["modelVersion"] == "gpt-4o"
  assert result["responseId"] == "chatcmpl-1"


def check_answer_with_tool_call() -> None:
  """A tool call comes back as a function call part."""
  answer = {
    "choices": [
      {
        "index": 0,
        "message": {
          "role": "assistant",
          "content": None,
          "tool_calls": [
            {
              "id": "call_9",
              "type": "function",
              "function": {
                "name": "get_weather",
                "arguments": '{"city": "Manila"}',
              },
            }
          ],
        },
        "finish_reason": "tool_calls",
      }
    ]
  }
  result = gemini.to_gemini(answer, "m")
  part = result["candidates"][0]["content"]["parts"][0]
  assert part == {"functionCall": {"name": "get_weather", "args": {"city": "Manila"}}}
  assert result["candidates"][0]["finishReason"] == "STOP"


def check_length_reason() -> None:
  """The finish reasons cross over."""
  answer = {
    "choices": [{"index": 0, "message": {"content": "cut"}, "finish_reason": "length"}]
  }
  assert gemini.to_gemini(answer, "m")["candidates"][0]["finishReason"] == "MAX_TOKENS"


def check_chunk() -> None:
  """One stream chunk becomes one Gemini chunk."""
  chunk = {
    "model": "gpt-4o",
    "choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}],
  }
  result = gemini.chunk_to_gemini(chunk, "m")
  assert result["candidates"][0]["content"]["parts"] == [{"text": "Hel"}]
  assert "finishReason" not in result["candidates"][0]
  last = {
    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
  }
  final = gemini.chunk_to_gemini(last, "m")
  assert final["candidates"][0]["finishReason"] == "STOP"
  assert final["usageMetadata"]["totalTokenCount"] == 4


def check_streamed_tool_call() -> None:
  """Tool call fragments come together on the chunk that ends them."""
  calls = gemini.StreamCalls()

  def parts(chunk: dict) -> list:
    return gemini.chunk_to_gemini(chunk, "m", calls)["candidates"][0]["content"][
      "parts"
    ]

  head = {
    "choices": [
      {
        "index": 0,
        "delta": {
          "tool_calls": [
            {"index": 0, "function": {"name": "get_weather", "arguments": '{"ci'}}
          ]
        },
      }
    ]
  }
  assert parts(head) == []
  tail = {
    "choices": [
      {
        "index": 0,
        "delta": {
          "tool_calls": [{"index": 0, "function": {"arguments": 'ty": "Manila"}'}}]
        },
      }
    ]
  }
  assert parts(tail) == []
  end = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
  assert parts(end) == [
    {"functionCall": {"name": "get_weather", "args": {"city": "Manila"}}}
  ]


def check_empty_shapes() -> None:
  """An empty body or answer does not raise."""
  assert gemini.to_openai("m", {}) == {"model": "m", "messages": []}
  result = gemini.to_gemini({}, "m")
  assert result["candidates"][0]["content"]["parts"] == []
  assert result["modelVersion"] == "m"


def main() -> int:
  """Run every check."""
  check_text_request()
  check_snake_case_request()
  check_image_request()
  check_tool_request()
  check_tool_history()
  check_response_format()
  check_answer()
  check_answer_with_tool_call()
  check_length_reason()
  check_chunk()
  check_streamed_tool_call()
  check_empty_shapes()
  print("ok: gemini translation checks passed")
  return 0


if __name__ == "__main__":
  sys.exit(main())
