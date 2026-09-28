from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider


class PollinationsProvider(OpenAIProvider):
  """Pollinations through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://gen.pollinations.ai/v1",
    "discovery_url": "https://gen.pollinations.ai/v1/models",
  }
