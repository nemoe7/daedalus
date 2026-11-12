"""Run one job per file on worker threads and give up on a file after a fixed time."""

import queue
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, wait


def run_with_timeouts(files, process, workers, file_timeout=10.0, verbose=False):
  """Return `(results, skipped)`: process output per file, and files that ran out of time."""
  total = len(files)
  results = {}
  skipped = []
  pending = {}
  next_index = 0
  jobs = queue.Queue(maxsize=workers)

  def worker():
    while True:
      job = jobs.get()
      if job is None:
        return
      future, path = job
      if not future.set_running_or_notify_cancel():
        continue
      try:
        future.set_result(process(path))
      except BaseException as error:
        future.set_exception(error)

  for _ in range(workers):
    threading.Thread(target=worker, daemon=True).start()

  def submit_more(count):
    nonlocal next_index
    for _ in range(count):
      if next_index >= len(files):
        return
      path = files[next_index]
      if verbose:
        print(f"start {path}", file=sys.stderr, flush=True)
      future = Future()
      jobs.put_nowait((future, path))
      pending[future] = (path, time.monotonic() + file_timeout)
      next_index += 1

  submit_more(workers)
  done_count = 0
  while pending:
    timeout = max(
      min(deadline for _, deadline in pending.values()) - time.monotonic(), 0
    )
    done, _ = wait(list(pending), timeout=timeout, return_when=FIRST_COMPLETED)
    now = time.monotonic()
    expired = [
      future
      for future, (_, deadline) in pending.items()
      if future not in done and deadline <= now
    ]
    for future in done | set(expired):
      path = pending[future][0]
      if verbose:
        outcome = "timeout" if future in expired else "done"
        print(f"{outcome} {path}", file=sys.stderr, flush=True)
      del pending[future]
      if future in expired:
        future.cancel()
        skipped.append(path)
      else:
        try:
          results[path] = future.result()
        except Exception as error:
          results[path] = {
            "test": None,
            "error": f"{path}: {type(error).__name__}: {error}",
            "rows": [],
            "text": None,
          }
      done_count += 1
      print(".", end="", file=sys.stderr, flush=True)
      if done_count % 25 == 0 or done_count == total:
        print(f" [{done_count * 100 // total:>3}%]", file=sys.stderr, flush=True)
    retained = []
    while True:
      try:
        job = jobs.get_nowait()
      except queue.Empty:
        break
      if not job[0].cancelled():
        retained.append(job)
    for job in retained:
      jobs.put_nowait(job)
    submit_more(len(done) + len(expired))
  if skipped:
    print(
      "\nSkipped (no result within 10s):\n" + "\n".join(str(path) for path in skipped),
      file=sys.stderr,
    )
  for _ in range(workers):
    jobs.put_nowait(None)
  return results, skipped
