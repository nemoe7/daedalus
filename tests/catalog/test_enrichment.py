"""Catalog enrichment: the LiteLLM pages, the config columns and the overrides."""

import httpx

from daedalus.catalog import enrichment
from daedalus.catalog.discovery import MAX_PAGES, Fetch, with_param

PAGE = {"data": [{"id": "openrouter/a", "rpm": 9}], "has_more": False}

MS_PAGE = {
  "count": 6,
  "models": [
    {
      "id": "r-a",
      "rawId": "a",
      "capabilities": {"reasoning": True},
      "reasoning": {
        "mode": "effort",
        "mandatory": False,
        "efforts": ["high", "low", "none", "auto"],
      },
    },
    {
      "id": "r-b",
      "rawId": "b",
      "capabilities": {},
      "reasoning": {"mode": "toggle", "mandatory": False},
    },
    {
      "id": "r-c",
      "rawId": "c",
      "capabilities": {"reasoning": True},
      "reasoning": {"mode": "effort", "mandatory": True, "efforts": ["none", "low"]},
    },
    {
      "id": "r-d",
      "rawId": "d",
      "capabilities": {"reasoning": False},
      "reasoning": None,
    },
    {
      "id": "r-e",
      "rawId": "e",
      "capabilities": {},
      "reasoning": {"mode": "toggle", "mandatory": True},
    },
    {
      "id": "r-f",
      "rawId": "f",
      "capabilities": {},
      "reasoning": {"mode": "adaptive", "mandatory": False},
    },
    {"id": "no-raw", "reasoning": None},
  ],
}


def sequenced(*payloads: dict) -> tuple[Fetch, list[str]]:
  """A fetch that hands out one payload per call and records every URL it sees."""

  queue = list(payloads)
  seen: list[str] = []

  def fetch(url: str, headers: dict[str, str]) -> dict:
    seen.append(url)
    return queue.pop(0) if queue else {}

  return fetch, seen


def broken(url: str, headers: dict[str, str]) -> dict:
  raise httpx.HTTPError("boom")


def test_litellm_entries_keys_the_rows_by_slug() -> None:
  """The provider prefix comes off the id, and the slug is the key."""
  fetch, _ = sequenced(PAGE)
  assert enrichment.litellm_entries("openrouter", fetch) == {
    "a": {"id": "openrouter/a", "rpm": 9}
  }


def test_litellm_entries_reads_until_a_page_says_no_more() -> None:
  """`has_more` true means one more read."""
  fetch, seen = sequenced(
    {"data": [{"id": "openrouter/a"}], "has_more": True},
    {"data": [{"id": "openrouter/b"}], "has_more": False},
  )
  assert set(enrichment.litellm_entries("openrouter", fetch)) == {"a", "b"}
  assert len(seen) == 2


def test_litellm_entries_stops_at_the_page_limit() -> None:
  """A catalog that always says more still stops at the page limit."""
  fetch, seen = sequenced(
    *[{"data": [], "has_more": True} for _ in range(MAX_PAGES + 5)]
  )
  assert enrichment.litellm_entries("openrouter", fetch) == {}
  assert len(seen) == MAX_PAGES


def test_litellm_entries_skips_a_row_with_no_id() -> None:
  """A row with no string id never reaches the catalog."""
  fetch, _ = sequenced(
    {"data": [{"id": 5}, {"id": "openrouter/a"}, "nope"], "has_more": False}
  )
  assert set(enrichment.litellm_entries("openrouter", fetch)) == {"a"}


def test_litellm_entries_asks_for_the_catalog_name_of_the_provider() -> None:
  """`z-ai` reads as `zai`, and `kilo` reads as `openrouter`."""
  for provider, name in enrichment.LITELLM_PROVIDER.items():
    fetch, seen = sequenced({"data": [{"id": f"{name}/a"}], "has_more": False})
    assert set(enrichment.litellm_entries(provider, fetch)) == {"a"}
    assert f"provider={name}" in seen[0]


def test_modelschemas_entries_keys_the_rows_by_the_raw_id() -> None:
  """The raw id is the slug key, and a row without one stays out."""
  fetch, _ = sequenced(MS_PAGE)
  assert set(enrichment.modelschemas_entries("openrouter", fetch)) == {
    "a",
    "b",
    "c",
    "d",
    "e",
    "f",
  }


def test_modelschemas_entries_asks_for_the_service_name_of_the_provider() -> None:
  """`cloudflare` reads as `cloudflare-workers-ai`, and `z-ai` as `zai`."""
  for provider, name in enrichment.MODELSCHEMAS_PROVIDER.items():
    fetch, seen = sequenced({})
    enrichment.modelschemas_entries(provider, fetch)
    assert f"provider={name}" in seen[0]


