from daedalus.providers.groq import GroqProvider

PROVIDER = GroqProvider("groq", {"api_base": "https://g.test/v1", "api_key": "k"})


def test_groq_drops_empty_assistant_message() -> None:
  """An assistant message with empty content and no tool calls is dropped."""
  messages = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "reasoning_content": "think"},
  ]
  body = PROVIDER.body("m", {"messages": messages})
  assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_groq_keeps_empty_assistant_with_tool_calls() -> None:
  """An assistant message with empty content and tool calls is kept."""
  messages = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
  ]
  body = PROVIDER.body("m", {"messages": messages})
  assert body["messages"][1] == {
    "role": "assistant",
    "content": "",
    "tool_calls": [{"id": "c1"}],
  }


def test_groq_strips_reasoning_from_text_assistant() -> None:
  """An assistant message with text keeps the text and loses reasoning_content."""
  messages = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "Hel", "reasoning_content": "think"},
  ]
  body = PROVIDER.body("m", {"messages": messages})
  assert body["messages"][1] == {"role": "assistant", "content": "Hel"}
