"""The `daedalus` command: serve, catalog, dump and hooks."""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

import yaml

from daedalus import __version__, catalog, config, dashboard, store
from daedalus.catalog import discovery
from daedalus.config import remote, settings
from daedalus.server import api, logs
from daedalus.store import keys

logger = logging.getLogger("daedalus")
# The load check of `daedalus hooks verify` runs a file in its own process, with this limit.
IMPORT_TIMEOUT = 10.0
IMPORT_CODE = (
  "import sys; from pathlib import Path; from daedalus.providers.hooks import load; "
  "sys.exit(0 if load(Path(sys.argv[1])) else 1)"
)


def dump_models(file_format: str) -> None:
  """Write every stored model row to `models.json` or `models.csv`."""
  rows = store.stored_rows()
  folder = discovery.DUMP_DIR
  folder.mkdir(parents=True, exist_ok=True)
  for extension in ("json", "csv"):
    (folder / f"models.{extension}").unlink(missing_ok=True)
  path = folder / f"models.{file_format}"
  if file_format == "json":
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", "utf-8")
  else:
    discovery.write_csv(path, rows)
  logger.info("wrote %d available models to %s", len(rows), path)


def hook_name(name: str) -> str:
  """The file name inside the hooks folder, from a bare name or a `hooks/...` path."""
  return name.strip().removeprefix("hooks/")


def import_problem(path: Path) -> str | None:
  """The last line of the load error of a hook file, or None when it loads.

  The check runs the file in its own process, with a timeout, the way the loader runs it.
  """
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


def hooks_pin(names: list[str], urls: list[str]) -> int:
  """Print the sha256 of each hook file and of each URL, and write the lock file."""
  pins = remote.read_lock()
  bad = 0
  for name in names or sorted(remote.on_disk()):
    plain = hook_name(name)
    if not remote.NAME.fullmatch(plain):
      print(f"{'refused':<9} {name}")
      bad = 1
      continue
    try:
      body = (remote.FOLDER / plain).read_bytes()
    except OSError:
      print(f"{'missing':<9} {plain}")
      bad = 1
      continue
    found = remote.digest(body)
    pins[plain] = found
    print(f"{found}  {plain}")
  for url in urls:
    body = remote.fetch(url)
    if body is None:
      print(f"{'failed':<9} {url}")
      bad = 1
      continue
    print(f"{remote.digest(body)}  {url}")
  remote.write_lock(pins)
  return bad


def hooks_verify() -> int:
  """Check each hook file against the lock file, the settings pins and the loader."""
  try:
    entries = settings.load()["remote_hooks"]
  except settings.SettingsError as exc:
    print(f"settings: {exc}")
    return 2
  lock = remote.read_lock()
  disk = remote.on_disk()
  remote_pins: dict[str, str] = {}
  for entry in entries:
    if isinstance(entry, dict) and remote.NAME.fullmatch(remote.file_name(entry)):
      remote_pins[remote.file_name(entry)] = str(entry.get("sha256", "")).lower()
  failed = 0
  for name in sorted(set(disk) | set(lock) | set(remote_pins)):
    found = disk.get(name)
    notes: list[str] = []
    bad = warned = unpinned = False
    if name in lock:
      if found is None:
        notes.append("in the lock file, not in the folder")
        bad = True
      elif found != lock[name]:
        notes.append(
          f"changed: the lock holds {lock[name][:12]}, the disk holds {found[:12]}"
        )
        bad = True
      else:
        notes.append("pin ok")
    if name in remote_pins:
      if found is None:
        notes.append("downloads at the next start")
      elif found != remote_pins[name]:
        notes.append("drift: the bytes differ from the settings pin")
        bad = True
      else:
        notes.append("settings pin ok")
    if found is not None and name not in lock and name not in remote_pins:
      notes.append("no pin recorded; run `daedalus hooks pin`")
      unpinned = True
    if found is not None:
      problem = import_problem(remote.FOLDER / name)
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
    help=f"start the router on {api.HOST}:PORT; build a missing model store first",
  )
  serve.add_argument(
    "port",
    nargs="?",
    default=api.PORT,
    type=int,
    help=f"default {api.PORT}, or DAEDALUS_PORT",
  )
  serve.add_argument(
    "--catalog", action="store_true", help="rebuild the model store first"
  )
  commands.add_parser(
    "catalog", help="discover provider models and rebuild the model store"
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
  hooks = commands.add_parser("hooks", help="pin the hook files and check them on disk")
  hooks_commands = hooks.add_subparsers(dest="hooks_command", metavar="COMMAND")
  pin = hooks_commands.add_parser(
    "pin", help="print the sha256 of each hook file and write config/hooks.lock.json"
  )
  pin.add_argument(
    "names", nargs="*", help="hook file names or hooks/... paths; default: every file"
  )
  pin.add_argument(
    "--url",
    action="append",
    default=[],
    dest="urls",
    help="print the sha256 of the bytes at this URL, for a remote_hooks entry",
  )
  hooks_commands.add_parser(
    "verify", help="check each hook file against its pins, and load it with a warning"
  )
  args = parser.parse_args(argv)
  if args.command is None:
    parser.print_help()
    return
  logs.setup_logging()
  if args.command == "dump":
    store.migrate()
    if args.kind in ("catalog", "all"):
      discovery.dump(file_format=args.format)
    if args.kind in ("models", "all"):
      dump_models(args.format)
  elif args.command == "catalog":
    store.migrate()
    catalog.refresh()
  elif args.command == "hooks":
    if args.hooks_command is None:
      hooks.print_help()
      return
    code = (
      hooks_pin(args.names, args.urls)
      if args.hooks_command == "pin"
      else hooks_verify()
    )
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
    if args.catalog or not store.has_store():
      catalog.refresh()
    api.CATALOG_REFRESH = catalog.refresh
    api.CATALOG_REBUILD_CACHED = catalog.rebuild_cached
    api.LIMIT_CHECKS = True
    import uvicorn

    host = None if api.HOST in ("0.0.0.0", "::") else api.HOST
    uvicorn.run(api.app, host=host, port=args.port, log_config=None, access_log=False)
