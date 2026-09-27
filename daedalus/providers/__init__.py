"""Provider adapters behind the OpenAI-compatible router."""

from collections.abc import Mapping

from daedalus.providers.base import OpenAIProvider, ProviderError, frame
from daedalus.providers.gemini import GeminiProvider
from daedalus.providers.interactions import InteractionsProvider

API_TYPES: dict[str, type[OpenAIProvider]] = {
  "openai": OpenAIProvider,
  "gemini": GeminiProvider,
  "interactions": InteractionsProvider,
}

__all__ = [
  "API_TYPES",
  "GeminiProvider",
  "InteractionsProvider",
  "OpenAIProvider",
  "ProviderError",
  "frame",
  "prepare",
]


def prepare(
  model: str, payload: dict, config: Mapping
) -> tuple[OpenAIProvider, str, dict, dict[str, str]]:
  """Build the provider and the upstream request for one `provider/slug` model."""
  name, separator, slug = model.partition("/")
  settings = config.get(name)
  if not separator or not slug or not isinstance(settings, dict):
    raise ProviderError(f"Unknown provider model: {model}")
  api_type = settings.get("api_type", "openai")
  if api_type not in API_TYPES:
    raise ProviderError(f"Unknown api_type for {name}: {api_type}")
  provider = API_TYPES[api_type](name, settings)
  return (provider, *provider.request(slug, payload))
