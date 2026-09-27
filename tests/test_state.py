import tempfile
from pathlib import Path

from daedalus import catalog


def main() -> None:
  root = Path(__file__).resolve().parent.parent / ".daedalus-state"
  assert catalog.MODELS_TSV == root / "models.tsv"
  assert catalog.MODELS_DB == root / "models.sqlite3"
  with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / ".daedalus-state" / "models.sqlite3"
    catalog.write_store([{"id": "provider/model"}], target)
    original = catalog.MODELS_DB
    catalog.MODELS_DB = target
    try:
      assert catalog.read_models() == ["provider/model"]
    finally:
      catalog.MODELS_DB = original
  print("ok: application state paths")


if __name__ == "__main__":
  main()
