import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from daedalus import catalog, store

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


def main() -> None:
  seen = []

  def fetch(url: str, headers: dict) -> dict:
    query = parse_qs(urlsplit(url).query)
    seen.append(query)
    assert url.startswith(catalog.LITELLM_CATALOG), url
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
  rows, problems = catalog.enrich(lines, config, fetch, native)
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

    table = Path(folder) / "models.tsv"
    catalog.write_models_tsv(rows, table)
    header, first, *rest = table.read_text(encoding="utf-8").splitlines()
    shown = [line.split("\t")[0] for line in (first, *rest)]
    assert shown == routable, shown
    assert header.split("\t") == ["id", *store.COLUMNS], header
    cells = dict(zip(header.split("\t"), first.split("\t"), strict=True))
    assert cells["id"] == "z-ai/glm-5" and cells["mode"] == "chat", cells
    assert cells["supports_function_calling"] == "true", cells
    dropped = {"input_cost_per_token", "deprecation_date", "source"}
    assert not dropped & set(cells), cells
  print("ok: model store")


if __name__ == "__main__":
  main()
