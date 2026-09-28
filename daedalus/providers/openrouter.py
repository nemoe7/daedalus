from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider, free_stealth, openrouter_columns


class OpenRouterProvider(OpenAIProvider):
  """OpenRouter through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://openrouter.ai/api/v1",
    "discovery_url": "https://openrouter.ai/api/v1/models",
  }

  columns = staticmethod(openrouter_columns)
  exclude_exempt = staticmethod(free_stealth)
