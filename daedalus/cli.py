"""The `daedalus` command: serve, catalog, dump and hooks."""

import argparse
import errno
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import yaml

from daedalus import __version__, catalog, config, dashboard, store
from daedalus.catalog import discovery, exports
from daedalus.catalog.exports import dump_models
from daedalus.config import remote, settings
from daedalus.providers import hooks
from daedalus.server import api, logs
from daedalus.store import keys

logger = logging.getLogger("daedalus")
# The load check of `daedalus hooks verify` runs a file in its own process, with this limit.
IMPORT_TIMEOUT = 10.0
IMPORT_CODE = (
  "import sys; from pathlib import Path; from daedalus.providers.hooks import load; "
  "sys.exit(0 if load(Path(sys.argv[1])) else 1)"
)
# The discovery step of a host CLI: the wait for `/health`, and for the rebuild ask.
PROBE_SECONDS = 0.3
ASK_SECONDS = 2.0
EXPORT_SECONDS = 300.0
DAEDALUS_URL = "DAEDALUS_URL"
# The server hosts that a client reaches at the loopback address.
ANY_HOST = ("0.0.0.0", "::")


def import_problem(path: Path) -> str | None:
  """Return a hook-load error detail, or None after a successful subprocess check."""
  try:
    done = subprocess.run(
      [sys.executable, "-c", IMPORT_CODE, str(path)],
      capture_output=True,
      text=True,
      timeout=IMPORT_TIMEOUT,
      check=False,
    )
  except subprocess.TimeoutExpired:
    return f"no answer within {int(IMPORT_TIMEOUT)} seconds"
  except OSError as exc:
    return str(exc)
  if done.returncode == 0:
    return None
  lines = (done.stderr or done.stdout).strip().splitlines()
  return lines[-1] if lines else f"exit {done.returncode}"


def probe(url: str) -> bool:
  """Tell if a daedalus answers `GET /health` within `PROBE_SECONDS`."""
  try:
    with urllib.request.urlopen(f"{url}/health", timeout=PROBE_SECONDS) as answer:
      return answer.status == 200
  except (OSError, ValueError):
    return False


def server_url() -> str:
  """Return the configured server address without probing it."""
  url = os.environ.get(DAEDALUS_URL, "").rstrip("/")
  if not url:
    host = os.environ.get(api.DAEDALUS_HOST) or api.HOST
    if host in ANY_HOST:
      host = "127.0.0.1"
    url = f"http://{host}:{os.environ.get(api.DAEDALUS_PORT) or api.PORT}"
  return url


def live_url() -> str | None:
  """Return the configured server address if its health probe succeeds, or None."""
  url = server_url()
  return url if probe(url) else None


def refresh_dump(kind: str, file_format: str) -> None:
  """Use a completed live export, or refresh locally after an implicit local refusal."""
  url = server_url()
  key = dashboard.master()
  query = urlencode({"dump": kind, "format": file_format})
  request = urllib.request.Request(
    f"{url}/v1/catalog?{query}",
    method="POST",
    headers={"Authorization": f"Bearer {key}"} if key else {},
  )
  try:
    with urllib.request.urlopen(request, timeout=EXPORT_SECONDS) as answer:
      if answer.status != 200:
        raise ValueError("the server did not return a completed export")
      body = json.load(answer)
      if not isinstance(body, dict):
        raise TypeError("the server returned an invalid export")
      files = body.get("files")
  except urllib.error.HTTPError:
    raise
  except (urllib.error.URLError, OSError) as error:
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    local = urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"}
    if (
      os.environ.get(DAEDALUS_URL)
      or not local
      or not isinstance(cause, OSError)
      or cause.errno != errno.ECONNREFUSED
    ):
      raise
    store.migrate()
    started = time.time()
    catalog.refresh()
    files = exports.collect(kind, file_format, started)
  exports.write(files, kind, file_format)


class Refused(Exception):
  """The live daedalus answered the rebuild request with a refusal."""


