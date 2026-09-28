from collections.abc import Mapping
from typing import Any, ClassVar

from daedalus.providers.base import OpenAIProvider, free_stealth, openrouter_columns


class OpenRouterProvider(OpenAIProvider):
  """OpenRouter through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://openrouter.ai/api/v1",
    "discovery_url": "https://openrouter.ai/api/v1/models",
  }
  stream_usage: ClassVar[bool] = True

  # The fields of the OpenRouter Image API. It always answers with `b64_json`.
  image_fields: ClassVar[tuple[str, ...]] = (
    "prompt",
    "n",
    "size",
    "quality",
    "background",
    "output_format",
    "output_compression",
  )

  columns = staticmethod(openrouter_columns)
  exclude_exempt = staticmethod(free_stealth)

  def image_request(
    self, slug: str, payload: dict
  ) -> tuple[str, dict[str, Any], dict[str, str]]:
    """The OpenRouter Image API request, at `/images`."""
    _, options, headers = super().image_request(slug, payload)
    return self.base + "/images", options, headers
