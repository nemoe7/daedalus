"""Tests for the model catalog."""

import json
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from daedalus import catalog, config, store
from daedalus.catalog import discovery, enrichment

TEXT = {"name": "Text Generation"}
CONFIG: dict[str, Any] = {
  "cloudflare": {
    "api_key": "cf-token",
    "discovery_url": "https://cf.test/accounts/x/ai/models/search?per_page=100",
    "exclude": ["llama-guard*", "@cf/zai-org/glm-5.3", "*kimi-k2.6"],
    "tier": {"TIER-A": ["qwen3*"]},
  },
  "gemini": {
    "api_key": "gem-token",
    "discovery_url": "https://gem.test/v1beta/models",
    "exclude": ["aqa", "*-latest", "gemini-2.5-flash*"],
    "tier": {"TIER-B": ["gemini-3.5-flash"]},
  },
  "openrouter": {
    "api_key": "or-token",
    "discovery_url": "https://or.test/api/v1/models",
    "exclude": ["free", "*content-safety*", "openrouter/*", "!*:free"],
    "tier": {"TIER-B": ["*"]},
  },
  "z-ai": {
    "api_key": "zai-token",
    "discovery_url": "https://z-ai.test/v4/models",
    "exclude": ["*"],
    "tier": {"TIER-A": ["glm-4.5"]},
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
    if urlsplit(url).hostname == "broken.test":
      raise httpx.ConnectError("unreachable", request=httpx.Request("GET", url))
    return pages[url]

  return fetch


PAYOUT: dict[str, dict[str, Any]] = {
  "https://cf.test/accounts/x/ai/models/search?per_page=100": {
    "success": True,
    "result": [
      {"id": "fe8904cf-e20e", "name": "@cf/qwen/qwq-32b", "task": TEXT},
      {"id": "1a2b3c4d-0000", "name": "@cf/meta/llama-guard-3-8b", "task": TEXT},
      {"id": "5e6f7a8b-1111", "name": "@cf/zai-org/glm-5.3", "task": TEXT},
      {"id": "9f8e7d6c-2222", "name": "@cf/moonshotai/kimi-k2.6", "task": TEXT},
      {
        "id": "0b1c2d3e-3333",
        "name": "@cf/deepseek-ai/deepseek-r1-distill-llama-8b",
        "task": TEXT,
      },
    ],
    "errors": [],
    "result_info": {"count": 5, "page": 1, "per_page": 100, "total_count": 105},
  },
  "https://cf.test/accounts/x/ai/models/search?per_page=100&page=2": {
    "success": True,
    "result": [
      {"id": "7d8e9f0a-4444", "name": "@cf/openai/gpt-oss-120b", "task": TEXT}
    ],
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
  "https://z-ai.test/v4/models": {
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


def test_matches() -> None:
  assert discovery.matches("glm-4.5", "glm-4.5")
  assert not discovery.matches("glm-4.5", "glm-4.5-air"), "exact must not prefix-match"
  assert discovery.matches("*kimi-k2.6", "@cf/moonshotai/kimi-k2.6"), "star glob"
  assert discovery.matches("*content-safety*", "nvidia/nemotron-3.5-content-safety")
  assert discovery.matches("gemini-3.?-flash", "gemini-3.5-flash"), "question mark glob"
  assert not discovery.matches("gemini-3.?-flash", "gemini-3.15-flash")
  assert discovery.matches("^glm-4\\.[67]$", "glm-4.6"), "regex head"
  assert not discovery.matches("^glm-4\\.[67]$", "glm-4.5"), "regex must anchor"
  assert not discovery.matches("llama-guard*", "@cf/meta/llama-guard-3-8b"), (
    "an anchored pattern does not reach past an org head"
  )
  assert discovery.matches("!*:free", "openai/gpt-6-sol"), "negation drops a paid row"
  assert not discovery.matches("!*:free", "google/gemma-4-31b-it:free")
  assert discovery.matches("!^glm-4\\.[67]$", "glm-4.5"), "negation inverts regex too"
  assert not discovery.matches("!^glm-4\\.[67]$", "glm-4.6")
  assert discovery.matches("^.*$", "a" * discovery.SLUG_LIMIT), "a slug at the limit"
  assert not discovery.matches("^.*$", "a" * (discovery.SLUG_LIMIT + 1)), (
    "a slug above it"
  )


def test_nested_repeat_pattern_matches_nothing(
  caplog: pytest.LogCaptureFixture,
) -> None:
  """A repeat inside a repeat can hang the catalog on a long slug: it is refused."""
  # A config file supplies this shape, so the test reads it as data. A literal
  # here trips the ReDoS query on this file.
  nested = json.loads('"^(a+)+$"')
  slug = "a" * 40 + "b"
  with caplog.at_level("WARNING", logger="daedalus.catalog"):
    assert discovery.matches(nested, slug) is False, "the shape matches nothing"
    assert discovery.matches(nested, slug) is False
  told = [
    r.getMessage() for r in caplog.records if "repeats a repeat" in r.getMessage()
  ]
  assert len(told) == 1, told
  assert discovery.matches("^a+b$", slug), "a safe pattern still works"


def row_slugs(payload: dict, match: dict | None = None) -> list[str]:
  return list(discovery.extract_rows(payload, match))


def discover(name: str, fetch) -> tuple[list[str], list[str]]:
  """The kept slugs and skip reasons of 1 provider, through the catalog build."""
  lines, skipped = discovery.build_rows({name: CONFIG[name]}, fetch)
  return [line.split("/", 1)[1] for line in lines], skipped


def test_extract_row_slugs() -> None:
  data = row_slugs({"data": [{"id": "a"}, {"id": "b"}]})
  assert data == ["a", "b"], data
  gemini = row_slugs({"models": [{"name": "models/gemini-3.5-flash"}]})
  assert gemini == ["gemini-3.5-flash"], gemini
  cloudflare = row_slugs(
    {"result": [{"id": "fe8904cf-e20e", "name": "@cf/qwen/qwq-32b"}]}
  )
  assert cloudflare == ["@cf/qwen/qwq-32b"], cloudflare
  junk = row_slugs({"data": ["x", {"no_id": 1}, {"id": ""}]})
  assert junk == [], junk
  assert row_slugs({"result": None, "success": False}) == []


def test_next_page_url() -> None:
  token = discovery.next_page_url("https://gem.test/models", {"nextPageToken": "p2"})
  assert token == "https://gem.test/models?pageToken=p2", token
  links = discovery.next_page_url(
    "https://or.test/models",
    {"links": {"next": "https://or.test/models?page=2"}},
  )
  assert links == "https://or.test/models?page=2", links
  assert discovery.next_page_url("https://or.test/m", {"links": {"next": None}}) is None
  # Cloudflare names a row total, not a page total.
  rows = discovery.next_page_url(
    "https://cf.test/search?per_page=100",
    {"result_info": {"count": 100, "page": 1, "per_page": 100, "total_count": 313}},
  )
  assert rows == "https://cf.test/search?per_page=100&page=2", rows
  spaced = discovery.with_param("https://cf.test/s?task=Text%20Generation", "page", 2)
  assert spaced == "https://cf.test/s?task=Text%20Generation&page=2", spaced
  last = discovery.next_page_url(
    "https://cf.test/search?per_page=100&page=4",
    {"result_info": {"count": 13, "page": 4, "per_page": 100, "total_count": 313}},
  )
  assert last is None, last
  pages = discovery.next_page_url(
    "https://x.test/s",
    {"result_info": {"page": 1, "total_pages": 3}},
  )
  assert pages == "https://x.test/s?page=2", pages
  assert discovery.next_page_url("https://x.test/m", {"data": []}) is None


def test_failure() -> None:
  assert discovery.failure({"success": True, "result": []}) is None
  assert discovery.failure({"data": []}) is None
  denied = discovery.failure(
    {"success": False, "errors": [{"code": 7000, "message": "No route for that URI"}]}
  )
  assert denied == "No route for that URI", denied
  assert discovery.failure({"success": False}) == "the upstream reported a failure"


def test_fetch_json_retry(monkeypatch: pytest.MonkeyPatch) -> None:
  """The fetch_json helper retries on a 500 status and succeeds."""
  calls = 0

  def mock_get(url: str, **kwargs: Any) -> httpx.Response:
    nonlocal calls
    calls += 1
    request = httpx.Request("GET", url)
    if calls == 1:
      return httpx.Response(500, request=request)
    return httpx.Response(200, json={"ok": True}, request=request)

  monkeypatch.setattr(httpx, "get", mock_get)
  monkeypatch.setattr(time, "sleep", lambda _: None)
  data = discovery.fetch_json("https://api.test/models", {})
  assert data == {"ok": True}
  assert calls == 2


def test_select() -> None:
  provider = CONFIG["cloudflare"]
  slugs = [
    "@cf/qwen/qwq-32b",
    "@cf/meta/llama-guard-3-8b",
    "@cf/zai-org/glm-5.3",
    "@cf/moonshotai/kimi-k2.6",
  ]
  kept = discovery.select(provider, slugs)
  assert kept == ["@cf/meta/llama-guard-3-8b", "@cf/qwen/qwq-32b"], kept

  # An excluded slug stays excluded: a tier pattern does not claim it back.
  assert discovery.select(CONFIG["z-ai"], ["glm-4.5", "glm-4.6"]) == []

  # A pattern that names its own provider is a slug pattern, not a head to strip:
  # `openrouter/*` reaches `openrouter/auto`, and only that.
  routers = discovery.select(
    {"exclude": ["openrouter/*"]},
    ["openrouter/auto", "openai/gpt-6-sol", "qwen/qwen3.8-27b:free"],
  )
  assert routers == ["openai/gpt-6-sol", "qwen/qwen3.8-27b:free"], routers


def test_auth_headers() -> None:
  assert discovery.auth_headers("groq", {"api_key": "g"}) == {
    "Authorization": "Bearer g"
  }
  assert discovery.auth_headers("gemini", {"api_key": "m"}) == {"x-goog-api-key": "m"}
  assert discovery.auth_headers("groq", {"api_key": ""}) == {}


def test_discover() -> None:
  fetch = make_fetch(PAYOUT)
  gemini, _ = discover("gemini", fetch)
  assert gemini == ["gemini-3.5-flash", "gemini-3.6-flash"], gemini
  assert SEEN["https://gem.test/v1beta/models"][0] == {"x-goog-api-key": "gem-token"}

  # Cloudflare pages on its row total, and takes `name`, not the UUID `id`.
  cloudflare, _ = discover("cloudflare", fetch)
  assert "@cf/openai/gpt-oss-120b" in cloudflare, cloudflare
  assert "@cf/qwen/qwq-32b" in cloudflare, cloudflare
  assert "@cf/zai-org/glm-5.3" not in cloudflare, cloudflare
  assert "@cf/moonshotai/kimi-k2.6" not in cloudflare, cloudflare
  assert "@cf/meta/llama-guard-3-8b" in cloudflare, (
    "the anchored pattern cannot drop it"
  )
  assert "https://cf.test/accounts/x/ai/models/search?per_page=100&page=2" in SEEN

  # A single page, and no row survives `exclude: ["*"]`.
  openrouter, _ = discover("openrouter", fetch)
  assert openrouter == ["google/gemma-4-26b-a4b-it:free"], openrouter
  assert discover("z-ai", fetch) == ([], []), "no row survives the star exclude"
  denied = discover("denied", fetch)
  assert denied == ([], ["denied: No route for that URI"]), denied


def test_build_rows() -> None:
  lines, skipped = discovery.build_rows(CONFIG, make_fetch(PAYOUT))
  assert list(lines) == [
    "cloudflare/@cf/deepseek-ai/deepseek-r1-distill-llama-8b",
    "cloudflare/@cf/meta/llama-guard-3-8b",
    "cloudflare/@cf/openai/gpt-oss-120b",
    "cloudflare/@cf/qwen/qwq-32b",
    "gemini/gemini-3.5-flash",
    "gemini/gemini-3.6-flash",
    "openrouter/google/gemma-4-26b-a4b-it:free",
  ], lines
  assert "nokey: no api_key" in skipped, skipped
  assert "denied: No route for that URI" in skipped, skipped
  assert any(item.startswith("broken: ") for item in skipped), skipped
  assert len(skipped) == 3, skipped


def test_the_provider_reads_run_at_the_same_time(monkeypatch) -> None:
  """The provider downloads overlap, so the rebuild waits for the slowest alone."""
  monkeypatch.setattr(discovery, "CORES", 2)
  both = threading.Barrier(2, timeout=5)

  def fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
    both.wait()
    return PAYOUT[url]

  providers = {"openrouter": CONFIG["openrouter"], "z-ai": CONFIG["z-ai"]}
  lines, skipped = discovery.build_rows(providers, fetch)
  assert skipped == [], skipped
  assert "openrouter/google/gemma-4-26b-a4b-it:free" in lines


def test_a_line_per_provider_read(caplog) -> None:
  """Each provider read logs its model count, so a rebuild shows its progress."""
  with caplog.at_level("INFO", logger="daedalus.catalog"):
    discovery.build_rows(
      {"openrouter": CONFIG["openrouter"], "gemini": CONFIG["gemini"]},
      make_fetch(PAYOUT),
    )
  told = [record.getMessage() for record in caplog.records]
  assert "openrouter: read 3 models" in told, told
  assert "gemini: read 5 models" in told, told


def test_the_read_cap_holds(monkeypatch) -> None:
  """With 1 core the reads run one after the other."""
  monkeypatch.setattr(discovery, "CORES", 1)
  live = 0
  peak = 0
  lock = threading.Lock()

  def read(key: str) -> str:
    nonlocal live, peak
    with lock:
      live += 1
      peak = max(peak, live)
    time.sleep(0.05)
    with lock:
      live -= 1
    return key.upper()

  assert discovery.read_each(list("abcd"), read) == {
    "a": "A",
    "b": "B",
    "c": "C",
    "d": "D",
  }
  assert peak == 1, peak


def test_provider_file_override() -> None:
  """A file takes only its models, with values that do not mix with the main block."""
  main = {
    "api_key": "old",
    "discovery_url": "https://or.test/api/v1/models",
    "exclude": ["!*:free"],
    "models": {"*gemma*:free": {"rpm": 1}},
  }
  file = {
    "api_key": "new",
    "discovery_url": "https://or.test/api/v1/models",
    "tier": {"TIER-A": ["openai/gpt-6-sol"]},
    "models": {"openai/gpt-6-sol": {"rpm": 100, "pool": False}},
  }
  config.set_config({"openrouter": {**main, config.FILE_KEY: file}})
  try:
    blocks = config.get_config()
    lines, skipped = discovery.build_rows(
      blocks, make_fetch(PAYOUT), save_snapshots=True
    )
    assert not skipped, skipped
    assert list(lines) == [
      "openrouter/google/gemma-4-26b-a4b-it:free",
      "openrouter/openai/gpt-6-sol",
    ], lines
    rows, problems = enrichment.enrich(
      lines, blocks, fetch=lambda *_: {"data": []}, native=lines
    )
    assert not problems, problems
    info = {row["id"]: row for row in rows}
    assert info["openrouter/openai/gpt-6-sol"]["rpm"] == 100
    assert info["openrouter/google/gemma-4-26b-a4b-it:free"]["rpm"] == 1
    found, _ = discovery.read_providers(blocks, make_fetch(PAYOUT))
    assert [is_file for _, _, _, is_file in found] == [False, True], found
    with tempfile.TemporaryDirectory() as folder:
      paths = discovery.dump(blocks, folder=folder)
      assert [p.name for p in paths] == ["openrouter.json"], "1 file for each provider"
  finally:
    config.set_config(None)


def test_shared_download() -> None:
  """A file with the same URL as its main block reads the list 1 time, also with another key."""
  block = {"api_key": "main", "discovery_url": "https://or.test/api/v1/models"}
  file = {**block, "api_key": "file", "models": {"openai/gpt-6-sol": {"pool": False}}}
  config.set_config({"openrouter": {**block, config.FILE_KEY: file}})
  calls: list[str] = []

  def fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
    calls.append(url)
    return make_fetch(PAYOUT)(url, headers)

  try:
    blocks = config.get_config()
    found, skipped = discovery.read_providers(blocks, fetch, save_snapshots=True)
    assert not skipped, skipped
    assert [is_file for _, _, _, is_file in found] == [False, True], found
    assert len(calls) == 1, calls
    with tempfile.TemporaryDirectory() as folder:
      paths = discovery.dump(blocks, folder=folder)
      assert [p.name for p in paths] == ["openrouter.json"], paths

    def broken(url: str, headers: dict[str, str]) -> dict[str, Any]:
      calls.append(url)
      raise httpx.ConnectError("down")

    calls.clear()
    failed: list[str] = []
    found, skipped = discovery.read_providers(blocks, broken, failed)
    assert (found, failed, len(calls)) == ([], ["openrouter"], 1), (skipped, calls)
  finally:
    config.set_config(None)


def test_shared_litellm() -> None:
  """Kilo and OpenRouter read the same LiteLLM catalog and modelschemas provider 1 time."""
  calls: list[str] = []

  def fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
    calls.append(url)
    return {"data": [{"id": "openrouter/a/b", "max_input_tokens": 9}]}

  lines = ["kilo/a/b", "openrouter/a/b"]
  rows, problems = enrichment.enrich(lines, {}, fetch)
  assert (len(calls), problems) == (2, []), calls
  litellm_page = discovery.with_param(
    discovery.with_param(
      discovery.with_param(enrichment.LITELLM_CATALOG, "provider", "openrouter"),
      "page_size",
      enrichment.LITELLM_PAGE_SIZE,
    ),
    "page",
    1,
  )
  assert calls[0] == litellm_page
  assert calls[1] == discovery.with_param(
    enrichment.MODELSCHEMAS_URL, "provider", "openrouter"
  )
  assert [row["max_input_tokens"] for row in rows] == [9, 9], rows


def test_discovery_match() -> None:
  payload = {
    "result": [
      {"name": "@cf/a/free"},
      {"name": "@cf/a/paid", "properties": [{"property_id": "paid", "value": "true"}]},
      {"name": "@cf/a/open", "properties": [{"property_id": "paid", "value": "false"}]},
      {"name": "@cf/a/top", "paid": True},
    ]
  }
  match = {"paid": False}
  slugs = row_slugs(payload, match)
  assert slugs == ["@cf/a/free", "@cf/a/open"], slugs
  assert len(row_slugs(payload)) == 4, "no match keeps every row"
  provider = {
    "api_key": "k",
    "discovery_url": "https://m.test/s",
    "discovery_match": match,
  }
  lines, _ = discovery.build_rows({"m": provider}, lambda *_: payload)
  assert list(lines) == ["m/@cf/a/free", "m/@cf/a/open"], lines


def test_dump() -> None:
  discovery.read_providers(CONFIG, make_fetch(PAYOUT), save_snapshots=True)
  with tempfile.TemporaryDirectory() as folder:
    stale = Path(folder) / "broken.json"
    stale.write_text("{}", encoding="utf-8")
    paths = discovery.dump(CONFIG, folder=folder)
    names = sorted(path.name for path in paths)
    assert names == [
      "cloudflare.json",
      "gemini.json",
      "openrouter.json",
      "z-ai.json",
    ], names
    assert not stale.exists(), "a failed provider keeps no old dump"
    cloudflare = json.loads((Path(folder) / "cloudflare.json").read_text("utf-8"))
    slugs = row_slugs(cloudflare)
    assert "@cf/zai-org/glm-5.3" in slugs, "the dump keeps excluded rows"
    assert "@cf/openai/gpt-oss-120b" in slugs, "the dump merges every page"
    csv_paths = discovery.dump(CONFIG, folder=folder, file_format="csv")
    assert sorted(path.name for path in csv_paths) == [
      "cloudflare.csv",
      "gemini.csv",
      "openrouter.csv",
      "z-ai.csv",
    ]
    assert not list(Path(folder).glob("*.json")), "the old catalog format goes"
    cloudflare_csv = (Path(folder) / "cloudflare.csv").read_text("utf-8")
    assert cloudflare_csv.startswith("id,") and "@cf/zai-org/glm-5.3" in cloudflare_csv


def test_cached_snapshots() -> None:
  """Cached rebuilds need no network and a changed URL preserves rows until refresh."""
  url = "https://cache.test/v1/models"
  config = {"cache": {"api_key": "k", "discovery_url": url}}
  discovery.save_snapshot(url, {"data": [{"id": "alpha"}]})

  def forbidden(*_: Any) -> dict[str, Any]:
    raise AssertionError("cached discovery must not fetch")

  found, skipped = discovery.read_providers(config, forbidden, cached=True)
  assert not skipped and found[0][2] == {"data": [{"id": "alpha"}]}, (found, skipped)
  lines, skipped = discovery.build_rows(config, forbidden, cached=True)
  assert not skipped and list(lines) == ["cache/alpha"], (lines, skipped)
  with tempfile.TemporaryDirectory() as folder:
    paths = discovery.dump(
      {"cache": {"api_key": "", "discovery_url": url}}, folder=folder
    )
    assert [path.name for path in paths] == ["cache.json"], paths

  changed = {"cache": {"api_key": "k", "discovery_url": url + "?v=2"}}
  failed: list[str] = []
  found, skipped = discovery.read_providers(changed, forbidden, failed, cached=True)
  assert not found and failed == ["cache"]
  assert skipped == ["cache: no cached catalog snapshot"], skipped


def test_rebuild_cached_updates_models(monkeypatch) -> None:
  """A cached rebuild reapplies provider selection to the persisted snapshot."""
  url = "https://rebuild-cache.test/models"
  provider = {"api_key": "k", "discovery_url": url}
  config.set_config({"cache": provider})
  discovery.save_snapshot(url, {"data": [{"id": "alpha"}, {"id": "beta"}]})

  def enrich_rows(lines, config, native=None, failed=None):
    return ([{"id": line, **(native or {}).get(line, {})} for line in lines], [])

  monkeypatch.setattr(enrichment, "enrich", enrich_rows)
  try:
    catalog.rebuild_cached()
    assert store.read_models() == ["cache/alpha", "cache/beta"]
    config.set_config({"cache": {**provider, "exclude": ["alpha"]}})
    catalog.rebuild_cached()
    assert store.read_models() == ["cache/beta"]
  finally:
    config.set_config(None)


def test_merge_pages() -> None:
  one = discovery.merge_pages([{"data": [{"id": "a"}], "object": "list"}])
  assert one == {"data": [{"id": "a"}], "object": "list"}, one
  two = discovery.merge_pages(
    [
      {"data": [{"id": "a"}], "object": "list", "nextPageToken": "p2"},
      {"data": [{"id": "b"}], "object": "list"},
    ]
  )
  assert two["data"] == [{"id": "a"}, {"id": "b"}], two
  assert two["object"] == "list", two
  assert "nextPageToken" not in two, two
  assert discovery.merge_pages([]) == {}


def test_declared_kept() -> None:
  """A slug a `models:` key names is kept even when `exclude` drops it."""
  provider = {"exclude": ["*"], "models": {"glm-4.7-flash": {"tpm": 8000}}}
  slugs = ["glm-4.5", "glm-4.6", "glm-4.7-flash"]
  assert discovery.select(provider, slugs) == ["glm-4.7-flash"]

  globbed = {
    "exclude": ["openai/*"],
    "models": {"*gpt-oss-120b": {"max_input_tokens": 7000}},
  }
  assert discovery.select(globbed, ["openai/gpt-oss-120b", "openai/gpt-6-sol"]) == [
    "openai/gpt-oss-120b"
  ]

  # An exact `models:` key is written even when discovery does not return it.
  absent = {"exclude": ["*"], "models": {"glm-4.5-flash": {"rpm": 60}}}
  assert discovery.select(absent, ["glm-4.5", "glm-5"]) == ["glm-4.5-flash"]

  # A pattern key is a matcher, not an id, so it never becomes a slug of its own.
  pattern_only = {"exclude": ["*"], "models": {"*gemma-4-31b-it:free": {"rpm": 5}}}
  assert discovery.select(pattern_only, ["google/gemma-4-31b-it:free"]) == [
    "google/gemma-4-31b-it:free"
  ]
  assert discovery.select(pattern_only, []) == []


def test_free_stealth() -> None:
  """A stealth row passes the kilo and openrouter excludes only with price 0 in each field."""
  free = {"prompt": "0", "completion": "0"}
  payload = {
    "data": [
      {"id": "google/gemma-4-31b-it:free", "pricing": free},
      {"id": "stealth/space-bunny-alpha", "pricing": {**free, "discount": 0}},
      {
        "id": "stealth/claude-opus-4.8",
        "pricing": {"prompt": "0.000004", "completion": "0"},
      },
      {"id": "stealth/router", "pricing": {"prompt": "-1", "completion": "-1"}},
      {"id": "stealth/unknown"},
      {"id": "google/lyria-3-pro-preview", "pricing": free},
    ]
  }
  providers = config.load_config()
  for name in ("openrouter", "kilo"):
    block = {
      key: value for key, value in providers[name].items() if key != config.FILE_KEY
    }
    block.update(api_key="k", discovery_url="https://gateway.test/models")
    lines, skipped = discovery.build_rows({name: block}, lambda *_: payload)
    assert not skipped, skipped
    # The catalog also keeps each model id that the block names exactly.
    declared = [f"{name}/{slug}" for slug in discovery.declared_ids(block)]
    assert sorted(lines) == sorted(
      [
        f"{name}/google/gemma-4-31b-it:free",
        f"{name}/stealth/space-bunny-alpha",
        *declared,
      ]
    ), lines
  groq = {
    "api_key": "k",
    "discovery_url": "https://groq.test/models",
    "exclude": ["stealth/*"],
  }
  lines, _ = discovery.build_rows({"groq": groq}, lambda *_: payload)
  assert "groq/stealth/space-bunny-alpha" not in lines, lines


def test_free_only() -> None:
  """The committed config keeps only :free rows for kilo and openrouter."""
  providers = config.load_config()
  rows = [
    "openai/gpt-6-sol",
    "google/gemma-4-31b-it:free",
    "anthropic/claude-opus-5.5",
    "nvidia/nemotron-3.5-content-safety",
  ]
  openrouter = discovery.select(
    providers["openrouter"],
    rows
    + [
      "openrouter/auto",
      "openrouter/free",
      "openrouter/pareto-code",
    ],
  )
  declared = discovery.declared_ids(providers["openrouter"])
  assert openrouter == sorted(["google/gemma-4-31b-it:free", *declared]), openrouter

  kilo = discovery.select(
    providers["kilo"],
    rows
    + [
      "kilo-auto/free",
      "kilo-auto/efficient",
      "poolside/laguna-s-2.1:free",
    ],
  )
  assert kilo == ["google/gemma-4-31b-it:free", "poolside/laguna-s-2.1:free"], kilo


def test_non_text() -> None:
  """The committed config drops Gemini image, live and 2.5 rows, and sets other modes."""
  providers = config.load_config()
  gemini = [
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-3.1-flash-lite-image",
    "gemini-3.1-flash-live-preview",
    "gemini-3.5-transcribe-live",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
  ]
  kept = discovery.select(providers["gemini"], gemini)
  assert "gemini-3.1-flash-lite" in kept, kept
  assert not set(gemini[:-1]) & set(kept), kept
  modes = {
    ("mistral", "codestral-embed-2505"): "embedding",
    ("mistral", "voxtral-mini-latest"): "audio_transcription",
    ("groq", "whisper-large-v3"): "audio_transcription",
    ("groq", "canopylabs/orpheus-v1-english"): "audio_speech",
    ("gemini", "gemini-3.8-flash-tts"): "audio_speech",
    ("gemini", "gemini-3.1-flash-tts-preview"): "audio_speech",
    ("gemini", "gemini-3.5-transcribe"): "audio_transcription",
    ("mistral", "codestral-2508"): None,
    ("cloudflare", "@cf/cloudflare/clef"): "decisions",
    ("cloudflare", "@cf/cloudflare/clef-flash"): "decisions",
  }
  for (name, slug), mode in modes.items():
    assert slug in discovery.select(providers[name], [slug]), slug
    found = enrichment.config_params(providers[name], slug).get("mode")
    assert found == mode, (slug, found, "a mode keeps the row out of the chat chains")


def test_failed_providers() -> None:
  """Only a model list fetch that fails puts the provider on the failed list."""
  providers = {
    "up": {"api_key": "k", "discovery_url": "https://up.test/models"},
    "down": {"api_key": "k", "discovery_url": "https://down.test/models"},
    "nokey": {"discovery_url": "https://nokey.test/models"},
    "nourl": {"api_key": "k"},
  }

  def fetch(url: str, headers: dict[str, str]) -> dict[str, Any]:
    if "down" in url:
      raise httpx.ConnectError("refused")
    return {"data": [{"id": "m"}]}

  failed: list[str] = []
  lines, skipped = discovery.build_rows(providers, fetch, failed)
  assert list(lines) == ["up/m"], lines
  assert failed == ["down"], failed
  assert len(skipped) == 3, skipped
