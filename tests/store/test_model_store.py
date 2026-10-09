import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from daedalus import store
from daedalus.catalog import enrichment

PAGES = {
  ("zai", "1"): {
    "data": [
      {
        "id": "zai/glm-5",
        "mode": "chat",
        "max_input_tokens": 200000,
        "max_output_tokens": 128000,
        "supports_function_calling": True,
        "rpm": 10,
        "input_cost_per_token": 1e-06,
        "source": "https://zai.test",
      }
    ],
    "has_more": True,
  },
  ("zai", "2"): {
    "data": [{"id": "zai/glm-embed", "mode": "embedding"}],
    "has_more": False,
  },
  ("openrouter", "1"): {
    "data": [{"id": "openrouter/google/gemma-4-31b-it:free", "mode": "chat"}],
    "has_more": False,
  },
}


def test_model_store() -> None:
  seen = []

  def fetch(url: str, headers: dict) -> dict:
    if url.startswith(enrichment.MODELSCHEMAS_URL):
      return {}
    query = parse_qs(urlsplit(url).query)
    seen.append(query)
    assert url.startswith(enrichment.LITELLM_CATALOG), url
    assert query["page_size"] == ["500"], query
    if query["provider"] == ["broken"]:
      raise httpx.ConnectError("down")
    return PAGES.get((query["provider"][0], query["page"][0]), {"data": []})

  config = {
    "z-ai": {
      "rpm": 60,
      "models": {"glm-5": {"max_input_tokens": 131072}, "glm-*": {"tpm": 8000}},
    },
    "kilo": {"tools": True, "models": {"google/*": {"tools": False}}},
    "broken": {},
  }
  lines = [
    "z-ai/glm-5",
    "z-ai/glm-embed",
    "kilo/google/gemma-4-31b-it:free",
    "broken/x",
  ]
  native = {"z-ai/glm-5": {"max_input_tokens": 1, "max_output_tokens": 64000}}
  rows, problems = enrichment.enrich(lines, config, fetch, native)
  assert [query["page"] for query in seen[:2]] == [["1"], ["2"]], seen
  assert len(seen) == 4, seen
  assert problems == ["broken: LiteLLM catalog failed: down"], problems
  glm = rows[0]
  assert glm["max_input_tokens"] == 131072, glm
  assert glm["max_output_tokens"] == 64000, "the provider row wins over LiteLLM"
  assert glm["rpm"] == 60 and glm["tpm"] == 8000, glm
  assert glm["supports_function_calling"] is True, glm
  assert rows[1]["mode"] == "embedding", rows[1]
  assert rows[2]["mode"] == "chat", rows[2]
  assert rows[2]["supports_function_calling"] is False, (
    "a models entry wins over the provider"
  )
  assert rows[3]["supports_function_calling"] is None, rows[3]
  assert rows[3]["mode"] is None and rows[3]["id"] == "broken/x", rows[3]

  with tempfile.TemporaryDirectory() as folder:
    database = Path(folder) / "models.sqlite3"
    store.write_store(rows, database)
    store.write_store(rows, database)
    original = store.MODELS_DB
    store.MODELS_DB = database
    try:
      routable = ["z-ai/glm-5", "kilo/google/gemma-4-31b-it:free", "broken/x"]
      assert store.read_models() == routable, store.read_models()
      assert store.read_models(routable_only=False) == lines
      assert store.read_models(tools_only=True) == ["z-ai/glm-5"], "config and catalog"
    finally:
      store.MODELS_DB = original
    with sqlite3.connect(database) as connection:
      stored = connection.execute(
        "SELECT provider, slug, max_input_tokens, supports_function_calling "
        "FROM models WHERE id = 'z-ai/glm-5'"
      ).fetchone()
    connection.close()
    assert stored == ("z-ai", "glm-5", 131072, 1), stored
    store.MODELS_DB = database
    try:
      full = store.stored_rows()
      assert [row["id"] for row in full] == lines, full
      assert set(full[0]) == set(store.STORED_COLUMNS), (
        "the dump carries every stored column"
      )
      assert full[0]["provider"] == "z-ai" and full[0]["slug"] == "glm-5", full[0]
      assert full[0]["supports_function_calling"] is True, "a flag reads as a boolean"
      compact = store.model_rows()
      assert set(compact[0]) == {
        "id",
        "provider",
        "slug",
        "mode",
        "max_input_tokens",
        "max_output_tokens",
        "tools",
        "reasoning",
        "effort",
        "efforts",
        "flags",
      }, compact[0]
    finally:
      store.MODELS_DB = original


