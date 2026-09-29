"""Shared pytest setup: a state folder for each test file, and async tests on asyncio."""

import inspect
import os

import pytest

from daedalus import config, dashboard, store
from daedalus.catalog import discovery, schedule
from daedalus.providers import hooks, signatures
from daedalus.routing import loops, router
from daedalus.server import api, headroom, media, upstream

# The master key of a test file without its own MASTER value.
MASTER = "test-master-key-0001"
# The values that `api.apply_settings` sets. Each test file gets them back at the end.
SETTINGS = (
  *((upstream, name) for name in ("TIMEOUT_SECONDS", "WAIT_SECONDS")),
  *((api, name) for name in ("SLOW_SECONDS", "AFFINITY", "KEYWORDS", "SWITCH")),
  *(
    (api.PENALTIES, name)
    for name in (
      "idle",
      "enabled",
      "stay",
      "success",
      "fault",
      "slow",
      "hourly",
      "rate_limit",
    )
  ),
  (api.RETRIES, "idle"),
  (media.REPEATS, "idle"),
  (api.PACING, "enabled"),
  (signatures, "IDLE_SECONDS"),
  (loops, "IDLE_SECONDS"),
  (headroom, "TIMEOUT_SECONDS"),
  *((schedule, name) for name in ("EVERY", "ANCHOR")),
  (router, "RENAMED"),
)


@pytest.fixture(autouse=True, scope="module")
def state_folder(
  request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
):
  """A temporary state folder and fresh router state for each test file. No test touches `.daedalus-state`."""
  folder = tmp_path_factory.mktemp("state")
  environ = dict(os.environ)
  os.environ.pop(headroom.URL_ENV, None)
  os.environ[dashboard.MASTER_ENV] = getattr(request.module, "MASTER", MASTER)
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(store, "MODELS_DB", folder / "models.sqlite3")
    patch.setattr(discovery, "DUMP_DIR", folder / "dump")
    patch.setattr(hooks, "HOOKS_DIR", folder / "hooks")
    patch.setattr(api.PENALTIES, "pick", api.PENALTIES.pick)
    patch.setattr(headroom, "_down", False)
    # Each test file starts like a new process, with no router state.
    for kept in (
      api.PENALTIES,
      api.RETRIES,
      api.COOLDOWNS,
      api.PACING,
      media.REPEATS,
      api.LIMITS,
      dashboard.HISTORY,
      loops,
    ):
      kept.clear()
    dashboard.LIVE.rows.clear()
    for owner, name in SETTINGS:
      patch.setattr(owner, name, getattr(owner, name))
    config.set_config(None)
    upstream.set_client(None)
    yield folder
  os.environ.clear()
  os.environ.update(environ)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
  for item in items:
    if inspect.iscoroutinefunction(getattr(item, "function", None)):
      item.add_marker(pytest.mark.anyio)


@pytest.fixture
def anyio_backend() -> str:
  return "asyncio"
