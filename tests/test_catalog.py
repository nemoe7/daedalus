"""Runnable check for the catalog. Run: .venv/bin/python tests/test_catalog.py

Stub payloads copy the shapes in the provider dumps: `data[].id` for groq, kilo,
mistral, openrouter, and zai; `models[].name` with a `models/` head for gemini;
`result[].name` with a UUID `id` and a `result_info` row total for cloudflare.
"""

import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from daedalus import catalog

CONFIG: dict[str, Any] = {
  "cloudflare": {
    "api_key": "cf-token",
    "discovery_url": "https://cf.test/accounts/x/ai/models/search?per_page=100",
    "exclude": ["llama-guard*", "@cf/zai-org/glm-5.3", "*kimi-k2.6"],
    "tier": {"REASONING": ["qwen3*"]},
  },
  "gemini": {
    "api_key": "gem-token",
    "discovery_url": "https://gem.test/v1beta/models",
    "exclude": ["aqa", "*-latest", "gemini-2.5-flash*"],
    "tier": {"COMPLEX": ["gemini-3.5-flash"]},
  },
  "openrouter": {
    "api_key": "or-token",
    "discovery_url": "https://or.test/api/v1/models",
    "exclude": ["*content-safety*"],
    "tier": {"COMPLEX": ["*"]},
  },
  "zai": {
    "api_key": "zai-token",
    "discovery_url": "https://zai.test/v4/models",
    "exclude": ["*"],
    "tier": {"REASONING": ["glm-4.5"]},
  },
  "nokey": {"api_key": "", "discovery_url": "https://nokey.test/models"},
  "broken": {"api_key": "b-token", "discovery_url": "https://broken.test/models"},
  "denied": {"api_key": "d-token", "discovery_url": "https://denied.test/search"},
}

SEEN: dict[str, list[dict[str, str]]] = {}


def make_fetch(pages: dict[str, dict[str, Any]]):
  """Stub fetch: URL to page payload."""

  def fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
    SEEN.setdefault(url, []).append(headers)
    if url.startswith("https://broken.test"):
      raise httpx.ConnectError("unreachable", request=httpx.Request("GET", url))
    return pages[url]

  return fetch


PAYOUT: dict[str, dict[str, Any]] = {
  "https://cf.test/accounts/x/ai/models/search?per_page=100": {
    "success": True,
    "result": [
      {"id": "fe8904cf-e20e", "name": "@cf/qwen/qwq-32b"},
      {"id": "1a2b3c4d-0000", "name": "@cf/meta/llama-guard-3-8b"},
      {"id": "5e6f7a8b-1111", "name": "@cf/zai-org/glm-5.3"},
      {"id": "9f8e7d6c-2222", "name": "@cf/moonshotai/kimi-k2.6"},
      {"id": "0b1c2d3e-3333", "name": "@cf/deepseek-ai/deepseek-r1-distill-llama-8b"},
    ],
    "errors": [],
    "result_info": {"count": 5, "page": 1, "per_page": 100, "total_count": 105},
  },
  "https://cf.test/accounts/x/ai/models/search?per_page=100&page=2": {
    "success": True,
    "result": [{"id": "7d8e9f0a-4444", "name": "@cf/openai/gpt-oss-120b"}],
    "errors": [],
    "result_info": {"count": 1, "page": 2, "per_page": 100, "total_count": 105},
  },
  "https://gem.test/v1beta/models": {
    "models": [
      {"name": "models/gemini-3.5-flash", "version": "001"},
      {"name": "models/gemini-2.5-flash-lite"},
      {"name": "models/gemini-pro-latest"},
      {"name": "models/aqa"},
    ],
    "nextPageToken": "CiRtb2RlbHM=",
  },
  "https://gem.test/v1beta/models?pageToken=CiRtb2RlbHM=": {
    "models": [{"name": "models/gemini-3.6-flash"}],
  },
  "https://or.test/api/v1/models": {
    "data": [
      {"id": "google/gemma-4-26b-a4b-it:free", "canonical_slug": "google/gemma-4-26b"},
      {"id": "nvidia/nemotron-3.5-content-safety"},
      {"id": "openai/gpt-6-sol"},
    ],
    "total_count": 3,
    "links": {"next": None},
  },
  "https://zai.test/v4/models": {
    "object": "list",
    "data": [{"id": "glm-4.5"}, {"id": "glm-4.6"}, {"id": "glm-4.5-air"}],
  },
  "https://denied.test/search": {
    "success": False,
    "errors": [{"code": 7000, "message": "No route for that URI"}],
    "messages": [],
    "result": None,
  },
}


