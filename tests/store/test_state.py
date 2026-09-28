import tempfile
from pathlib import Path

from daedalus import config, store


def test_state_paths() -> None:
  root = Path(__file__).resolve().parents[2] / ".daedalus-state"
  assert config.STATE_DIR / "models.sqlite3" == root / "models.sqlite3"
  with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / ".daedalus-state" / "models.sqlite3"
    store.write_store([{"id": "provider/model"}], target)
    original = store.MODELS_DB
    store.MODELS_DB = target
    try:
      assert store.read_models() == ["provider/model"]
    finally:
      store.MODELS_DB = original
