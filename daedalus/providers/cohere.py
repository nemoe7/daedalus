from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider


class CohereProvider(OpenAIProvider):
  """Cohere through its OpenAI-compatible endpoint."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.cohere.com/compatibility/v1",
    "discovery_url": "https://api.cohere.com/compatibility/v1/models",
  }

  # The compatibility endpoint rejects these Chat Completions fields.
  unsupported: tuple[str, ...] = ("n", "logit_bias", "parallel_tool_calls")
