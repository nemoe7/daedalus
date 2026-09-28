"""Provider adapters behind the OpenAI-compatible router."""

from collections.abc import Mapping
from typing import Any

from daedalus.config import block_for, expand
from daedalus.providers.base import (
  OpenAIProvider,
  ProviderError,
  check_wait,
  effort_text,
  frame,
)
from daedalus.providers.cloudflare import CloudflareProvider
from daedalus.providers.gemini import GeminiProvider
from daedalus.providers.groq import GroqProvider
from daedalus.providers.kilo import KiloProvider
from daedalus.providers.mistral import MistralProvider
from daedalus.providers.openrouter import OpenRouterProvider
from daedalus.providers.zai import ZAiProvider

API_TYPES: dict[str, type[OpenAIProvider]] = {
  "openai": OpenAIProvider,
  "gemini": GeminiProvider,
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
  "check_wait",
  "effort_text",
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
  provider, slug = provider_for(model, config)
  return (provider, *provider.request(slug, payload))


def provider_for(model: str, config: Mapping) -> tuple[OpenAIProvider, str]:
  """Build the provider and the slug for one `provider/slug` model."""
  name, separator, slug = model.partition("/")
  raw = block_for(config, name, slug) if separator else None
  if not separator or not slug or raw is None:
    raise ProviderError(f"Unknown provider model: {model}")
  merged = settings(name, raw)
  api_type = merged.get("api_type", "openai")
  if api_type not in API_TYPES:
    raise ProviderError(f"Unknown api_type for {name}: {api_type}")
  kind = PROVIDERS.get(name, OpenAIProvider)
  if api_type != kind.defaults.get("api_type"):
    kind = API_TYPES[api_type]
  return kind(name, merged), slug
