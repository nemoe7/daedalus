"""The `daedalus` command: serve, catalog and dump."""

import argparse
import logging

import yaml

from daedalus import catalog, config, dashboard, store
from daedalus.catalog import discovery
from daedalus.config import settings
from daedalus.server import api, logs
from daedalus.store import keys

logger = logging.getLogger("daedalus")


def run(argv: list[str] | None = None) -> None:
  """Entry point for `daedalus`."""
  parser = argparse.ArgumentParser(
    prog="daedalus", description="OpenAI-compatible router for the providers."
  )
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
  commands.add_parser(
    "dump", help="write the raw model list of each provider to .daedalus-state/dump"
  )
  args = parser.parse_args(argv)
  if args.command is None:
    parser.print_help()
    return
  logs.setup_logging()
  if args.command == "dump":
    discovery.dump()
  elif args.command == "catalog":
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
    api.LIMIT_CHECKS = True
    import uvicorn

    uvicorn.run(
      api.app, host=api.HOST, port=args.port, log_config=None, access_log=False
    )
