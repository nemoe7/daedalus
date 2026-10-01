"""Time the store reads that each chat request repeats. Run: uv run python scripts/bench_store.py"""

import shutil
import statistics
import tempfile
import time
from pathlib import Path

from daedalus import store
from daedalus.routing import cooldowns, penalties

# A free-provider catalog holds a few hundred models.
MODELS = 600
CALLS = 200


def fill(path: Path) -> list[str]:
  """A store with MODELS rows, some of them with input, rate and pace limits."""
  rows = []
  for index in range(MODELS):
    row = {"id": f"provider-{index % 7}/model-{index}", "mode": "chat"}
    if index % 2 == 0:
      row["max_input_tokens"] = 32_000 * (1 + index % 8)
    if index % 3 == 0:
      row["rpm"] = 60
      row["tpm"] = 100_000
    rows.append(row)
  store.write_store(rows, path)
  return [row["id"] for row in rows]


def time_it(label: str, work) -> None:
  """The mean and the p95 of one call, in milliseconds."""
  taken = []
  for _ in range(CALLS):
    start = time.perf_counter()
    work()
    taken.append((time.perf_counter() - start) * 1000)
  taken.sort()
  mean = statistics.fmean(taken)
  p95 = taken[max(0, round(CALLS * 0.95) - 1)]
  print(f"{label:<26} {mean:7.3f} ms mean {p95:7.3f} ms p95")


def main() -> None:
  folder = Path(tempfile.mkdtemp(prefix="daedalus-bench-"))
  try:
    path = folder / "models.sqlite3"
    models = fill(path)
    store.MODELS_DB = path
    print(f"{MODELS} models, {CALLS} calls each\n")
    time_it("O1 store.input_limits", store.input_limits)
    time_it("O2 store.pace_limits", store.pace_limits)
    pen = penalties.Penalties(lambda: path)
    time_it("O3 Penalties.weights", lambda: pen.weights(models))
    cool = cooldowns.Cooldowns(lambda: path)
    time_it("O3 Cooldowns.ends", cool.ends)
  finally:
    shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
  main()
