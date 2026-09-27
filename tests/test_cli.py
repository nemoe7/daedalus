import contextlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from daedalus import api, catalog

DAEDALUS = [sys.executable, "-c", "from daedalus.api import run; run()"]


def check_help() -> None:
  shown = subprocess.run(
    [*DAEDALUS, "--help"], capture_output=True, text=True, timeout=10, check=False
  )
  assert shown.returncode == 0, shown.stderr
  assert shown.stdout.startswith("usage:"), shown.stdout
  for flag in ("-i, --init", "-c, --catalog"):
    assert flag in shown.stdout, shown.stdout
  wrong = subprocess.run(
    [*DAEDALUS, "--wrong"], capture_output=True, text=True, timeout=10, check=False
  )
  assert wrong.returncode == 2, wrong.stderr
  assert not hasattr(catalog, "main"), "python -m daedalus.catalog has no entry point"


def check_flags() -> None:
  calls: list[str] = []
  original = (catalog.MODELS_DB, catalog.refresh, sys.modules.get("uvicorn"))
  catalog.refresh = lambda: calls.append("catalog")
  sys.modules["uvicorn"] = SimpleNamespace(run=lambda *_, **__: calls.append("listen"))
  try:
    with tempfile.TemporaryDirectory() as folder:
      catalog.MODELS_DB = Path(folder) / "models.sqlite3"
      shown = io.StringIO()
      with contextlib.redirect_stdout(shown):
        api.run([])
      assert calls == [] and shown.getvalue().startswith("usage:"), calls
      api.run(["--init"])
      assert calls == ["catalog", "listen"], calls
      catalog.MODELS_DB.touch()
      calls.clear()
      api.run(["--init"])
      assert calls == ["listen"], calls
      calls.clear()
      api.run(["--catalog"])
      assert calls == ["catalog"], calls
      calls.clear()
      api.run(["--catalog", "--init"])
      assert calls == ["catalog", "listen"], calls
      calls.clear()
      api.run(["-c", "-i"])
      assert calls == ["catalog", "listen"], calls
  finally:
    catalog.MODELS_DB, catalog.refresh, uvicorn = original
    if uvicorn is None:
      sys.modules.pop("uvicorn", None)
    else:
      sys.modules["uvicorn"] = uvicorn


def main() -> None:
  check_help()
  check_flags()
  print("ok: cli flags")


if __name__ == "__main__":
  main()
