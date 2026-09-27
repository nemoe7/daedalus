"""The `daedalus` command: serve, catalog, dump and key."""

import argparse
import logging

import yaml

from daedalus import api, catalog, keys, settings

logger = logging.getLogger("daedalus")
KEY_OFF = "off"


def set_key(value: str) -> None:
  """Store a new, custom, or no local API key. Show a new key once."""
  if value == KEY_OFF:
    keys.save_hash(catalog.MODELS_DB, None)
    logger.info("removed the local API key; the router accepts all requests")
    return
  key = value or keys.generate()
  keys.save_hash(catalog.MODELS_DB, keys.digest(key))
  if not value:
    print(key)
  logger.info("stored the local API key hash in %s", catalog.MODELS_DB)


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
  key = commands.add_parser(
    "key",
    help=f"set the local API key: a new key without KEY, your KEY, or '{KEY_OFF}' to remove it",
  )
  key.add_argument("key", nargs="?", default="", metavar="KEY")
  args = parser.parse_args(argv)
  if args.command is None:
    parser.print_help()
    return
  if (
    args.command == "key" and args.key not in ("", KEY_OFF) and not keys.valid(args.key)
  ):
    key.error(f"a custom key needs {keys.MIN_LENGTH} or more characters and no spaces")
  api.setup_logging()
  if args.command == "key":
    set_key(args.key)
  elif args.command == "dump":
    catalog.dump()
  elif args.command == "catalog":
    catalog.refresh()
  else:
    try:
      api.apply_settings(settings.load())
    except (settings.SettingsError, yaml.YAMLError) as exc:
      parser.exit(2, f"daedalus: {exc}\n")
    if args.catalog or not catalog.has_store():
      catalog.refresh()
    import uvicorn

    uvicorn.run(
      api.app, host=api.HOST, port=args.port, log_config=None, access_log=False
    )
