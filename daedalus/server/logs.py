"""Log format and duration text for the router log."""

import logging
import time

LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s %(message)s"


def setup_logging() -> None:
  """Write every log line in one format, without Uvicorn access or httpx request lines."""
  logging.addLevelName(logging.WARNING, "WARN")
  logging.basicConfig(
    level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S", force=True
  )
  logging.getLogger("httpx").setLevel(logging.WARNING)
  for handler in logging.getLogger().handlers:
    handler.addFilter(short_name)


def short_name(record: logging.LogRecord) -> bool:
  """Show Uvicorn server lines as `uvicorn`, not as `uvicorn.error`."""
  if record.name == "uvicorn.error":
    record.name = "uvicorn"
  return True


def seconds_text(seconds: float) -> str:
  """A duration as log text, in seconds with 3 decimals."""
  return f"{seconds:.3f}s"


def elapsed(started: float) -> str:
  """The time since `started`, as log text."""
  return seconds_text(time.perf_counter() - started)
