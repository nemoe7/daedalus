"""Runnable check for the catalog. Run: .venv/bin/python tests/test_catalog.py"""

import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from daedalus import catalog

CONFIG: dict[str, Any] = {
  "cloudflare": {
    "api_key": "cf-token",
    "discovery_url": "https://cf.test/models/search",
    "exclude": ["llama-guard*", "@cf/zai-org/glm-5.3", "glm-5.2"],
    "tier": {
      "REASONING": ["qwen3*"],
      "COMPLEX": ["*deepseek-v4-pro*"],
    },
  },
  "gemini": {
    "api_key": "gem-token",
    "discovery_url": "https://gem.test/v1beta/models",
    "exclude": ["aqa", "*-latest", "gemini-2.5-flash*"],
    "tier": {"COMPLEX": ["gemini-3.5-flash"]},
  },
  "zai": {
    "api_key": "zai-token",
    "discovery_url": "https://zai.test/v4/models",
    "exclude": ["*"],
    "tier": {"REASONING": ["glm-4.5", "^glm-4\\.[67]$"], "COMPLEX": ["glm-4.5-air"]},
  },
  "nokey": {"api_key": "", "discovery_url": "https://nokey.test/models"},
  "broken": {"api_key": "b-token", "discovery_url": "https://broken.test/models"},
}

PAGES: dict[str, list[dict[str, Any]]] = {}


def make_fetch(pages: dict[str, dict[str, Any]]):
  """Stub fetch: URL to page payload."""

  def fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
    PAGES.setdefault(url, []).append(headers)
    if url.startswith("https://broken.test"):
      raise httpx.ConnectError("unreachable", request=httpx.Request("GET", url))
    return pages[url]

  return fetch


PAYOUT: dict[str, dict[str, Any]] = {
  "https://cf.test/models/search": {
    "success": True,
    "result": [
      {"id": "9c6a6b3e", "name": "@cf/qwen/qwq-32b"},
      {"id": "1a2b3c4d", "name": "llama-guard-4-12b"},
      {"id": "5e6f7a8b", "name": "@cf/zai-org/glm-5.3"},
      {"id": "9f8e7d6c", "name": "deepseek-v4-pro-0813"},
      {"id": "0b1c2d3e", "name": "@cf/meta/llama-3.1-8b-instruct"},
    ],
    "result_info": {"page": 2, "per_page": 20, "total_pages": 2},
  },
  "https://gem.test/v1beta/models": {
    "models": [
      {"name": "models/gemini-3.5-flash"},
      {"name": "models/gemini-2.5-flash-lite"},
      {"name": "models/gemini-pro-latest"},
      {"name": "models/aqa"},
    ],
    "nextPageToken": "page-2",
  },
  "https://gem.test/v1beta/models?pageToken=page-2": {
    "models": [{"name": "models/gemini-3.6-flash"}],
  },
  "https://zai.test/v4/models": {
    "object": "list",
    "data": [
      {"id": "glm-4.5"},
      {"id": "glm-4.6"},
      {"id": "glm-4.7"},
      {"id": "glm-4.5-air"},
      {"id": "glm-4.5-flash"},
    ],
  },
}


def check_matches() -> None:
  assert catalog.matches("glm-4.5", "glm-4.5")
  assert not catalog.matches("glm-4.5", "glm-4.5-air"), "exact must not prefix-match"
  assert catalog.matches("llama-guard*", "llama-guard-4-12b"), "star glob"
  assert catalog.matches("*deepseek-v4-pro*", "deepseek-v4-pro-0813"), "star both ends"
  assert catalog.matches("gemini-3.?-flash", "gemini-3.5-flash"), "question mark glob"
  assert not catalog.matches("gemini-3.?-flash", "gemini-3.15-flash")
  assert catalog.matches("^glm-4\\.[67]$", "glm-4.6"), "regex head"
  assert not catalog.matches("^glm-4\\.[67]$", "glm-4.5"), "regex must anchor"
  assert catalog.matches("*", "anything"), "wildcard claims every row"


def check_extract_slugs() -> None:
  data = catalog.extract_slugs({"data": [{"id": "a"}, {"id": "b"}]})
  assert data == ["a", "b"], data
  gemini = catalog.extract_slugs({"models": [{"name": "models/gemini-3.5-flash"}]})
  assert gemini == ["gemini-3.5-flash"], gemini
  cloudflare = catalog.extract_slugs(
    {"result": [{"id": "9c6a6b3e", "name": "@cf/qwen/qwq-32b"}]}
  )
  assert cloudflare == ["@cf/qwen/qwq-32b"], cloudflare
  junk = catalog.extract_slugs({"data": ["x", {"no_id": 1}, {"id": ""}]})
  assert junk == [], junk


def check_next_page_url() -> None:
  token = catalog.next_page_url("https://gem.test/models", {"nextPageToken": "p2"})
  assert token == "https://gem.test/models?pageToken=p2", token
  more = catalog.next_page_url(
    "https://cf.test/search?per_page=20",
    {"result_info": {"page": 1, "total_pages": 3}},
  )
  assert more == "https://cf.test/search?per_page=20&page=2", more
  last = catalog.next_page_url(
    "https://cf.test/search?page=3",
    {"result_info": {"page": 3, "total_pages": 3}},
  )
  assert last is None, last
  assert catalog.next_page_url("https://x.test/m", {"data": []}) is None


