from collections.abc import Mapping
from typing import ClassVar

from daedalus.providers.base import OpenAIProvider


class CloudflareProvider(OpenAIProvider):
  """Cloudflare Workers AI through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "os.environ/CLOUDFLARE_API_BASE",
    "discovery_url": "https://api.cloudflare.com/client/v4/accounts/os.environ/CLOUDFLARE_ACCOUNT_ID/ai/models/search?per_page=100",
  }
