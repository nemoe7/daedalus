"""Tests for the Mistral reasoning changes."""

import asyncio
import json

import httpx
import yaml

from daedalus.catalog.enrichment import config_params
from daedalus.providers.mistral import MistralProvider

PROVIDER = MistralProvider(
  "mistral", {"api_base": "https://mistral.test/v1", "api_key": "k"}
)
THINKING = [
  {"type": "thinking", "thinking": [{"type": "text", "text": "17 * 23 = 391"}]},
  {"type": "text", "text": "391"},
]


def sent_effort(effort: object) -> object:
  """The effort that goes to Mistral for one client value."""
  payload = {
    "messages": [{"role": "user", "content": "hi"}],
    "reasoning_effort": effort,
  }
  return PROVIDER.request("labs-leanstral-1-5", payload)[1]["reasoning_effort"]


def test_efforts() -> None:
  for effort, expected in (
    ("none", "none"),
    ("minimal", "none"),
    ("low", "high"),
    ("medium", "high"),
    ("high", "high"),
    ("xhigh", "high"),
    ("other", "other"),
  ):
    assert sent_effort(effort) == expected, (effort, sent_effort(effort))
  payload = {"messages": [{"role": "user", "content": "hi"}]}
  assert "reasoning_effort" not in PROVIDER.request("m", payload)[1], "no effort added"


def test_completion() -> None:
  answer = {
    "choices": [{"index": 0, "message": {"role": "assistant", "content": THINKING}}]
  }
  message = PROVIDER.completion(answer, "mistral/m")["choices"][0]["message"]
  assert message == {
    "role": "assistant",
    "content": "391",
    "reasoning_content": "17 * 23 = 391",
  }, message
  simple = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
  assert PROVIDER.completion(simple, "mistral/m") == simple, "a string content stays"


async def streamed(lines: list[str]) -> list[str]:
  """The data payloads that the provider stream sends for the upstream lines."""
  body = "".join(f"{line}\n\n" for line in lines).encode()
  response = httpx.Response(200, content=body)
  out = b"".join(
    [chunk async for chunk in PROVIDER.stream(response, "mistral/m", False)]
  )
  return [block[6:] for block in out.decode().split("\n\n") if block]


def test_stream() -> None:
  delta = {"choices": [{"index": 0, "delta": {"content": THINKING[:1]}}]}
  text = {"choices": [{"index": 0, "delta": {"content": "391"}}]}
  found = asyncio.run(
    streamed(
      [f"data: {json.dumps(delta)}", f"data: {json.dumps(text)}", "data: [DONE]"]
    )
  )
  assert found[-1] == "[DONE]", found
  first = json.loads(found[0])["choices"][0]["delta"]
  assert first == {"content": "", "reasoning_content": "17 * 23 = 391"}, first
  assert json.loads(found[1]) == text, "a string delta stays"
  cut = asyncio.run(streamed([f"data: {json.dumps(text)}"]))
  assert "[DONE]" not in cut, "no [DONE] when Mistral sends none"


def test_discoverable() -> None:
  """Alias rows and the models of OCR, moderation and realtime audio stay out."""
  chat = {"completion_chat": True}
  rows = {
    "mistral-medium-latest": ("mistral-medium-latest", chat),
    "mistral-medium-2604": ("mistral-medium-latest", chat),
    "mistral-vibe-cli-latest": ("mistral-medium-latest", chat),
    "mistral-ocr-2512": ("mistral-ocr-2512", {"ocr": True}),
    "mistral-moderation-2603": ("mistral-moderation-2603", {"moderation": True}),
    "voxtral-mini-realtime-2602": (
      "voxtral-mini-realtime-2602",
      {"audio_transcription_realtime": True},
    ),
    "codestral-embed": ("codestral-embed", {}),
  }
  kept = [
    slug
    for slug, (name, capabilities) in rows.items()
    if MistralProvider.discoverable(
      {"id": slug, "name": name, "capabilities": capabilities}
    )
  ]
  assert kept == ["mistral-medium-latest", "codestral-embed"], kept


def test_modes() -> None:
  with open("config/providers/free.yml", encoding="utf-8") as handle:
    mistral = yaml.safe_load(handle)["mistral"]
  for slug, mode in (
    ("voxtral-mini-2507", "audio_transcription"),
    ("voxtral-mini-tts-2603", "audio_speech"),
    ("voxtral-small-latest", None),
  ):
    assert config_params(mistral, slug).get("mode") == mode, slug


def test_history() -> None:
  thinking = {"type": "thinking", "thinking": [{"type": "text", "text": "hmm"}]}
  call = {"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}
  messages = [
    {"role": "user", "content": "hi", "reasoning_content": "kept out"},
    {"role": "assistant", "content": "391", "reasoning_content": "hmm"},
    {
      "role": "assistant",
      "content": None,
      "tool_calls": [call],
      "reasoning_content": "hmm",
    },
    {
      "role": "assistant",
      "content": [{"type": "text", "text": "a"}],
      "reasoning_content": "hmm",
    },
    {"role": "assistant", "content": "plain", "reasoning_content": ""},
  ]
  sent = PROVIDER.request("m", {"messages": messages})[1]["messages"]
  assert sent[0] == {"role": "user", "content": "hi"}, "only assistant messages change"
  assert sent[1]["content"] == [thinking, {"type": "text", "text": "391"}], sent[1]
  assert sent[2]["content"] == [thinking] and sent[2]["tool_calls"] == [call], sent[2]
  assert sent[3]["content"] == [thinking, {"type": "text", "text": "a"}], sent[3]
  assert sent[4] == {"role": "assistant", "content": "plain"}, (
    "empty thinking adds no chunk"
  )
  assert all("reasoning_content" not in m for m in sent), sent
  assert messages[1]["content"] == "391", "the client body does not change"