def check_select() -> None:
  provider = CONFIG["cloudflare"]
  slugs = [
    "@cf/qwen/qwq-32b",
    "llama-guard-4-12b",
    "@cf/zai-org/glm-5.3",
    "glm-5.2",
    "deepseek-v4-pro-0813",
    "@cf/meta/llama-3.1-8b-instruct",
  ]
  kept = catalog.select("cloudflare", provider, slugs)
  assert kept == [
    "@cf/meta/llama-3.1-8b-instruct",
    "@cf/qwen/qwq-32b",
    "deepseek-v4-pro-0813",
  ], kept

  rescued = catalog.select(
    "zai", CONFIG["zai"], ["glm-4.5", "glm-4.6", "glm-4.5-flash"]
  )
  assert rescued == ["glm-4.5", "glm-4.6"], rescued

  # The block key supplies the provider, so a `provider/` head on a pattern is dropped.
  headed = {"exclude": ["openrouter/google/gemma-4-26b-a4b-it:free"]}
  dropped = catalog.select("openrouter", headed, ["google/gemma-4-26b-a4b-it:free"])
  assert dropped == [], dropped
  assert catalog.strip_provider_head("openrouter/google/x", "openrouter") == "google/x"
  assert catalog.strip_provider_head("*google/x", "openrouter") == "*google/x"


def check_auth_headers() -> None:
  bearer = catalog.auth_headers("groq", {"api_key": "g"})
  assert bearer == {"Authorization": "Bearer g"}, bearer
  gemini = catalog.auth_headers("gemini", {"api_key": "m"})
  assert gemini == {"x-goog-api-key": "m"}, gemini
  assert catalog.auth_headers("groq", {"api_key": ""}) == {}


def check_discover_provider() -> None:
  fetch = make_fetch(PAYOUT)
  gemini, full = catalog.discover_provider("gemini", CONFIG["gemini"], fetch)
  assert gemini == ["gemini-3.5-flash", "gemini-3.6-flash"], gemini
  assert [row["name"] for row in full["models"]] == [
    "models/gemini-3.5-flash",
    "models/gemini-2.5-flash-lite",
    "models/gemini-pro-latest",
    "models/aqa",
    "models/gemini-3.6-flash",
  ], full
  assert "nextPageToken" not in full, full
  paged = PAGES["https://gem.test/v1beta/models"]
  assert paged[0] == {"x-goog-api-key": "gem-token"}, paged
  assert len(paged) == 1, "page 2 must not repeat page 1"
  assert "https://gem.test/v1beta/models?pageToken=page-2" in PAGES

  cloudflare, _ = catalog.discover_provider("cloudflare", CONFIG["cloudflare"], fetch)
  assert "@cf/qwen/qwq-32b" in cloudflare, cloudflare
  assert "llama-guard-4-12b" not in cloudflare, cloudflare
  assert "@cf/zai-org/glm-5.3" not in cloudflare, cloudflare


def check_build_catalog() -> None:
  lines, payloads, skipped = catalog.build_catalog(CONFIG, make_fetch(PAYOUT))
  assert lines == [
    "cloudflare/@cf/meta/llama-3.1-8b-instruct",
    "cloudflare/@cf/qwen/qwq-32b",
    "cloudflare/deepseek-v4-pro-0813",
    "gemini/gemini-3.5-flash",
    "gemini/gemini-3.6-flash",
    "zai/glm-4.5",
    "zai/glm-4.5-air",
    "zai/glm-4.6",
    "zai/glm-4.7",
  ], lines
  assert sorted(payloads) == ["cloudflare", "gemini", "zai"], payloads
  assert "nokey: no api_key" in skipped, skipped
  assert any(item.startswith("broken: ") for item in skipped), skipped
  assert len(skipped) == 2, skipped


def check_write_models_txt() -> None:
  with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / "models.txt"
    catalog.write_models_txt(["groq/a", "gemini/b"], target)
    assert target.read_text(encoding="utf-8") == "groq/a\ngemini/b\n"


def check_collate() -> None:
  one = catalog.collate([{"data": [{"id": "a"}], "object": "list"}])
  assert one == {"data": [{"id": "a"}], "object": "list"}, one
  two = catalog.collate(
    [
      {"data": [{"id": "a"}], "object": "list", "nextPageToken": "p2"},
      {"data": [{"id": "b"}], "object": "list"},
    ]
  )
  assert two["data"] == [{"id": "a"}, {"id": "b"}], two
  assert two["object"] == "list", two
  assert "nextPageToken" not in two, two
  assert catalog.collate([]) == {}


def check_write_provider_yml() -> None:
  with tempfile.TemporaryDirectory() as directory:
    path = catalog.write_provider_yml(
      "groq",
      {"object": "list", "data": [{"id": "llama-3.3-70b"}]},
      Path(directory),
    )
    assert path.name == "groq.yml", path.name
    assert path.read_text(encoding="utf-8") == (
      "object: list\ndata:\n- id: llama-3.3-70b\n"
    ), path.read_text(encoding="utf-8")


def main() -> int:
  check_matches()
  check_extract_slugs()
  check_next_page_url()
  check_select()
  check_auth_headers()
  check_discover_provider()
  check_build_catalog()
  check_collate()
  check_write_models_txt()
  check_write_provider_yml()
  print(f"ok: catalog checks passed, {len(PAGES)} stub pages fetched")
  return 0


if __name__ == "__main__":
  sys.exit(main())
