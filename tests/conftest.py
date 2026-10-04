"""Shared pytest setup: a state folder for each test file, and async tests on asyncio."""

import inspect
import os

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.catalog import discovery, schedule
from daedalus.config import defaults as defaults_module
from daedalus.providers import hooks, signatures
from daedalus.routing import loops, router
from daedalus.server import api, headroom, media, upstream

# The master key of a test file without its own MASTER value.
MASTER = "test-master-key-0001"


def no_network(*_args: object, **_kwargs: object) -> str:
  """A test never reads the default provider file of the repository over the network."""
  raise httpx.ConnectError("no network in a test")


# The values that `api.apply_settings` sets. Each test file gets them back at the end.
SETTINGS = (
  *((upstream, name) for name in ("TIMEOUT_SECONDS", "WAIT_SECONDS")),
  *(
    (api, name)
    for name in (
      "SLOW_SECONDS",
      "AFFINITY",
      "KEYWORDS",
      "SWITCH",
      "PARALLEL_ENABLED",
      "PARALLEL_COUNT",
      "PARALLEL_CHANCE",
      "PARALLEL_SLOW_SECONDS",
      "PARALLEL_PENALTY",
      "REQUEST_HOOKS",
    )
  ),
  *(
    (api.PENALTIES, name)
    for name in (
      "idle",
      "enabled",
      "change_on_draw",
      "race",
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
  *(
    (loops, name)
    for name in ("IDLE_SECONDS", "CALLS", "REPEATS", "SHORTEST", "LONGEST")
  ),
  (headroom, "TIMEOUT_SECONDS"),
  *((schedule, name) for name in ("EVERY", "ANCHOR", "BUSY", "PENDING")),
  (router, "RENAMED"),
)


@pytest.fixture(autouse=True, scope="module")
def state_folder(
  request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
):
  """A temporary state folder and fresh router state for each test file. No test touches `.daedalus-state`."""
  folder = tmp_path_factory.mktemp("state")
  environ = dict(os.environ)
  os.environ.pop(headroom.HEADROOM_URL, None)
  os.environ[dashboard.DAEDALUS_MASTER_KEY] = getattr(request.module, "MASTER", MASTER)
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(store, "MODELS_DB", folder / "models.sqlite3")
    # No test reads the default provider file of the repository over the network.
    patch.setattr(defaults_module, "PATH", folder / "free.defaults.yml")
    patch.setattr(defaults_module, "fetch", no_network)
    patch.setattr(discovery, "DUMP_DIR", folder / "dump")
    patch.setattr(hooks, "CONFIG_DIR", folder / "config")
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
    patch.setattr(api, "CATALOG_REFRESH", None)
    patch.setattr(api, "CATALOG_REBUILD_CACHED", None)
    schedule.BUSY, schedule.PENDING = False, None
    config.set_config(None)
    config.SAVED.clear()
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


# The pull request and commit checks live in scripts/, which is not a package.
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))
