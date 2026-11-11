"""Survey schema validation, deterministic boolean checks, and SHA-256 answer grading."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import re
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any

SPEC_PATH = Path(__file__).resolve().parents[2] / "references" / "survey.json"
QUESTIONS_PATH = (
  Path(__file__).resolve().parents[2] / "references" / "survey-questions.txt"
)


def parse_questions_text(text: str) -> list[tuple[str, str]]:
  """Parse ID-prefixed survey question lines from the shared plain-text source."""
  pairs: list[tuple[str, str]] = []
  seen: set[str] = set()
  for raw_line in text.splitlines():
    line = raw_line.strip()
    if not line:
      continue
    qid, sep, prompt = line.partition(":")
    qid = qid.strip()
    prompt = prompt.strip()
    if not sep or not qid or not prompt:
      raise ValueError(f"Invalid survey question line: {raw_line!r}")
    if qid in seen:
      raise ValueError(f"Duplicate survey question ID in text source: {qid}")
    seen.add(qid)
    pairs.append((qid, prompt))
  return pairs


def load_spec(
  spec_path: Path = SPEC_PATH,
  questions_path: Path = QUESTIONS_PATH,
) -> dict[str, Any]:
  """Load the survey schema and attach question text from the shared text file."""
  spec: dict[str, Any] = json.loads(spec_path.read_text(encoding="utf-8"))
  pairs = parse_questions_text(questions_path.read_text(encoding="utf-8"))
  expected_ids = [question["id"] for question in spec["questions"]]
  found_ids = [qid for qid, _ in pairs]
  if found_ids != expected_ids:
    raise ValueError(
      f"Survey question ID mismatch between {spec_path.name} ({expected_ids}) "
      f"and {questions_path.name} ({found_ids})"
    )
  by_id = dict(pairs)
  spec["questions"] = [
    {**question, "question": by_id[question["id"]]} for question in spec["questions"]
  ]
  return spec


SPEC: dict[str, Any] = load_spec()

SUBSTRING_HASHES: dict[str, dict[str, tuple[int, str]]] = {
  "S2E5P": {
    "first_name": (
      8,
      "01138dd6e45cfa7b3f641faabe8f5871f5a954fdc0f5a69e2ce90088b12e73b6",
    ),
    "last_name": (
      8,
      "1490cb8487063a4603535301a97d149cd9398bfdf1094778d0a73795018b2699",
    ),
    "organization": (
      6,
      "3fcdf687a16e44890da4f8debac0d8296ec64dd977a001c1dff474dc8623c900",
    ),
    "role": (
      5,
      "44cbbafc032d2221daaee9bc8717990ebd2e4748394b3d8452d21e1010e9ce4f",
    ),
  },
  "A2P6R": {
    "command_name": (
      6,
      "2b9b60b448c7ebb523cacbcd2b5577433944dd1a21c94969e3d83994bf4636c2",
    ),
    "concurrency_option": (
      12,
      "e21864bfe015399cf07e25bd8b2cc79faee75338f72c63911f7ec25ff3535ca2",
    ),
  },
  "M2A6Y": {
    "data_center": (
      8,
      "22231c86a43dccfc0ea60210097e44412bd320ed1b9ef50f57bbbfd800f5773c",
    ),
  },
}

FORBIDDEN_TOKEN_HASHES: dict[str, dict[str, frozenset[str]]] = {
  "M2A6Y": {
    "data_center": frozenset(
      {
        "b004327ee707b987a2eabe35c024a1c9009a012e8823c14e85e809674b7988f4",
        "5ff19d4e9f2e36c29f948221fc7ea570f4c02c42cf6577f56f1deb0f51a973fc",
        "d540eafa7bb5937e5da7dfdbeebc37936acb3cb466c8fe7a2ee5d6ae0107535c",
        "b777ecab812571092cbcb83407a2f63d73c183de263ad0a6adf0f3fac60523e9",
      }
    ),
  },
}

QUANTITY_VALUE_HASHES: dict[str, dict[str, str]] = {
  "M2A6Y": {
    "power_capacity_mw": (
      "000f1386200bc1999d5080dc0190ec3bc69554c8bfd0b27f3d0fb8e98ca07d82"
    ),
    "nvidia_gpu_count": (
      "fef763af2d6dc4f7cb8acd48aa7fc55167e438f957a58bef885700cd3c3ef89a"
    ),
  },
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


def _has_hashed_substring(
  text: str, qid: str, field: str, length: int, digest: str
) -> bool:
  folded = text.casefold()
  if len(folded) < length:
    return False
  prefix = f"{qid}:{field}:".encode()
  for start in range(len(folded) - length + 1):
    candidate = hashlib.sha256(
      prefix + folded[start : start + length].encode()
    ).hexdigest()
    if candidate == digest:
      return True
  return False


def _has_forbidden_token(
  text: str, qid: str, field: str, digests: frozenset[str]
) -> bool:
  tokens = re.findall(r"[a-z0-9]+", text.casefold())
  candidates = list(tokens) + [f"{a} {b}" for a, b in itertools.pairwise(tokens)]
  prefix = f"{qid}:{field}:forbid:".encode()
  return any(
    hashlib.sha256(prefix + item.encode()).hexdigest() in digests for item in candidates
  )


def _matches_quantity_value(
  q_val: dict[str, Any], qid: str, field: str, digest: str
) -> bool:
  if q_val.get("operator") == "unknown":
    return False
  prefix = f"{qid}:{field}:".encode()
  raw = str(q_val.get("value")).encode()
  return hashlib.sha256(prefix + raw).hexdigest() == digest


def _grade_hashed_question(qid: str, answer: dict[str, Any]) -> bool:
  for field, (length, digest) in SUBSTRING_HASHES.get(qid, {}).items():
    text = str(answer.get(field) or "")
    if not _has_hashed_substring(text, qid, field, length, digest):
      return False
    forbid = FORBIDDEN_TOKEN_HASHES.get(qid, {}).get(field)
    if forbid and _has_forbidden_token(text, qid, field, forbid):
      return False
  for field, digest in QUANTITY_VALUE_HASHES.get(qid, {}).items():
    q_val = answer.get(field)
    if not isinstance(q_val, dict) or not _matches_quantity_value(
      q_val, qid, field, digest
    ):
      return False
  return bool(qid in SUBSTRING_HASHES or qid in QUANTITY_VALUE_HASHES)


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
    result["correct"] = _grade_hashed_question(qid, answer)
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
