"""Collect fresh dump files and write validated output to the client folder."""

import json
import logging
import os
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory

from daedalus import catalog, store
from daedalus.catalog import discovery, enrichment

logger = logging.getLogger("daedalus")
KINDS = {"catalog", "models", "enrichments", "all"}
FORMATS = {"json", "csv"}


def dump_models(
  file_format: str, folder: Path | None = None, since: float | None = None
) -> Path:
  """Write stored model rows with parsed effort lists to JSON or CSV."""
  stored, built_at = (
    store.stored_catalog()
    if file_format == "json" or since is not None
    else (store.stored_rows(), None)
  )
  if since is not None and (built_at is None or built_at < since):
    raise ValueError("the model store was not rebuilt")
  rows = [
    {
      **row,
      "supported_efforts": store.effort_list(row.get("supported_efforts")) or None,
    }
    for row in stored
  ]
  folder = discovery.DUMP_DIR if folder is None else folder
  folder.mkdir(parents=True, exist_ok=True)
  for extension in FORMATS:
    (folder / f"models.{extension}").unlink(missing_ok=True)
  path = folder / f"models.{file_format}"
  if file_format == "json":
    wrapped = discovery.dump_record(rows, "built_at", built_at)
    path.write_text(json.dumps(wrapped, indent=2, ensure_ascii=False) + "\n", "utf-8")
  else:
    discovery.write_csv(path, rows)
  logger.info("wrote %d available models to %s", len(rows), path)
  return path


def collect(kind: str, file_format: str, since: float) -> dict[str, str]:
  """Capture all requested fresh files without changing the dump folder."""
  if catalog.LAST_FAILED:
    raise ValueError("a required provider fetch failed")
  with TemporaryDirectory() as temporary:
    folder = Path(temporary)
    paths = []
    if kind in {"catalog", "all"}:
      paths.extend(discovery.dump(folder=folder, file_format=file_format, since=since))
    if kind in {"models", "all"}:
      paths.append(dump_models(file_format, folder, since))
    if kind in {"enrichments", "all"}:
      paths.extend(enrichment.dump(folder=folder, file_format=file_format, since=since))
    files = {}
    for path in paths:
      with path.open(encoding="utf-8", newline="") as source:
        files[path.name] = source.read()
    return files


def write(files: object, kind: str, file_format: str) -> None:
  """Validate every output name and stage every file before replacing existing dumps."""
  if not isinstance(files, dict):
    raise TypeError("the export has no file mapping")
  model_name = f"models.{file_format}"
  if kind in {"models", "all"} and model_name not in files:
    raise ValueError("the export has no models file")
  if kind == "enrichments" and not files:
    raise ValueError("the export has no enrichment files")
  for name, content in files.items():
    stem = Path(name).stem
    is_enrichment = discovery.is_enrichment_stem(stem)
    if (
      not isinstance(name, str)
      or not name
      or any(ord(char) < 32 or char in '\\/:*?"<>|' for char in name)
      or Path(name).name != name
      or PureWindowsPath(name).is_reserved()
      or Path(name).suffix != f".{file_format}"
      or (kind == "models" and name != model_name)
      or (kind == "catalog" and (name == model_name or is_enrichment))
      or (kind == "enrichments" and not is_enrichment)
      or not isinstance(content, str)
    ):
      raise ValueError("the export contains an invalid file")
  folder = discovery.DUMP_DIR
  folder.mkdir(parents=True, exist_ok=True)
  with TemporaryDirectory(dir=folder) as temporary:
    staging = Path(temporary)
    for name, content in files.items():
      (staging / name).write_text(content, encoding="utf-8", newline="")
    for name in files:
      os.replace(staging / name, folder / name)
    for old in folder.iterdir():
      is_enrichment = discovery.is_enrichment_stem(old.stem)
      selected = (
        kind == "all"
        or (kind == "models" and old.stem == "models")
        or (kind == "enrichments" and is_enrichment)
        or (kind == "catalog" and old.stem != "models" and not is_enrichment)
      )
      if (
        old.is_file()
        and old.suffix in {".json", ".csv"}
        and selected
        and old.name not in files
      ):
        old.unlink()
