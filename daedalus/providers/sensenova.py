from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider


class SenseNovaProvider(OpenAIProvider):
  """SenseNova through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://token.sensenova.ai/v1",
    "discovery_url": "https://token.sensenova.ai/v1/models",
  }

  @staticmethod
  def discoverable(row: dict) -> bool:
    """Keep only rows that make text: the image rows output an image."""
    modalities = row.get("output_modalities")
    return not isinstance(modalities, list) or "text" in modalities
