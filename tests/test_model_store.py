import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from daedalus import catalog

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
    "kilo": {},
    "broken": {},
  }
  lines = [
    "z-ai/glm-5",
    "z-ai/glm-embed",
    "kilo/google/gemma-4-31b-it:free",
    "broken/x",
  ]
  rows, problems = catalog.enrich(lines, config, fetch)
  assert [query["page"] for query in seen[:2]] == [["1"], ["2"]], seen
  assert len(seen) == 4, seen
  assert problems == ["broken: LiteLLM catalog failed: down"], problems
  glm = rows[0]
  assert glm["max_input_tokens"] == 131072, glm
  assert glm["max_output_tokens"] == 128000, glm
  assert glm["rpm"] == 60 and glm["tpm"] == 8000, glm
  assert glm["supports_function_calling"] is True, glm
  assert rows[1]["mode"] == "embedding", rows[1]
  assert rows[2]["mode"] == "chat", rows[2]
  assert rows[3]["mode"] is None and rows[3]["id"] == "broken/x", rows[3]

  with tempfile.TemporaryDirectory() as folder:
    database = Path(folder) / "models.sqlite3"
    catalog.write_store(rows, database)
    catalog.write_store(rows, database)
    original = catalog.MODELS_DB
    catalog.MODELS_DB = database
    try:
      routable = ["z-ai/glm-5", "kilo/google/gemma-4-31b-it:free", "broken/x"]
      assert catalog.read_models() == routable, catalog.read_models()
      assert catalog.read_models(routable_only=False) == lines
    finally:
      catalog.MODELS_DB = original
    with sqlite3.connect(database) as connection:
      stored = connection.execute(
        "SELECT provider, slug, max_input_tokens, supports_function_calling "
        "FROM models WHERE id = 'z-ai/glm-5'"
      ).fetchone()
    connection.close()
    assert stored == ("z-ai", "glm-5", 131072, 1), stored

    table = Path(folder) / "models.txt"
    catalog.write_models_txt(rows, table)
    header, first, *_ = table.read_text(encoding="utf-8").splitlines()
    assert header.split("\t") == ["id", *catalog.COLUMNS], header
    cells = dict(zip(header.split("\t"), first.split("\t"), strict=True))
    assert cells["id"] == "z-ai/glm-5" and cells["mode"] == "chat", cells
    assert cells["supports_function_calling"] == "true", cells
    assert cells["deprecation_date"] == "", cells
  print("ok: model store")


if __name__ == "__main__":
  main()
