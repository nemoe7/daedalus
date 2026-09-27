from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider


class ZAiProvider(OpenAIProvider):
  """Z.ai through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.z.ai/api/paas/v4",
    "discovery_url": "https://api.z.ai/api/paas/v4/models",
  }