def check_matches() -> None:
  assert catalog.matches("glm-4.5", "glm-4.5")
  assert not catalog.matches("glm-4.5", "glm-4.5-air"), "exact must not prefix-match"
  assert catalog.matches("*kimi-k2.6", "@cf/moonshotai/kimi-k2.6"), "star glob"
  assert catalog.matches("*content-safety*", "nvidia/nemotron-3.5-content-safety")
  assert catalog.matches("gemini-3.?-flash", "gemini-3.5-flash"), "question mark glob"
  assert not catalog.matches("gemini-3.?-flash", "gemini-3.15-flash")
  assert catalog.matches("^glm-4\\.[67]$", "glm-4.6"), "regex head"
  assert not catalog.matches("^glm-4\\.[67]$", "glm-4.5"), "regex must anchor"
  assert not catalog.matches("llama-guard*", "@cf/meta/llama-guard-3-8b"), (
    "an anchored pattern does not reach past an org head"
  )


def check_extract_slugs() -> None:
  data = catalog.extract_slugs({"data": [{"id": "a"}, {"id": "b"}]})
  assert data == ["a", "b"], data
  gemini = catalog.extract_slugs({"models": [{"name": "models/gemini-3.5-flash"}]})
  assert gemini == ["gemini-3.5-flash"], gemini
  cloudflare = catalog.extract_slugs(
    {"result": [{"id": "fe8904cf-e20e", "name": "@cf/qwen/qwq-32b"}]}
  )
  assert cloudflare == ["@cf/qwen/qwq-32b"], cloudflare
  junk = catalog.extract_slugs({"data": ["x", {"no_id": 1}, {"id": ""}]})
  assert junk == [], junk
  assert catalog.extract_slugs({"result": None, "success": False}) == []


def check_next_page_url() -> None:
  token = catalog.next_page_url("https://gem.test/models", {"nextPageToken": "p2"})
  assert token == "https://gem.test/models?pageToken=p2", token
  links = catalog.next_page_url(
    "https://or.test/models",
    {"links": {"next": "https://or.test/models?page=2"}},
  )
  assert links == "https://or.test/models?page=2", links
  assert catalog.next_page_url("https://or.test/m", {"links": {"next": None}}) is None
  # Cloudflare names a row total, not a page total.
  rows = catalog.next_page_url(
    "https://cf.test/search?per_page=100",
    {"result_info": {"count": 100, "page": 1, "per_page": 100, "total_count": 313}},
  )
  assert rows == "https://cf.test/search?per_page=100&page=2", rows
  last = catalog.next_page_url(
    "https://cf.test/search?per_page=100&page=4",
    {"result_info": {"count": 13, "page": 4, "per_page": 100, "total_count": 313}},
  )
  assert last is None, last
  pages = catalog.next_page_url(
    "https://x.test/s",
    {"result_info": {"page": 1, "total_pages": 3}},
  )
  assert pages == "https://x.test/s?page=2", pages
  assert catalog.next_page_url("https://x.test/m", {"data": []}) is None


def check_failure() -> None:
  assert catalog.failure({"success": True, "result": []}) is None
  assert catalog.failure({"data": []}) is None
  denied = catalog.failure(
    {"success": False, "errors": [{"code": 7000, "message": "No route for that URI"}]}
  )
  assert denied == "No route for that URI", denied
  assert catalog.failure({"success": False}) == "the upstream reported a failure"


def check_select() -> None:
  provider = CONFIG["cloudflare"]
  slugs = [
    "@cf/qwen/qwq-32b",
    "@cf/meta/llama-guard-3-8b",
    "@cf/zai-org/glm-5.3",
    "@cf/moonshotai/kimi-k2.6",
  ]
  kept = catalog.select("cloudflare", provider, slugs)
  assert kept == ["@cf/meta/llama-guard-3-8b", "@cf/qwen/qwq-32b"], kept

  # An excluded slug stays excluded: a tier pattern does not claim it back.
  assert catalog.select("zai", CONFIG["zai"], ["glm-4.5", "glm-4.6"]) == []

  # The block key supplies the provider, so a `provider/` head on a pattern is dropped.
  headed = {"exclude": ["openrouter/google/gemma-4-26b-a4b-it:free"]}
  assert catalog.select("openrouter", headed, ["google/gemma-4-26b-a4b-it:free"]) == []
  assert catalog.strip_provider_head("openrouter/google/x", "openrouter") == "google/x"
  assert catalog.strip_provider_head("*google/x", "openrouter") == "*google/x"


