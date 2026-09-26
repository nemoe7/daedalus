"""Runnable check for the interactions translator. Run: python tests/test_interactions.py"""

import sys

from daedalus import interactions


def check_text_input() -> None:
  """A string input becomes one user message."""
  body = {
    "model": "gemini-3.5-flash",
    "input": "Why is the sky blue?",
    "generation_config": {"temperature": 0.4, "thinking_level": "low"},
  }
  payload = interactions.to_openai(body)
  assert payload["model"] == "gemini-3.5-flash"
  assert payload["messages"] == [{"role": "user", "content": "Why is the sky blue?"}], (
    payload["messages"]
  )
  assert payload["temperature"] == 0.4
  assert "thinking_level" not in payload


def check_multimodal_input() -> None:
  """Inline data and a file uri both become image parts."""
  body = {
    "model": "m",
    "input": [
      {"type": "text", "text": "What is in this image?"},
      {"type": "image", "data": "AAAA", "mime_type": "image/png"},
    ],
  }
  content = interactions.to_openai(body)["messages"][0]["content"]
  assert content[0] == {"type": "text", "text": "What is in this image?"}
  assert content[1]["image_url"]["url"] == "data:image/png;base64,AAAA"
  remote = {"model": "m", "input": [{"type": "image", "uri": "https://x/y.png"}]}
  only = interactions.to_openai(remote)["messages"][0]["content"]
  assert only[0]["image_url"]["url"] == "https://x/y.png"


def check_function_tools() -> None:
  """A function tool crosses over, and a search tool does not."""
  body = {
    "model": "m",
    "input": "weather in london",
    "tools": [
      {"type": "google_search"},
      {
        "type": "function",
        "name": "get_temperature",
        "description": "Read the temperature",
        "parameters": {
          "type": "object",
          "properties": {"location": {"type": "string"}},
        },
      },
    ],
  }
  payload = interactions.to_openai(body)
  assert payload["tools"] == [
    {
      "type": "function",
      "function": {
        "name": "get_temperature",
        "description": "Read the temperature",
        "parameters": {
          "type": "object",
          "properties": {"location": {"type": "string"}},
        },
      },
    }
  ], payload["tools"]


def check_function_result() -> None:
  """A function result becomes a tool message with the call id."""
  body = {
    "model": "m",
    "input": [
      {
        "type": "function_result",
        "name": "get_temperature",
        "call_id": "call_7",
        "result": [{"type": "text", "text": '{"temperature": "15C"}'}],
      }
    ],
  }
  message = interactions.to_openai(body)["messages"][0]
  assert message == {
    "role": "tool",
    "tool_call_id": "call_7",
    "content": '{"temperature": "15C"}',
  }, message


def check_response_format() -> None:
  """Google writes schema types in capitals, so they come down a case."""
  body = {
    "model": "m",
    "input": "a recipe",
    "response_format": [
      {
        "type": "text",
        "mime_type": "application/json",
        "schema": {
          "type": "OBJECT",
          "properties": {"name": {"type": "STRING"}},
          "required": ["name"],
        },
      }
    ],
  }
  fmt = interactions.to_openai(body)["response_format"]
  assert fmt["json_schema"]["schema"] == {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
  }, fmt
  plain = {"model": "m", "response_format": [{"mime_type": "application/json"}]}
  assert interactions.to_openai(plain)["response_format"] == {"type": "json_object"}


def check_model_resolution() -> None:
  """The model comes from the body, or from the agent config."""
  assert interactions.model_of({"model": "gemini-3.5-flash"}) == "gemini-3.5-flash"
  assert (
    interactions.model_of({"agent_config": {"type": "antigravity", "model": "lite"}})
    == "lite"
  )
  assert interactions.model_of({"agent": "antigravity-preview-09-2026"}) == ""


def check_answer() -> None:
  """An OpenAI answer comes back as an interaction."""
  answer = {
    "id": "chatcmpl-1",
    "model": "gpt-4o",
    "choices": [
      {
        "index": 0,
        "message": {"role": "assistant", "content": "Because of scattering."},
        "finish_reason": "stop",
      }
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
  }
  result = interactions.to_interaction(answer, {"input": "Why?"}, "m")
  assert result["object"] == "interaction"
  assert result["status"] == "completed"
  assert result["id"] == "chatcmpl-1"
  assert result["model"] == "gpt-4o"
  assert result["usage"] == {
    "prompt_tokens": 9,
    "completion_tokens": 4,
    "total_tokens": 13,
  }
  assert result["steps"][0] == {
    "type": "user_input",
    "status": "done",
    "content": [{"type": "text", "text": "Why?"}],
  }
  assert result["steps"][1]["content"] == [
    {"type": "text", "text": "Because of scattering."}
  ]


def check_requires_action() -> None:
  """A tool call asks the caller to act."""
  answer = {
    "choices": [
      {
        "index": 0,
        "message": {
          "content": None,
          "tool_calls": [
            {
              "id": "call_3",
              "type": "function",
              "function": {"name": "get_temperature", "arguments": '{"a": 1}'},
            }
          ],
        },
        "finish_reason": "tool_calls",
      }
    ]
  }
  result = interactions.to_interaction(answer, {"input": "weather"}, "m")
  assert result["status"] == "requires_action"
  assert result["steps"][1] == {
    "type": "function_call",
    "id": "call_3",
    "name": "get_temperature",
    "arguments": {"a": 1},
  }


def check_incomplete() -> None:
  """A cut answer is incomplete."""
  answer = {
    "choices": [{"index": 0, "message": {"content": "cut"}, "finish_reason": "length"}]
  }
  assert interactions.to_interaction(answer, {}, "m")["status"] == "incomplete"


def check_stream_events() -> None:
  """The stream opens, carries deltas, and closes."""
  stream = interactions.Stream("m", "int_9")
  names = [name for name, _ in stream.open()]
  assert names == ["interaction.created", "interaction.in_progress", "step.start"], (
    names
  )
  assert stream.open() == []
  events = stream.delta("Hel")
  assert [name for name, _ in events] == ["step.delta"]
  assert events[0][1]["delta"] == {"type": "text", "text": "Hel"}
  closed = stream.close({"usage": {"total_tokens": 5}})
  assert [name for name, _ in closed] == ["step.stop", "interaction.completed"]
  assert closed[1][1]["interaction"]["usage"]["total_tokens"] == 5
  raw = interactions.frame([("step.delta", {"a": 1})])
  assert raw == b'event: step.delta\ndata: {"a": 1}\n\n', raw


def check_empty_input() -> None:
  """An empty input does not raise."""
  payload = interactions.to_openai({})
  assert payload["model"] == ""
  assert payload["messages"] == []
  result = interactions.to_interaction({}, {}, "m")
  assert result["status"] == "completed"
  assert result["id"] == interactions.LOCAL_ID


def main() -> int:
  """Run every check."""
  check_text_input()
  check_multimodal_input()
  check_function_tools()
  check_function_result()
  check_response_format()
  check_model_resolution()
  check_answer()
  check_requires_action()
  check_incomplete()
  check_stream_events()
  check_empty_input()
  print("ok: interactions translation checks passed")
  return 0


if __name__ == "__main__":
  sys.exit(main())