def ask_rebuild(url: str) -> bool:
  """Request a live rebuild, returning False without a key or response and raising Refused on HTTP errors."""
  key = dashboard.master()
  if key is None:
    logger.warning("no master key: the rebuild runs in this process")
    return False
  request = urllib.request.Request(
    f"{url}/v1/catalog", method="POST", headers={"Authorization": f"Bearer {key}"}
  )
  try:
    with urllib.request.urlopen(request, timeout=ASK_SECONDS) as answer:
      return answer.status == 202
  except urllib.error.HTTPError as exc:
    raise Refused(
      f"the daedalus at {url} refused the rebuild: HTTP {exc.code}"
    ) from exc
  except (OSError, ValueError) as exc:
    logger.warning("the daedalus at %s did not answer: %s", url, exc)
  return False


def hooks_of() -> tuple[dict[str, Any], Path] | None:
  """The hooks group of the settings and its folder, or None with the problem printed."""
  try:
    group = settings.load()["hooks"]
  except settings.SettingsError as exc:
    print(f"settings: {exc}")
    return None
  hooks.set_installed(group["dir"], group["disabled"], group["order"])
  return group, hooks.folder()


def hooks_update() -> int:
  """Fetch the sources of the settings now, and print 1 row per installed file."""
  found = hooks_of()
  if found is None:
    return 2
  group, folder = found
  if not group["sources"]:
    print("no source in hooks.sources")
    return 1
  moved = remote.update(group["sources"], folder)
  repos = {source["repo"] for source in group["sources"]}
  for name, record in sorted(remote.read_records().items()):
    if record["repo"] in repos:
      print(f"{name:<24} {record['version'] or '-':<12} {lock_note(record)}")
  print(f"moved {len(moved)} file(s)" if moved else "no change")
  return 0


def lock_note(record: dict[str, str]) -> str:
  """The version, the repo and the short commit of 1 lock record."""
  version = record.get("version") or "-"
  source = f"{record['repo']}@{record['commit'][:12]}" if record.get("repo") else "-"
  return f"{version} {source}"


def hooks_list() -> int:
  """Print 1 row per hook file: the version, the scope, the state and the source."""
  found = hooks_of()
  if found is None:
    return 2
  records = remote.read_records()
  for row in hooks.rows():
    scope = row["scope"]
    if row["targets"]:
      scope = f"{scope or 'global'}: {', '.join(row['targets'])}"
    record = records.get(row["name"], {})
    source = lock_note(record) if record else ""
    state = "bad" if row["problem"] else "on" if row["enabled"] else "off"
    note = f"; {row['problem']}" if row["problem"] else ""
    print(
      f"{row['name']:<30} {row['version'] or '-':<12} {scope or '-':<24} {state:<4} {source}{note}"
    )
  return 0


def hooks_verify() -> int:
  """Check each hook file against the lock record of the sources and the loader."""
  found = hooks_of()
  if found is None:
    return 2
  _group, folder = found
  lock = remote.read_records()
  disk = remote.on_disk(folder)
  failed = 0
  for name in sorted(set(disk) | set(lock)):
    here = disk.get(name)
    record = lock.get(name, {})
    notes: list[str] = []
    bad = warned = unpinned = False
    if name in lock:
      if here is None:
        notes.append("in the lock file, not in the folder")
        bad = True
      elif here != record["sha256"]:
        notes.append(
          f"changed: the lock holds {record['sha256'][:12]}, the disk holds {here[:12]}"
        )
        bad = True
      else:
        notes.append(f"lock ok: {lock_note(record)}")
    if here is not None and name not in lock:
      notes.append("no record; run `daedalus hooks update`")
      unpinned = True
    if here is not None:
      problem = import_problem(folder / name)
      if problem:
        notes.append(f"does not load: {problem}")
        warned = True
    verdict = "bad" if bad else "warn" if warned else "unpinned" if unpinned else "ok"
    print(f"{verdict:<9} {name}: {'; '.join(notes)}")
    if bad:
      failed = 1
  return failed


