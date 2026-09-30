"""The `daedalus` command: serve, catalog and dump."""

import argparse
import json
import logging

import yaml

from daedalus import __version__, catalog, config, dashboard, store
from daedalus.catalog import discovery
from daedalus.config import settings
from daedalus.server import api, logs
from daedalus.store import keys

logger = logging.getLogger("daedalus")


def dump_models(file_format: str) -> None:
  """Write the available model rows to `models.json` or `models.csv`."""
  rows = store.model_rows()
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
  else:
    if dashboard.master() is None:
      parser.exit(
        2,
        f"daedalus: set {dashboard.MASTER_ENV}: {keys.MIN_LENGTH} or more characters, no spaces\n",
      )
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

    uvicorn.run(
      api.app, host=api.HOST, port=args.port, log_config=None, access_log=False
    )