def check_auth_headers() -> None:
  assert catalog.auth_headers("groq", {"api_key": "g"}) == {"Authorization": "Bearer g"}
  assert catalog.auth_headers("gemini", {"api_key": "m"}) == {"x-goog-api-key": "m"}
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
  assert SEEN["https://gem.test/v1beta/models"][0] == {"x-goog-api-key": "gem-token"}

  # Cloudflare pages on its row total, and takes `name`, not the UUID `id`.
  cloudflare, cf_full = catalog.discover_provider(
    "cloudflare",
    CONFIG["cloudflare"],
    fetch,
  )
  assert "@cf/openai/gpt-oss-120b" in cloudflare, cloudflare
  assert "@cf/qwen/qwq-32b" in cloudflare, cloudflare
  assert "@cf/zai-org/glm-5.3" not in cloudflare, cloudflare
  assert "@cf/moonshotai/kimi-k2.6" not in cloudflare, cloudflare
  assert "@cf/meta/llama-guard-3-8b" in cloudflare, (
    "the anchored pattern cannot drop it"
  )
  assert len(cf_full["result"]) == 6, cf_full["result"]
  assert "https://cf.test/accounts/x/ai/models/search?per_page=100&page=2" in SEEN

  # A single page, and no row survives `exclude: ["*"]`.
  openrouter, _ = catalog.discover_provider("openrouter", CONFIG["openrouter"], fetch)
  assert openrouter == ["google/gemma-4-26b-a4b-it:free", "openai/gpt-6-sol"], (
    openrouter
  )
  assert catalog.discover_provider("zai", CONFIG["zai"], fetch)[0] == []

  try:
    catalog.discover_provider("denied", CONFIG["denied"], fetch)
  except ValueError as error:
    assert str(error) == "No route for that URI", error
  else:
    raise AssertionError("a failed envelope must raise")


def check_build_catalog() -> None:
  lines, payloads, skipped = catalog.build_catalog(CONFIG, make_fetch(PAYOUT))
  assert lines == [
    "cloudflare/@cf/deepseek-ai/deepseek-r1-distill-llama-8b",
    "cloudflare/@cf/meta/llama-guard-3-8b",
    "cloudflare/@cf/openai/gpt-oss-120b",
    "cloudflare/@cf/qwen/qwq-32b",
    "gemini/gemini-3.5-flash",
    "gemini/gemini-3.6-flash",
    "openrouter/google/gemma-4-26b-a4b-it:free",
    "openrouter/openai/gpt-6-sol",
  ], lines
  assert sorted(payloads) == ["cloudflare", "gemini", "openrouter", "zai"], payloads
  assert payloads["zai"]["data"] == [
    {"id": "glm-4.5"},
    {"id": "glm-4.6"},
    {"id": "glm-4.5-air"},
  ], payloads["zai"]
  assert "nokey: no api_key" in skipped, skipped
  assert "denied: No route for that URI" in skipped, skipped
  assert any(item.startswith("broken: ") for item in skipped), skipped
  assert len(skipped) == 3, skipped


def check_merge_pages() -> None:
  one = catalog.merge_pages([{"data": [{"id": "a"}], "object": "list"}])
  assert one == {"data": [{"id": "a"}], "object": "list"}, one
  two = catalog.merge_pages(
    [
      {"data": [{"id": "a"}], "object": "list", "nextPageToken": "p2"},
      {"data": [{"id": "b"}], "object": "list"},
    ]
  )
  assert two["data"] == [{"id": "a"}, {"id": "b"}], two
  assert two["object"] == "list", two
  assert "nextPageToken" not in two, two
  assert catalog.merge_pages([]) == {}


def check_writers() -> None:
  with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / "models.txt"
    catalog.write_models_txt(["groq/a", "gemini/b"], target)
    assert target.read_text(encoding="utf-8") == "groq/a\ngemini/b\n"

    path = catalog.write_provider_yml(
      "groq",
      {"object": "list", "data": [{"id": "llama-3.3-70b"}]},
      Path(directory),
    )
    assert path.name == "groq.yml", path.name
    assert path.read_text(encoding="utf-8") == (
      "object: list\ndata:\n- id: llama-3.3-70b\n"
    ), path.read_text(encoding="utf-8")
  assert str(catalog.PROVIDER_DIR) == "config/providers", catalog.PROVIDER_DIR


def main() -> int:
  check_matches()
  check_extract_slugs()
  check_next_page_url()
  check_failure()
  check_select()
  check_auth_headers()
  check_discover_provider()
  check_build_catalog()
  check_merge_pages()
  check_writers()
  print(f"ok: catalog checks passed, {len(SEEN)} stub pages fetched")
  return 0


if __name__ == "__main__":
  sys.exit(main())
