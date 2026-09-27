import tempfile
from pathlib import Path

from daedalus import catalog


def main() -> None:
  root = Path(__file__).resolve().parent.parent / ".daedalus-state"
  assert catalog.MODELS_TXT == root / "models.txt"
  with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / ".daedalus-state" / "models.txt"
    catalog.write_models_txt(["provider/model"], target)
    original = catalog.MODELS_TXT
    catalog.MODELS_TXT = target
    try:
      assert catalog.read_models_txt() == ["provider/model"]
    finally:
      catalog.MODELS_TXT = original
  print("ok: application state paths")


if __name__ == "__main__":
  main()
