import tempfile
from pathlib import Path

from daedalus import catalog, interactions


def main() -> None:
  root = Path(__file__).resolve().parent.parent / ".daedalus-state"
  assert catalog.MODELS_TXT == root / "models.txt"
  assert interactions.STATE_DIR == root / "interactions"
  with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / ".daedalus-state" / "models.txt"
    catalog.write_models_txt(["provider/model"], target)
    original = catalog.MODELS_TXT
    catalog.MODELS_TXT = target
    try:
      assert catalog.read_models_txt() == ["provider/model"]
    finally:
      catalog.MODELS_TXT = original
    original_state = interactions.STATE_DIR
    interactions.STATE_DIR = target.parent / "interactions"
    try:
      record = {"model": "provider/model", "messages": [], "steps": []}
      interactions.save("int_test", record)
      assert interactions.load("int_test") == record
      assert (interactions.STATE_DIR / "int_test.json").is_file()
    finally:
      interactions.STATE_DIR = original_state
  print("ok: application state paths")


if __name__ == "__main__":
  main()
