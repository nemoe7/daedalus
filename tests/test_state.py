import tempfile
from pathlib import Path

from daedalus import store


def main() -> None:
  root = Path(__file__).resolve().parent.parent / ".daedalus-state"
  assert store.MODELS_DB == root / "models.sqlite3"
  with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / ".daedalus-state" / "models.sqlite3"
    store.write_store([{"id": "provider/model"}], target)
    original = store.MODELS_DB
    store.MODELS_DB = target
    try:
      assert store.read_models() == ["provider/model"]
    finally:
      store.MODELS_DB = original
  print("ok: application state paths")


if __name__ == "__main__":
  main()
