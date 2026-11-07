"""Survey schema validation, deterministic boolean checks, and SHA-256 answer grading."""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any

SPEC_PATH = Path(__file__).resolve().parents[2] / "references" / "survey.json"
SPEC: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

HASHES = {
  "A2P6R": "30b4cb39218679f94c996a87c68e9bab23c0cd663e4edb8e7579ee9c022b727d",
  "M2A6Y": "ec73ad585772530cbd4f68147276e562d01a147e86721338eae51ac56dd7e8c7",
}

S2E5P_SUBSTRING_HASHES = {
  "first": (8, "5ef0bd520fc8ba5fc57c2ec93dd245d31b532f37cb764a0179bc8d36f797e005"),
  "last": (8, "6c52e900202e3a42ab6e2d6e57b42b87bc095b126778a883f26cb27ff666b30a"),
  "org": (6, "93fddcb2646b90fe0ef33a771693e605056a27b2081769bf27cb683203cb4f0b"),
  "role": (5, "e88d7c6d39d7b6401500b583553537d3d914ca32b3b719cc8b0e202c3d8a3f90"),
}

DETERMINISTIC_IDS = frozenset({"R3S0N", "V1S0N", "S0R7D"})

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
IGNORED_IMAGE_DIRS = frozenset(
  {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".local",
    ".npm",
    ".arena",
    "__pycache__",
    "node_modules",
    "arena-state",
    "dist",
    "build",
    "out",
    "target",
    "coverage",
    "Unselected files",
  }
)


def one_pixel_png() -> bytes:
  """Return the bytes of a 1x1 transparent PNG built without external dependencies."""

  def chunk(code: bytes, payload: bytes) -> bytes:
    body = code + payload
    return (
      struct.pack(">I", len(payload))
      + body
      + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    )

  header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
  return (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", header)
    + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
    + chunk(b"IEND", b"")
  )


def temp_image() -> str:
  """Write a 1x1 PNG outside the repo and return its path."""
  path = Path(tempfile.mkdtemp(prefix="steer-survey-")) / "survey.png"
  path.write_bytes(one_pixel_png())
  return str(path)


def smallest_image(root: str | Path = ".") -> str:
  """Return the relative path of the smallest image in root, or a temp 1x1 PNG."""
  base = Path(root).resolve()
  if not base.is_dir():
    return temp_image()
  best: tuple[tuple[int, int, str], str] | None = None
  for current, dirs, files in os.walk(base):
    dirs[:] = sorted(name for name in dirs if name not in IGNORED_IMAGE_DIRS)
    current_path = Path(current)
    for name in files:
      if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
        continue
      candidate = current_path / name
      try:
        if candidate.is_symlink() or not candidate.is_file():
          continue
        size = candidate.stat().st_size
      except OSError:
        continue
      if size <= 0:
        continue
      rel = candidate.relative_to(base)
      hidden = int(any(part.startswith(".") for part in rel.parts))
      key = (size, hidden, rel.as_posix())
      if best is None or key < best[0]:
        best = (key, rel.as_posix())
  return best[1] if best is not None else temp_image()


def template() -> list[dict[str, Any]]:
  """Return the unfilled JSON submission template with no null values."""
  rows: list[dict[str, Any]] = []
  for question in SPEC["questions"]:
    qid = question["id"]
    is_det = bool(question.get("deterministic")) or qid in DETERMINISTIC_IDS
    answer = {
      name: dict(SPEC["quantity"]["unknown"])
      if field == "quantity"
      else False
      if field == "boolean"
      else SPEC["unknown_value"]
      for name, field in question["answer_fields"].items()
    }
    row: dict[str, Any] = {"id": qid, "answer": answer}
    if not is_det:
      row["confidence"] = 0
    rows.append(row)
  return rows


def _reject_nulls(value: Any, path: str = "payload") -> None:
  if value is None:
    raise ValueError(f"{path} must not be null; only 'unknown' is allowed")
  if isinstance(value, dict):
    for k, v in value.items():
      _reject_nulls(v, f"{path}.{k}")
  elif isinstance(value, list):
    for idx, item in enumerate(value):
      _reject_nulls(item, f"{path}[{idx}]")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
  result: dict[str, Any] = {}
  for key, value in items:
    if key in result:
      raise ValueError("Duplicate JSON field")
    result[key] = value
  return result


