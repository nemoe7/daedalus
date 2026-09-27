"""Input size estimates and the context window skip."""

import logging
from typing import Any

from fastapi.responses import JSONResponse

logger = logging.getLogger("daedalus")

# Parts that hold binary data. Their text does not count as input tokens.
BINARY_KEYS = frozenset({"image_url", "input_audio", "file"})


def text_length(node: object) -> int:
  """The characters of all strings in a message tree, without binary parts."""
  if isinstance(node, str):
    return len(node)
  if isinstance(node, list):
    return sum(text_length(item) for item in node)
  if isinstance(node, dict):
    return sum(
      text_length(value) for key, value in node.items() if key not in BINARY_KEYS
    )
  return 0


def input_tokens(body: dict[str, Any]) -> int:
  """An estimate of the input tokens: the characters of the messages and tools, divided by 4."""
  characters = text_length(body.get("messages")) + text_length(body.get("tools"))
  return -(-characters // 4)


def too_large(candidate: str, tokens: int, limits: dict[str, int]) -> bool:
  """Tell if the input does not fit the model, and log the skip."""
  limit = limits.get(candidate)
  if limit is None or tokens <= limit:
    return False
  logger.warning("skip %s: input ~%d tokens > limit %d", candidate, tokens, limit)
  return True


def too_long(tokens: int) -> JSONResponse:
  """The OpenAI error for an input that no model in the chain can take."""
  message = (
    f"The input (~{tokens} tokens) is larger than the context window of each model"
  )
  body = {
    "error": {
      "message": message,
      "type": "invalid_request_error",
      "code": "context_length_exceeded",
    }
  }
  return JSONResponse(body, status_code=400)