def run(argv: list[str] | None = None) -> None:
  """Entry point for `daedalus`."""
  parser = argparse.ArgumentParser(
    prog="daedalus", description="OpenAI-compatible router for the providers."
  )
  parser.add_argument("--version", action="version", version=f"daedalus {__version__}")
  commands = parser.add_subparsers(dest="command", metavar="COMMAND")
  serve = commands.add_parser(
    "serve",
    help=f"start the router on {api.HOST}:PORT and build a missing store in the background",
  )
  serve.add_argument(
    "port",
    nargs="?",
    default=api.PORT,
    type=int,
    help=f"default {api.PORT}, or DAEDALUS_PORT",
  )
  serve.add_argument(
    "--catalog", action="store_true", help="rebuild the model store in the background"
  )
  catalog_command = commands.add_parser(
    "catalog", help="discover provider models and rebuild the model store"
  )
  catalog_command.add_argument(
    "--force",
    action="store_true",
    help="rebuild the store in this process when a live daedalus refuses the request",
  )
  dump = commands.add_parser(
    "dump", help="write provider catalogs or available models to .daedalus-state/dump"
  )
  dump.add_argument(
    "kind",
    nargs="?",
    choices=("catalog", "models", "all"),
    default="catalog",
    help="cached catalogs, stored models, or both (default: catalog)",
  )
  dump.add_argument(
    "-f",
    "--fmt",
    "--format",
    dest="format",
    choices=("json", "csv"),
    default="json",
    help="file format (default: json)",
  )
  dump.add_argument(
    "--refresh", action="store_true", help="fetch and wait for fresh dump files"
  )
  hooks_command = commands.add_parser(
    "hooks", help="update the hook files, list them, and check them on disk"
  )
  hooks_commands = hooks_command.add_subparsers(dest="hooks_command", metavar="COMMAND")
  hooks_commands.add_parser(
    "update", help="fetch the sources of hooks.sources and write the hook files"
  )
  hooks_commands.add_parser(
    "list",
    help="print the version, the scope, the state and the source of each hook file",
  )
  hooks_commands.add_parser(
    "verify",
    help="check each hook file against the lock record, and load it with a warning",
  )
  args = parser.parse_args(argv)
  if args.command is None:
    parser.print_help()
    return
  logs.setup_logging()
  if args.command == "dump":
    if args.refresh:
      try:
        refresh_dump(args.kind, args.format)
      except (OSError, TypeError, ValueError) as error:
        parser.exit(2, f"daedalus: fresh dump failed: {error}\n")
      return
    store.migrate()
    if args.kind in ("catalog", "all"):
      discovery.dump(file_format=args.format)
    if args.kind in ("models", "all"):
      dump_models(args.format)
  elif args.command == "catalog":
    url = live_url()
    if url is not None:
      print(f"using the daedalus at {url}")
    try:
      asked = url is not None and ask_rebuild(url)
    except Refused as exc:
      if not args.force:
        parser.exit(
          2, f"daedalus: {exc}. Set {dashboard.DAEDALUS_MASTER_KEY} to its key.\n"
        )
      logger.warning("%s: the force flag keeps the rebuild here", exc)
      asked = False
    if asked:
      logger.info("the daedalus at %s rebuilds the catalog now", url)
    else:
      store.migrate()
      catalog.refresh()
  elif args.command == "hooks":
    if args.hooks_command is None:
      hooks_command.print_help()
      return
    code = {
      "update": hooks_update,
      "list": hooks_list,
      "verify": hooks_verify,
    }[args.hooks_command]()
    if code:
      parser.exit(code)
  else:
    if dashboard.master() is None:
      parser.exit(
        2,
        f"daedalus: set {dashboard.DAEDALUS_MASTER_KEY}: {keys.MIN_LENGTH} or more characters, no spaces\n",
      )
    if not store.readable():
      logger.warning("the model store is not readable: it goes aside and is rebuilt")
      store.set_aside()
    store.migrate()
    try:
      api.apply_settings(settings.load())
      if config.DEFAULT_PATH.exists():
        config.load_config()
    except (settings.SettingsError, yaml.YAMLError) as exc:
      parser.exit(2, f"daedalus: {exc}\n")
    api.CATALOG_INITIAL = args.catalog or not store.has_store()
    api.CATALOG_REFRESH = catalog.refresh
    api.CATALOG_REBUILD_CACHED = catalog.rebuild_cached
    api.LIMIT_CHECKS = True
    import uvicorn

    host = None if api.HOST in ("0.0.0.0", "::") else api.HOST
    uvicorn.run(api.app, host=host, port=args.port, log_config=None, access_log=False)
