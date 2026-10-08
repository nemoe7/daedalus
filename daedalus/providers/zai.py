import re
from collections.abc import Mapping
from typing import Any, ClassVar

from daedalus.providers.base import OpenAIProvider

# GLM-4.x has no effort parameter: its thinking is on or off, and the flash rows are the ones
# the catalog serves.
FLASH_4X = re.compile(r"glm-4\.\d+[v]?-flash")


def is_flash(slug: str) -> bool:
  """Whether one slug is a GLM-4.x flash row that reasons through thinking on/off."""
  return bool(FLASH_4X.fullmatch(slug))


class ZAiProvider(OpenAIProvider):
  """Z.ai through its OpenAI-compatible API."""

  defaults: ClassVar[Mapping[str, str]] = {
    "api_type": "openai",
    "api_base": "https://api.z.ai/api/paas/v4",
    "discovery_url": "https://api.z.ai/api/paas/v4/models",
  }

  @staticmethod
  def columns(row: dict) -> dict[str, Any]:
    """The store columns of one Z.ai row: the flash rows claim the on/off ladder."""
    if is_flash(str(row.get("id") or "")):
      return {"supports_reasoning": True, "supported_efforts": ["none", "max"]}
    return {}

  def body(self, slug: str, payload: dict) -> dict:
    found = super().body(slug, payload)
    if not is_flash(slug):
      return found
    effort = found.get("reasoning_effort")
    if effort is None:
      return found
    # A 4.x flash row takes `thinking` on or off, and rejects the effort parameter.
    found.pop("reasoning_effort")
    found["thinking"] = {"type": "disabled" if effort == "none" else "enabled"}
    return found

  def effort(self, payload: dict) -> str | None:
    """The text of one flash body shows the thinking state as its on/off rung."""
    thinking = payload.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") in ("enabled", "disabled"):
      return "max" if thinking["type"] == "enabled" else "none"
    return super().effort(payload)