def test_supported_efforts_round_trip() -> None:
  """A stored effort list reads back as a list, and a model without one reads nothing."""
  with tempfile.TemporaryDirectory() as folder:
    database = Path(folder) / "models.sqlite3"
    rows = [
      {
        "id": "openrouter/z-ai/glm-5.3-flash",
        "reasoning_effort": "medium",
        "supported_efforts": ["max", "high", "medium", "low"],
      },
      {"id": "openrouter/plain"},
    ]
    store.write_store(rows, database)
    with sqlite3.connect(database) as connection:
      stored = connection.execute(
        "SELECT supported_efforts FROM models WHERE id = 'openrouter/z-ai/glm-5.3-flash'"
      ).fetchone()
    connection.close()
    assert stored == ('["max", "high", "medium", "low"]',), stored
    original = store.MODELS_DB
    store.MODELS_DB = database
    try:
      found = store.model_limits("openrouter/z-ai/glm-5.3-flash")
      assert found["supported_efforts"] == ["max", "high", "medium", "low"], found
      assert found["reasoning_effort"] == "medium", found
      assert "supported_efforts" not in store.model_limits("openrouter/plain")
      assert store.model_limits("openrouter/nope") == {}
    finally:
      store.MODELS_DB = original


def test_tier_round_trip() -> None:
  """A stored tier reads back with the limits of the model, and a model without one reads nothing."""
  with tempfile.TemporaryDirectory() as folder:
    database = Path(folder) / "models.sqlite3"
    store.write_store([{"id": "p/one", "tier": "TIER-A"}, {"id": "q/two"}], database)
    original = store.MODELS_DB
    store.MODELS_DB = database
    try:
      assert store.model_limits("p/one")["tier"] == "TIER-A"
      assert "tier" not in store.model_limits("q/two")
    finally:
      store.MODELS_DB = original


def test_in_place() -> None:
  with tempfile.TemporaryDirectory() as folder:
    database = Path(folder) / "models.sqlite3"
    first = [
      {"id": "a/1", "max_input_tokens": 1000},
      {"id": "a/2"},
      {"id": "b/1", "max_input_tokens": 2000},
    ]
    store.write_store(first, database)
    with sqlite3.connect(database) as connection:
      connection.execute("CREATE TABLE weights (model TEXT, weight REAL)")
      connection.execute("INSERT INTO weights VALUES ('a/1', 0.5)")
    connection.close()
    second = [{"id": "a/1", "max_input_tokens": None}, {"id": "c/1"}]
    store.write_store(second, database, keep=["b"], fill=["a"])
    with sqlite3.connect(database) as connection:
      rows = connection.execute(
        "SELECT id, max_input_tokens FROM models ORDER BY id"
      ).fetchall()
      weights = connection.execute("SELECT * FROM weights").fetchall()
    connection.close()
    assert rows == [("a/1", 1000), ("b/1", 2000), ("c/1", None)], rows
    assert weights == [("a/1", 0.5)], "other tables stay"
    store.write_store([{"id": "a/1", "max_input_tokens": None}], database)
    with sqlite3.connect(database) as connection:
      rows = connection.execute("SELECT id, max_input_tokens FROM models").fetchall()
    connection.close()
    assert rows == [("a/1", None)], rows


def test_readable_and_set_aside() -> None:
  """An unreadable store is named, and a start moves it aside for a new build."""
  with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "models.sqlite3"
    store.MODELS_DB = path
    assert store.readable(), "no file: nothing to read"
    path.write_text("not a database")
    assert not store.readable(), "a text file"
    assert store.set_aside() == path.with_name("models.sqlite3.broken")
    assert not path.exists() and path.with_name("models.sqlite3.broken").exists()
    assert store.readable(), "the new file is missing, so a build starts clean"
    assert store.set_aside() is None, "nothing to move"