def test_modelschemas_columns_reads_the_flag_and_the_ladder() -> None:
  """The flag comes from capabilities, the ladder from the reasoning fact, lowest first."""
  rows = {row["rawId"]: row for row in MS_PAGE["models"] if "rawId" in row}
  # a published name outside the vocabulary drops, and the list sorts lowest first
  assert enrichment.modelschemas_columns(rows["a"]) == {
    "supports_reasoning": True,
    "supported_efforts": ["none", "low", "high"],
  }
  # a toggle is the on/off pair, and a reasoning fact states the flag
  assert enrichment.modelschemas_columns(rows["b"]) == {
    "supports_reasoning": True,
    "supported_efforts": ["none", "max"],
  }
  # mandatory thinking cannot turn off, so the off rung drops
  assert enrichment.modelschemas_columns(rows["c"]) == {
    "supports_reasoning": True,
    "supported_efforts": ["low"],
  }
  # an explicit false flag stands alone
  assert enrichment.modelschemas_columns(rows["d"]) == {"supports_reasoning": False}
  # a mandatory toggle only takes on
  assert enrichment.modelschemas_columns(rows["e"]) == {
    "supports_reasoning": True,
    "supported_efforts": ["max"],
  }
  # adaptive names no rung the operator picks
  assert enrichment.modelschemas_columns(rows["f"]) == {"supports_reasoning": True}


def test_column_values_keeps_only_the_stored_columns() -> None:
  """A key that is not a stored column stays out of the row."""
  assert enrichment.column_values({"rpm": 5, "not_a_column": 1}) == {"rpm": 5}


def test_column_values_drops_a_value_that_is_none() -> None:
  """A column the block does not set does not overwrite the catalog."""
  assert enrichment.column_values({"rpm": None}) == {}


def test_column_values_turns_tools_into_the_function_calling_flag() -> None:
  """`tools` is a config word. The column is a boolean."""
  assert enrichment.column_values({"tools": True}) == {
    "supports_function_calling": True
  }
  assert enrichment.column_values({"tools": False}) == {
    "supports_function_calling": False
  }


def test_config_params_applies_a_pattern_that_matches() -> None:
  """A matching `models` entry beats the provider value."""
  found = enrichment.config_params(
    {"rpm": 5, "models": {"*big*": {"rpm": 1}}}, "a-big-model"
  )
  assert found["rpm"] == 1


def test_config_params_ignores_a_pattern_that_does_not_match() -> None:
  """A pattern the slug fails keeps the provider value."""
  found = enrichment.config_params(
    {"rpm": 5, "models": {"*small*": {"rpm": 1}}}, "a-big-model"
  )
  assert found["rpm"] == 5


def test_config_params_takes_the_last_matching_entry() -> None:
  """Two matching entries resolve in file order, so the later one wins."""
  block = {"models": {"*big*": {"rpm": 1}, "*model": {"rpm": 2}}}
  assert enrichment.config_params(block, "a-big-model")["rpm"] == 2


def test_enrich_reads_each_provider_once() -> None:
  """Two models of one provider share one read of each source."""
  fetch, seen = sequenced(PAGE, MS_PAGE)
  rows, problems = enrichment.enrich(["openrouter/a", "openrouter/b"], {}, fetch)
  assert len(seen) == 2
  litellm_page = with_param(
    with_param(
      with_param(enrichment.LITELLM_CATALOG, "provider", "openrouter"),
      "page_size",
      enrichment.LITELLM_PAGE_SIZE,
    ),
    "page",
    1,
  )
  assert seen[0] == litellm_page
  assert seen[1] == with_param(enrichment.MODELSCHEMAS_URL, "provider", "openrouter")
  assert [row["id"] for row in rows] == ["openrouter/a", "openrouter/b"]
  assert rows[0]["rpm"] == 9
  assert rows[0]["slug"] == "a"
  assert problems == []


def test_enrich_reports_a_catalog_that_failed() -> None:
  """A provider whose reads raised takes one problem line per source, and the rows still come out."""
  failed: list[str] = []
  rows, problems = enrichment.enrich(
    ["openrouter/a", "openrouter/b"], {}, broken, failed=failed
  )
  assert failed == ["openrouter"]
  assert len(problems) == 2
  assert problems[0].startswith("openrouter: LiteLLM catalog failed: boom")
  assert problems[1].startswith("openrouter: modelschemas source failed: boom")
  assert [row["id"] for row in rows] == ["openrouter/a", "openrouter/b"]