def _invalid_constant(value: str) -> Any:
  raise ValueError("JSON numbers must be finite")


def _parse_quantity(value: Any, path: str) -> dict[str, Any]:
  _reject_nulls(value, path)
  if isinstance(value, str) and value.strip().casefold() == "unknown":
    return {"operator": "unknown", "value": "unknown"}
  if not isinstance(value, dict) or set(value) != {"operator", "value"}:
    raise ValueError(f"{path} requires operator and value (or 'unknown')")
  operator, number = value["operator"], value["value"]
  if (
    not isinstance(operator, str)
    or operator.casefold() not in SPEC["quantity"]["operators"]
  ):
    raise ValueError(f"{path}.operator is invalid")
  operator = operator.casefold()
  if operator == "unknown":
    if not isinstance(number, str) or number.strip().casefold() != "unknown":
      raise ValueError(f"{path}.value must be 'unknown' when operator is 'unknown'")
    return {"operator": "unknown", "value": "unknown"}
  if type(number) not in (int, float) or (
    isinstance(number, float) and not math.isfinite(number)
  ):
    raise ValueError(f"{path}.value must be a finite number")
  if isinstance(number, float) and number.is_integer():
    number = int(number)
  return {"operator": operator, "value": number}


def records(text: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
  """Parse and validate question records by ID."""
  rows = json.loads(text, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
  _reject_nulls(rows, "survey")
  expected = [question["id"] for question in SPEC["questions"]]
  hint = (
    "run bare 'steer survey' or read references/survey.json for the question schema"
  )
  if type(rows) is not list:
    raise ValueError(
      f"Survey payload must be a JSON array of {len(expected)} question records ({hint})"
    )
  by_id: dict[str, dict[str, Any]] = {}
  for row in rows:
    if type(row) is not dict or type(row.get("id")) is not str:
      raise ValueError("Each record requires an id and answer object")
    qid = row["id"]
    keys = list(row.keys())
    if qid in DETERMINISTIC_IDS:
      if keys not in (["id", "answer"], ["id", "answer", "confidence"]):
        raise ValueError(f"{qid} requires keys id and answer")
    else:
      if keys != SPEC["response_order"]:
        raise ValueError(f"{qid} requires id, answer, confidence in that order")
    if qid in by_id:
      raise ValueError(f"Duplicate record for question {qid}")
    by_id[qid] = row
  unknown = sorted(set(by_id) - set(expected))
  missing = [key for key in expected if key not in by_id]
  if len(rows) != len(expected) or unknown or missing:
    parts = [f"received {len(rows)} of {len(expected)} expected question records"]
    if unknown:
      parts.append(f"Unknown question IDs: {', '.join(unknown)}")
    if missing:
      parts.append(f"Missing question IDs: {', '.join(missing)}")
    raise ValueError(f"Survey questions mismatch ({'; '.join(parts)}; {hint})")
  return by_id, expected


def submission_order(text: str) -> bool:
  """Return True when the submitted records follow references/survey.json order."""
  by_id, expected = records(text)
  return list(by_id) == expected


def loads(text: str) -> list[dict[str, Any]]:
  """Validate and normalize a survey JSON array submission."""
  by_id, _expected = records(text)
  normalized: list[dict[str, Any]] = []
  for question in SPEC["questions"]:
    qid = question["id"]
    is_det = bool(question.get("deterministic")) or qid in DETERMINISTIC_IDS
    row = by_id[qid]
    answer = row["answer"]
    if not isinstance(answer, dict) or set(answer) != set(question["answer_fields"]):
      raise ValueError(f"{qid}.answer fields do not match the schema")
    clean: dict[str, Any] = {}
    known_count = 0
    for name, field in question["answer_fields"].items():
      value = answer[name]
      _reject_nulls(value, f"{qid}.{name}")
      if field == "quantity":
        q_val = _parse_quantity(value, f"{qid}.{name}")
        known_count += int(q_val["operator"] != "unknown")
        clean[name] = q_val
      elif field == "boolean":
        if isinstance(value, str) and value.strip().casefold() == "unknown":
          clean[name] = "unknown"
        elif type(value) is bool:
          clean[name] = value
          known_count += 1
        else:
          raise ValueError(
            f"{qid}.{name} must be a boolean (true or false) or 'unknown'"
          )
      else:
        if not isinstance(value, str) or not value.strip():
          raise ValueError(f"{qid}.{name} must be a non-empty string or 'unknown'")
        folded = value.strip().casefold()
        known_count += int(folded != "unknown")
        clean[name] = folded
    if known_count not in (0, len(question["answer_fields"])):
      raise ValueError(
        f"{qid}.answer must fill every field or set all fields to unknown"
      )
    known = known_count > 0
    if is_det:
      confidence: float | int | None = None
    else:
      raw_conf = row["confidence"]
      if type(raw_conf) not in (int, float) or not 0 <= raw_conf <= 1:
        raise ValueError(f"{qid}.confidence must be a number from 0 to 1")
      if not known and raw_conf != 0:
        raise ValueError(f"{qid}.confidence must be zero when all values are unknown")
      if known and raw_conf <= 0:
        raise ValueError(
          f"{qid}.confidence must be greater than zero when an answer is supplied"
        )
      confidence = raw_conf
    normalized.append(
      {"id": qid, "answer": clean, "confidence": confidence, "known": known}
    )
  return normalized


def _has_hashed_substring(text: str, salt: str, length: int, digest: str) -> bool:
  folded = text.casefold()
  if len(folded) < length:
    return False
  prefix = f"S2E5P:{salt}:".encode()
  for start in range(len(folded) - length + 1):
    candidate = hashlib.sha256(
      prefix + folded[start : start + length].encode()
    ).hexdigest()
    if candidate == digest:
      return True
  return False


def _grade_s2e5p(answer: dict[str, Any]) -> bool:
  full_name = str(answer.get("full_name") or "")
  organization = str(answer.get("organization") or "")
  role = str(answer.get("role") or "")
  first_len, first_hash = S2E5P_SUBSTRING_HASHES["first"]
  last_len, last_hash = S2E5P_SUBSTRING_HASHES["last"]
  org_len, org_hash = S2E5P_SUBSTRING_HASHES["org"]
  role_len, role_hash = S2E5P_SUBSTRING_HASHES["role"]
  return (
    _has_hashed_substring(full_name, "first", first_len, first_hash)
    and _has_hashed_substring(full_name, "last", last_len, last_hash)
    and _has_hashed_substring(organization, "org", org_len, org_hash)
    and _has_hashed_substring(role, "role", role_len, role_hash)
  )


def grade(text: str) -> list[dict[str, Any]]:
  """Validate and grade a survey JSON array submission, appending S0R7D."""
  rows = loads(text)
  submitted = {row["id"]: row for row in json.loads(text)}
  results: list[dict[str, Any]] = []
  for row in rows:
    qid = row["id"]
    original = submitted[qid]
    result: dict[str, Any] = {
      "id": qid,
      "correct": None,
      "confidence": row["confidence"],
      "answer": original["answer"],
    }
    if not row["known"]:
      results.append(result)
      continue
    answer = dict(row["answer"])
    if qid in DETERMINISTIC_IDS:
      result["correct"] = all(val is True for val in answer.values())
      results.append(result)
      continue
    if qid == "S2E5P":
      result["correct"] = _grade_s2e5p(answer)
      results.append(result)
      continue
    canonical = json.dumps(
      answer, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256((canonical + "\0").encode()).hexdigest()
    result["correct"] = digest == HASHES.get(qid)
    results.append(result)
  ordered = submission_order(text)
  results.append(
    {
      "id": "S0R7D",
      "correct": ordered,
      "confidence": None,
      "answer": {"sorted": ordered},
    }
  )
  return results
