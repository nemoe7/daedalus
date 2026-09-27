"""Provider adapters behind the OpenAI-compatible router."""

from collections.abc import Mapping
from typing import Any

from daedalus.config import expand
from daedalus.providers.base import OpenAIProvider, ProviderError, frame
from daedalus.providers.cloudflare import CloudflareProvider
from daedalus.providers.gemini import GeminiProvider
from daedalus.providers.groq import GroqProvider
from daedalus.providers.interactions import InteractionsProvider
from daedalus.providers.kilo import KiloProvider
from daedalus.providers.mistral import MistralProvider
from daedalus.providers.openrouter import OpenRouterProvider
from daedalus.providers.zai import ZAiProvider

API_TYPES: dict[str, type[OpenAIProvider]] = {
  "openai": OpenAIProvider,
  "gemini": GeminiProvider,
  "interactions": InteractionsProvider,
}
PROVIDERS: dict[str, type[OpenAIProvider]] = {
  "cloudflare": CloudflareProvider,
  "gemini": GeminiProvider,
  "groq": GroqProvider,
  "kilo": KiloProvider,
  "mistral": MistralProvider,
  "openrouter": OpenRouterProvider,
  "z-ai": ZAiProvider,
}

__all__ = [
  "API_TYPES",
  "PROVIDERS",
  "OpenAIProvider",
  "ProviderError",
  "frame",
  "prepare",
  "settings",
]


def settings(name: str, config: Mapping[str, Any]) -> dict[str, Any]:
  """Merge one provider's non-empty config values over its class defaults."""
  defaults = expand(dict(PROVIDERS.get(name, OpenAIProvider).defaults))
  present = {key: value for key, value in config.items() if value not in (None, "")}
  return {**defaults, **present}


def prepare(
  model: str, payload: dict, config: Mapping
) -> tuple[OpenAIProvider, str, dict, dict[str, str]]:
  """Build the provider and the upstream request for one `provider/slug` model."""
  name, separator, slug = model.partition("/")
  raw = config.get(name)
  if not separator or not slug or not isinstance(raw, dict):
    raise ProviderError(f"Unknown provider model: {model}")
  merged = settings(name, raw)
  api_type = merged.get("api_type", "openai")
  if api_type not in API_TYPES:
    raise ProviderError(f"Unknown api_type for {name}: {api_type}")
  kind = PROVIDERS.get(name, OpenAIProvider)
  if api_type != kind.defaults.get("api_type"):
    kind = API_TYPES[api_type]
  provider = kind(name, merged)
  return (provider, *provider.request(slug, payload))
