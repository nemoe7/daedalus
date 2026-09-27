from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider


class GroqProvider(OpenAIProvider):
  """Groq through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.groq.com/openai/v1",
    "discovery_url": "https://api.groq.com/openai/v1/models",
  }