def test_enrich_reports_a_modelschemas_source_that_failed() -> None:
  """Only the modelschemas read failed: one problem line, and the other source still fills."""
  failed: list[str] = []

  modelschemas_page = with_param(enrichment.MODELSCHEMAS_URL, "provider", "openrouter")

  def flaky(url: str, headers: dict[str, str]) -> dict:
    if url == modelschemas_page:
      raise httpx.HTTPError("boom")
    return PAGE

  rows, problems = enrichment.enrich(["openrouter/a"], {}, flaky, failed=failed)
  assert failed == ["openrouter"]
  assert problems == ["openrouter: modelschemas source failed: boom"]
  assert rows[0]["rpm"] == 9


def test_enrich_fills_only_what_the_earlier_sources_left_empty() -> None:
  """modelschemas fills the gaps. The native column and the config key keep the lead."""
  fetch, _ = sequenced(PAGE, MS_PAGE)
  config = {"openrouter": {"models": {"c": {"supported_reasoning_efforts": ["xhigh"]}}}}
  rows, _ = enrichment.enrich(
    ["openrouter/a", "openrouter/b", "openrouter/c", "openrouter/d"],
    config,
    fetch,
    native={"openrouter/b": {"supported_efforts": ["low"]}},
  )
  by_id = {row["id"]: row for row in rows}
  # the modelschemas ladder and flag fill the gap the catalog left
  assert by_id["openrouter/a"]["supported_efforts"] == ["none", "low", "high"]
  assert by_id["openrouter/a"]["supports_reasoning"] is True
  # the native column beats modelschemas
  assert by_id["openrouter/b"]["supported_efforts"] == ["low"]
  # the config key beats modelschemas
  assert by_id["openrouter/c"]["supported_efforts"] == ["xhigh"]
  # an explicit false flag stands, and a model without a ladder stays empty
  assert by_id["openrouter/d"]["supports_reasoning"] is False
  assert by_id["openrouter/d"]["supported_efforts"] is None


def test_enrich_keeps_the_litellm_ladder_over_modelschemas() -> None:
  """A ladder the catalog holds stays over the one the service names."""
  litellm = {
    "data": [{"id": "openrouter/g", "supported_efforts": ["high"]}],
    "has_more": False,
  }
  ms = {
    "count": 1,
    "models": [
      {
        "rawId": "g",
        "capabilities": {"reasoning": True},
        "reasoning": {"mode": "effort", "mandatory": False, "efforts": ["low"]},
      }
    ],
  }
  fetch, _ = sequenced(litellm, ms)
  rows, _ = enrichment.enrich(["openrouter/g"], {}, fetch)
  assert rows[0]["supported_efforts"] == ["high"]


def test_enrich_lets_the_native_columns_beat_the_catalog() -> None:
  """What the provider API said wins over what the catalog guessed."""
  fetch, _ = sequenced(PAGE)
  rows, _ = enrichment.enrich(
    ["openrouter/a"], {}, fetch, native={"openrouter/a": {"rpm": 1}}
  )
  assert rows[0]["rpm"] == 1


def test_enrich_leaves_a_model_with_no_catalog_entry() -> None:
  """No catalog row means no columns, not a missing model."""
  fetch, _ = sequenced(PAGE)
  rows, _ = enrichment.enrich(["openrouter/zzz"], {}, fetch)
  assert rows[0]["id"] == "openrouter/zzz"
  assert rows[0]["rpm"] is None


def test_enrich_finalizes_the_supported_efforts_ladder() -> None:
  """The store takes the resolved ladder of a reasoning model, null for one that does not, and the claimed tier."""
  fetch, _ = sequenced(PAGE)
  config = {
    "openrouter": {
      "models": {"a": {"supported_reasoning_efforts": ["xhigh"]}},
      "tier": {"TIER-A": ["a", "c"], "TIER-B": ["b"]},
    }
  }
  rows, _ = enrichment.enrich(
    ["openrouter/a", "openrouter/b", "openrouter/c"],
    config,
    fetch,
    native={
      "openrouter/a": {"supports_reasoning": True},
      "openrouter/b": {
        "supports_reasoning": False,
        "supported_efforts": ["max", "high"],
      },
      "openrouter/c": {
        "supports_reasoning": True,
        "supported_efforts": ["high", "medium"],
      },
    },
  )
  # the model key has the last word
  assert rows[0]["supported_efforts"] == ["xhigh"]
  # a model that does not reasoning carries null, even if a list is present
  assert rows[1]["supported_efforts"] is None
  # a reasoning model without config keys keeps the list its catalog row holds
  assert rows[2]["supported_efforts"] == ["high", "medium"]
  # the tier the block claims, or null for a row no pattern claims
  assert rows[0]["tier"] == "TIER-A"
  assert rows[1]["tier"] == "TIER-B"
  assert rows[2]["tier"] == "TIER-A"
