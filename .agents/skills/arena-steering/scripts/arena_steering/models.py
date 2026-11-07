"""Domain models and validation rules for Arena Steering."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

MESSAGE_ID_RE = re.compile(r"^[0-9a-f]{6}$")
ENTITY_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

MESSAGE_STATUSES = ("sent", "seen", "acked")
TASK_TYPES = ("report", "code", "docs", "ops", "misc")
TASK_STATUSES = ("upcoming", "finished")
TASK_PRIORITIES = ("P1", "P2", "P3", "P4", "P5")
TASK_SIZES = (1, 2, 3, 5, 8, 13, 21, 34)
REPORT_STATUSES = ("published", "answered", "dismissed")
REPORT_FIELD_TYPES = ("choice", "checkbox", "text")

TASK_DETAIL_MAX_LEN = 120
OPTION_MIN_COUNT = 1
OPTION_MAX_COUNT = 20
OPTION_MAX_LEN = 200

_STATUS_RANK = {name: idx for idx, name in enumerate(MESSAGE_STATUSES)}
_REPORT_STATUS_RANK = {name: idx for idx, name in enumerate(REPORT_STATUSES)}


def kebab_slug(text: str, used: set[str] | None = None, max_len: int = 64) -> str:
  """Convert label text into a kebab-case ID with trailing hyphens stripped."""
  cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
  if max_len > 0 and len(cleaned) > max_len:
    cleaned = cleaned[:max_len].rstrip("-")
  base = cleaned or "field"
  if used is None or base not in used:
    return base
  suffix = 2
  while f"{base}-{suffix}" in used:
    suffix += 1
  return f"{base}-{suffix}"


def validate_hex_id(value: str, label: str = "Message ID") -> str:
  """Require a strict 6-character lowercase hex identifier."""
  if not isinstance(value, str) or not MESSAGE_ID_RE.fullmatch(value):
    raise ValueError(f"{label} must be 6 lowercase hex characters: {value!r}")
  return value


def validate_entity_id(value: str, label: str = "ID") -> str:
  """Require a non-empty kebab-case identifier with no trailing hyphen."""
  if not isinstance(value, str) or not ENTITY_ID_RE.fullmatch(value):
    raise ValueError(f"{label} must be kebab-case: {value!r}")
  return value


def can_advance_message_status(current: str, target: str) -> bool:
  """Check whether a message or answer status transition moves forward."""
  if current not in _STATUS_RANK or target not in _STATUS_RANK:
    return False
  return _STATUS_RANK[target] >= _STATUS_RANK[current]


def can_advance_report_status(current: str, target: str) -> bool:
  """Check whether a report status transition is valid."""
  if current not in _REPORT_STATUS_RANK or target not in _REPORT_STATUS_RANK:
    return False
  if target == "published":
    return True
  return _REPORT_STATUS_RANK[target] >= _REPORT_STATUS_RANK[current]


@dataclass(slots=True)
class Attachment:
  """File metadata attached to a steering message."""

  name: str
  path: str
  size_bytes: int = 0
  present: bool = True

  def __post_init__(self) -> None:
    if not self.name or not isinstance(self.name, str):
      raise ValueError("Attachment name is required")
    if not self.path or not isinstance(self.path, str):
      raise ValueError("Attachment path is required")
    if int(self.size_bytes) < 0:
      raise ValueError("Attachment size cannot be negative")
    self.size_bytes = int(self.size_bytes)
    self.present = bool(self.present)

  def to_dict(self) -> dict[str, Any]:
    return {
      "name": self.name,
      "path": self.path,
      "size_bytes": self.size_bytes,
      "present": self.present,
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> Attachment:
    return cls(
      name=str(data["name"]),
      path=str(data["path"]),
      size_bytes=int(data.get("size_bytes", 0)),
      present=bool(data.get("present", True)),
    )


@dataclass(slots=True)
class Message:
  """Steering inbox message with a 6-character hex ID and status ladder."""

  id: str
  body: str
  hidden: bool = False
  blocking: bool = True
  attachments: list[Attachment] = field(default_factory=list)
  parent_ids: list[str] = field(default_factory=list)
  parent_refs: list[dict[str, str]] = field(default_factory=list)
  status: str = "sent"
  acks: list[str] = field(default_factory=list)
  replies_seen: bool = False
  modified_at: str = ""
  type: str = "message"
  violation_type: str = ""
  violation_clause_file: str = ""
  answer_data: dict[str, Any] = field(default_factory=dict)

  def __post_init__(self) -> None:
    validate_hex_id(self.id, "Message.id")
    if not isinstance(self.body, str):
      raise TypeError("Message.body must be a string")
    self.hidden = bool(self.hidden)
    self.blocking = bool(self.blocking)
    if self.status not in MESSAGE_STATUSES:
      raise ValueError(f"Invalid Message.status: {self.status!r}")
    self.attachments = [
      item if isinstance(item, Attachment) else Attachment.from_dict(item)
      for item in self.attachments
    ]
    if not isinstance(self.parent_ids, list):
      raise TypeError("Message.parent_ids must be a list of message IDs")
    cleaned_parent_ids: list[str] = []
    for parent_id in self.parent_ids:
      validate_hex_id(parent_id, "Message.parent_ids")
      if parent_id not in cleaned_parent_ids:
        cleaned_parent_ids.append(parent_id)
    if not isinstance(self.parent_refs, list):
      raise TypeError("Message.parent_refs must be a list")
    seen_refs: set[tuple[str, str]] = set()
    cleaned_refs: list[dict[str, str]] = []
    for raw in [
      *self.parent_refs,
      *({"type": "message", "id": pid} for pid in cleaned_parent_ids),
    ]:
      if not isinstance(raw, dict) or set(raw) != {"type", "id"}:
        raise TypeError("Message.parent_refs entries need type and id")
      parent_type, parent_id = raw["type"], raw["id"]
      if parent_type not in {"message", "task", "report"}:
        raise ValueError("Invalid parent type")
      if parent_type == "message":
        validate_hex_id(parent_id, "Message.parent_refs")
        if parent_id == self.id:
          raise ValueError("Message cannot reply to itself")
      else:
        validate_entity_id(parent_id, "Message.parent_refs")
      key = (parent_type, parent_id)
      if key not in seen_refs:
        seen_refs.add(key)
        cleaned_refs.append({"type": parent_type, "id": parent_id})
    self.parent_refs = cleaned_refs
    self.parent_ids = [ref["id"] for ref in cleaned_refs if ref["type"] == "message"]
    if not isinstance(self.acks, list) or any(
      not isinstance(ack, str) or not ack.strip() for ack in self.acks
    ):
      raise ValueError("Message.acks must be a list of non-empty Markdown strings")
    self.acks = list(self.acks)
    self.modified_at = str(self.modified_at or "")
    if self.type not in {"message", "report_answer"}:
      raise ValueError(f"Invalid Message.type: {self.type!r}")
    if not isinstance(self.violation_type, str):
      raise TypeError("Message.violation_type must be text")
    if not isinstance(self.violation_clause_file, str):
      raise TypeError("Message.violation_clause_file must be text")
    if self.violation_type and (
      self.type != "message" or not self.hidden or not self.blocking
    ):
      raise ValueError("Violation flags must be hidden blocking messages")
    if self.violation_clause_file and not self.violation_type:
      raise ValueError("A clause file requires a violation flag")
    if not isinstance(self.answer_data, dict):
      raise TypeError("Message.answer_data must be a dictionary")
    self.answer_data = dict(self.answer_data)
    if self.type == "report_answer":
      if not self.hidden:
        raise ValueError("A report-answer message must be hidden")
      self.as_report_answer()

  def as_report_answer(self) -> ReportAnswer:
    """Project a typed answer from this message's authoritative status and replies."""
    if self.type != "report_answer":
      raise ValueError("Message is not a report answer")
    return ReportAnswer(
      id=self.id,
      report_id=str(self.answer_data.get("report_id", "")),
      values=dict(self.answer_data.get("values", {})),
      report_title=str(self.answer_data.get("report_title", "")),
      field_labels=dict(self.answer_data.get("field_labels", {})),
      orphaned=self.answer_data.get("orphaned", False),
      created_at=str(self.answer_data.get("created_at", "")),
      status=self.status,
      acks=list(self.acks),
      modified_at=self.modified_at,
    )

  @classmethod
  def from_report_answer(cls, answer: ReportAnswer) -> Message:
    """Create the hidden inbox record for a report answer."""
    lines = [f"Answer to report {answer.report_id} ({answer.report_title})"]
    for key, value in answer.values.items():
      label = answer.field_labels.get(key, key)
      text = (
        ", ".join(str(item) for item in value)
        if isinstance(value, list)
        else str(value)
      )
      lines.append(f"{label}: {text}")
    return cls(
      id=answer.id,
      body="\n".join(lines),
      hidden=True,
      type="report_answer",
      answer_data={
        "report_id": answer.report_id,
        "report_title": answer.report_title,
        "values": dict(answer.values),
        "field_labels": dict(answer.field_labels),
        "created_at": answer.created_at,
        "orphaned": answer.orphaned,
      },
      status=answer.status,
      acks=list(answer.acks),
      modified_at=answer.modified_at,
    )

  @property
  def replies_revision(self) -> str:
    """Digest the reply list for conditional seen updates."""
    return hashlib.sha256(json.dumps(self.acks).encode("utf-8")).hexdigest()

  @property
  def requires_ack(self) -> bool:
    """Return True when this message requires an acknowledgement."""
    return self.blocking and self.status != "acked"

  @property
  def is_unread(self) -> bool:
    """Return True when the message remains unread or lacks an acknowledgement."""
    if self.blocking:
      return self.status != "acked"
    return self.status == "sent"

  def to_dict(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "body": self.body,
      "hidden": self.hidden,
      "blocking": self.blocking,
      "attachments": [item.to_dict() for item in self.attachments],
      "parent_ids": list(self.parent_ids),
      "parent_refs": [dict(ref) for ref in self.parent_refs],
      "status": self.status,
      "acks": list(self.acks),
      "replies_seen": self.replies_seen,
      "replies_revision": self.replies_revision,
      "modified_at": self.modified_at,
      "type": self.type,
      "violation_type": self.violation_type,
      "violation_clause_file": self.violation_clause_file,
      "answer_data": dict(self.answer_data),
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> Message:
    return cls(
      id=str(data["id"]),
      body=str(data.get("body", "")),
      hidden=bool(data.get("hidden", False)),
      blocking=bool(data.get("blocking", True)),
      attachments=[Attachment.from_dict(raw) for raw in data.get("attachments", [])],
      parent_ids=[str(item) for item in data.get("parent_ids", [])],
      parent_refs=data.get("parent_refs", []),
      status=str(data.get("status", "sent")),
      acks=[str(item) for item in data.get("acks", [])],
      replies_seen=bool(data.get("replies_seen", False)),
      modified_at=str(data.get("modified_at", "")),
      type=str(data.get("type", "message")),
      violation_type=data.get("violation_type", ""),
      violation_clause_file=data.get("violation_clause_file", ""),
      answer_data=dict(data.get("answer_data", {})),
    )


@dataclass(slots=True, init=False)
class Task:
  """Mutable-queue task item with unified links (msg_ids, report_ids, task_ids)."""

  id: str
  title: str
  type: str
  types: list[str]
  phase_index: int
  phase_evidence: list[str | None]
  status: str
  scope: str
  details: list[str]
  links: dict[str, list[str]]
  priority: str
  priority_explicit: bool
  size: int
  insertion_order: int | None
  created_at: str
  modified_at: str
  effective_priority: str

  def __init__(
    self,
    id: str,
    title: str,
    type: str = "code",
    status: str = "upcoming",
    scope: str = "",
    details: list[str] | None = None,
    links: dict[str, list[str]] | None = None,
    *,
    size: int,
    priority: str = "P4",
    priority_explicit: bool = False,
    insertion_order: int | None = None,
    types: list[str] | None = None,
    phase_index: int | None = None,
    phase_evidence: list[str | None] | None = None,
    msg_ids: list[str] | None = None,
    report_ids: list[str] | None = None,
    task_ids: list[str] | None = None,
    deps: list[str] | None = None,
    gate_names: list[str] | None = None,
    modified_at: str = "",
    created_at: str = "",
    allow_long_details: bool = False,
  ) -> None:
    validate_entity_id(id, "Task.id")
    if not isinstance(title, str) or not title.strip():
      raise ValueError("Task.title must be a non-empty string")
    self.id = id
    self.title = title.strip()
    if priority not in TASK_PRIORITIES or not isinstance(priority, str):
      raise ValueError(f"Invalid Task.priority (P1–P5): {priority!r}")
    if not isinstance(priority_explicit, bool):
      raise TypeError(f"Invalid Task.priority_explicit: {priority_explicit!r}")
    if not isinstance(size, int) or isinstance(size, bool) or size not in TASK_SIZES:
      raise ValueError(
        f"Invalid Task.size (Fibonacci 1, 2, 3, 5, 8, 13, 21, 34): {size!r}"
      )
    if insertion_order is not None and (
      not isinstance(insertion_order, int)
      or isinstance(insertion_order, bool)
      or insertion_order < 0
    ):
      raise ValueError(f"Invalid Task.insertion_order: {insertion_order!r}")
    self.priority = priority
    self.effective_priority = priority
    self.created_at = str(created_at or modified_at or "")
    self.priority_explicit = priority_explicit
    self.size = size
    self.insertion_order = insertion_order
    if type not in TASK_TYPES:
      raise ValueError(f"Invalid Task.type: {type!r}")
    if types is not None and (
      not isinstance(types, list)
      or not types
      or any(item not in TASK_TYPES for item in types)
    ):
      raise ValueError(f"Invalid Task.types: {types!r}")
    self.types = list(types) if types is not None else [type]
    if status not in TASK_STATUSES:
      raise ValueError(f"Invalid Task.status: {status!r}")
    self.status = status
    index = len(self.types) if status == "finished" else 0
    self.phase_index = index if phase_index is None else phase_index
    if (
      not isinstance(self.phase_index, int)
      or isinstance(self.phase_index, bool)
      or not 0 <= self.phase_index <= len(self.types)
      or (self.phase_index == len(self.types)) != (status == "finished")
    ):
      raise ValueError(f"Invalid Task.phase_index: {self.phase_index!r}")
    self.type = self.types[min(self.phase_index, len(self.types) - 1)]
    evidence = [None] * len(self.types) if phase_evidence is None else phase_evidence
    if (
      not isinstance(evidence, list)
      or len(evidence) != len(self.types)
      or any(
        item is not None and (not isinstance(item, str) or not item)
        for item in evidence
      )
    ):
      raise ValueError(f"Invalid Task.phase_evidence: {evidence!r}")
    self.phase_evidence = list(evidence)
    self.scope = str(scope or "").strip()
    cleaned_details: list[str] = []
    for line in details or []:
      if not isinstance(line, str) or not line.strip():
        raise ValueError("Task detail line must be a non-empty string")
      text = line.strip()
      if len(text) > TASK_DETAIL_MAX_LEN and not allow_long_details:
        raise ValueError(
          f"Task detail exceeds {TASK_DETAIL_MAX_LEN} characters ({len(text)}): {text!r}"
        )
      cleaned_details.append(text)
    self.details = _dedupe_strings(cleaned_details)

    raw_links = dict(links) if isinstance(links, dict) else {}
    resolved_msgs = (
      msg_ids
      if msg_ids is not None
      else raw_links.get("msg_ids", raw_links.get("msg_id", []))
    )
    resolved_reports = (
      report_ids
      if report_ids is not None
      else raw_links.get("report_ids", raw_links.get("report_id", []))
    )
    resolved_tasks = (
      task_ids
      if task_ids is not None
      else (
        deps
        if deps is not None
        else raw_links.get(
          "task_ids", raw_links.get("task_id", raw_links.get("deps", []))
        )
      )
    )
    cleaned_msgs = _dedupe_strings([str(x) for x in (resolved_msgs or [])])
    cleaned_reports = _dedupe_strings([str(x) for x in (resolved_reports or [])])
    cleaned_tasks = _dedupe_strings([str(x) for x in (resolved_tasks or [])])
    resolved_gates = (
      gate_names if gate_names is not None else raw_links.get("gate_names", [])
    )
    cleaned_gates = _dedupe_strings([str(x) for x in (resolved_gates or [])])
    for gate in cleaned_gates:
      if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", gate):
        raise ValueError(f"Invalid gate name: {gate!r}")
    if self.id in cleaned_tasks:
      raise ValueError(f"Task {self.id!r} cannot depend on itself")
    self.links = {
      "msg_ids": cleaned_msgs,
      "report_ids": cleaned_reports,
      "task_ids": cleaned_tasks,
      "gate_names": cleaned_gates,
    }
    self.modified_at = str(modified_at or "")

  @property
  def msg_ids(self) -> list[str]:
    return self.links["msg_ids"]

  @msg_ids.setter
  def msg_ids(self, value: list[str]) -> None:
    self.links["msg_ids"] = _dedupe_strings([str(x) for x in value])

  @property
  def report_ids(self) -> list[str]:
    return self.links["report_ids"]

  @report_ids.setter
  def report_ids(self, value: list[str]) -> None:
    self.links["report_ids"] = _dedupe_strings([str(x) for x in value])

  @property
  def task_ids(self) -> list[str]:
    return self.links["task_ids"]

  @task_ids.setter
  def task_ids(self, value: list[str]) -> None:
    cleaned = _dedupe_strings([str(x) for x in value])
    if self.id in cleaned:
      raise ValueError(f"Task {self.id!r} cannot depend on itself")
    self.links["task_ids"] = cleaned

  @property
  def gate_names(self) -> list[str]:
    return self.links["gate_names"]

  @property
  def deps(self) -> list[str]:
    return self.links["task_ids"]

  @deps.setter
  def deps(self, value: list[str]) -> None:
    self.task_ids = value

  def to_dict(self, *, blocked: bool | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
      "id": self.id,
      "title": self.title,
      "type": self.type,
      "types": list(self.types),
      "phase_index": self.phase_index,
      "phase_evidence": list(self.phase_evidence),
      "current_phase": self.type if self.status == "upcoming" else None,
      "remaining_phases": self.types[self.phase_index + 1 :]
      if self.status == "upcoming"
      else [],
      "status": self.status,
      "scope": self.scope,
      "details": list(self.details),
      "priority": self.priority,
      "priority_explicit": self.priority_explicit,
      "size": self.size,
      "insertion_order": self.insertion_order,
      "links": {
        "msg_ids": list(self.msg_ids),
        "report_ids": list(self.report_ids),
        "task_ids": list(self.task_ids),
        "gate_names": list(self.gate_names),
      },
      "modified_at": self.modified_at,
      "created_at": self.created_at,
    }
    if blocked is not None:
      payload["blocked"] = bool(blocked)
    return payload

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> Task:
    raw_links = data.get("links")
    return cls(
      id=str(data["id"]),
      title=str(data["title"]),
      type=str(data.get("type", "code")),
      types=list(data["types"]) if "types" in data else None,
      phase_index=data.get("phase_index"),
      phase_evidence=list(data["phase_evidence"]) if "phase_evidence" in data else None,
      status=str(data.get("status", "upcoming")),
      scope=str(data.get("scope", "")),
      details=[str(item) for item in data.get("details", [])],
      priority=data.get("priority", "P3"),
      priority_explicit=data.get(
        "priority_explicit", data.get("priority", "P3") != "P3"
      ),
      size=data.get("size"),
      insertion_order=data.get("insertion_order"),
      links=raw_links if isinstance(raw_links, dict) else None,
      msg_ids=([str(item) for item in data["msg_ids"]] if "msg_ids" in data else None),
      report_ids=(
        [str(item) for item in data["report_ids"]] if "report_ids" in data else None
      ),
      task_ids=(
        [str(item) for item in data["task_ids"]] if "task_ids" in data else None
      ),
      deps=([str(item) for item in data["deps"]] if "deps" in data else None),
      gate_names=(
        [str(item) for item in data["gate_names"]] if "gate_names" in data else None
      ),
      modified_at=str(data.get("modified_at", "")),
      created_at=str(data.get("created_at", "")),
      allow_long_details=True,
    )


@dataclass(slots=True)
class ReportField:
  """Interactive question field parsed from a report Markdown file."""

  label: str
  type: str
  id: str = ""
  options: list[str] = field(default_factory=list)
  recommended: list[str] = field(default_factory=list)
  question: str = ""
  option_labels: dict[str, str] = field(default_factory=dict)
  annotated: bool = False

  def __post_init__(self) -> None:
    if not isinstance(self.label, str) or not self.label.strip():
      raise ValueError("ReportField.label is required")
    self.label = self.label.strip()
    self.question = str(self.question or self.label).strip()
    self.option_labels = dict(self.option_labels)
    if self.type not in REPORT_FIELD_TYPES:
      raise ValueError(f"Invalid ReportField.type: {self.type!r}")
    raw_id = self.id.strip().rstrip("-") if isinstance(self.id, str) else ""
    self.id = (
      validate_entity_id(raw_id, "ReportField.id") if raw_id else kebab_slug(self.label)
    )
    if self.type == "text":
      if self.options or self.recommended:
        raise ValueError("Text fields cannot define preset options or recommendations")
      self.options = []
      self.recommended = []
      return
    if not (OPTION_MIN_COUNT <= len(self.options) <= OPTION_MAX_COUNT):
      raise ValueError(
        f"{self.type} fields require {OPTION_MIN_COUNT}..{OPTION_MAX_COUNT} options"
      )
    cleaned_options: list[str] = []
    seen: set[str] = set()
    for opt in self.options:
      text = str(opt).strip()
      if not (1 <= len(text) <= OPTION_MAX_LEN):
        raise ValueError(f"Option must be 1..{OPTION_MAX_LEN} characters: {text!r}")
      if text in seen:
        raise ValueError(f"Field {self.id!r} repeats option {text!r}")
      seen.add(text)
      cleaned_options.append(text)
    self.options = cleaned_options
    if self.option_labels and (
      set(self.option_labels) != seen
      or any(
        not isinstance(v, str) or not v.strip() for v in self.option_labels.values()
      )
    ):
      raise ValueError(f"Field {self.id!r} requires an answer label for each option")
    cleaned_rec = [str(item).strip() for item in self.recommended if str(item).strip()]
    if any(item not in seen for item in cleaned_rec):
      raise ValueError(f"Field {self.id!r} recommends an unknown option")
    if self.type == "choice" and len(cleaned_rec) > 1:
      raise ValueError(f"Choice field {self.id!r} can recommend at most 1 option")
    self.recommended = _dedupe_strings(cleaned_rec)

  def to_dict(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "label": self.label,
      "type": self.type,
      "options": list(self.options),
      "recommended": list(self.recommended),
      "question": self.question,
      "option_labels": dict(self.option_labels),
      "annotated": self.annotated,
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> ReportField:
    return cls(
      id=str(data.get("id", "")),
      label=str(data["label"]),
      type=str(data["type"]),
      options=[str(item) for item in data.get("options", [])],
      recommended=[str(item) for item in data.get("recommended", [])],
      question=str(data.get("question", "")),
      option_labels=dict(data.get("option_labels", {})),
      annotated=bool(data.get("annotated", False)),
    )


@dataclass(slots=True)
class ReportAnswer:
  """Owner form submission that can outlive its report."""

  id: str
  report_id: str
  values: dict[str, Any]
  status: str = "sent"
  acks: list[str] = field(default_factory=list)
  modified_at: str = ""
  created_at: str = ""
  report_title: str = ""
  field_labels: dict[str, str] = field(default_factory=dict)
  orphaned: bool = False
  # Per-ACK response data, not part of the stored answer.
  task_details_added: dict[str, list[str]] = field(
    default_factory=dict, repr=False, compare=False
  )

  def __post_init__(self) -> None:
    validate_hex_id(self.id, "ReportAnswer.id")
    validate_entity_id(self.report_id, "ReportAnswer.report_id")
    if not isinstance(self.values, dict) or not self.values:
      raise ValueError("ReportAnswer.values must be a non-empty dictionary")
    self.values = dict(self.values)
    if self.status not in MESSAGE_STATUSES:
      raise ValueError(f"Invalid ReportAnswer.status: {self.status!r}")
    if not isinstance(self.acks, list) or any(
      not isinstance(ack, str) or not ack.strip() for ack in self.acks
    ):
      raise ValueError("ReportAnswer.acks must be a list of non-empty Markdown strings")
    self.acks = list(self.acks)
    self.modified_at = str(self.modified_at or "")
    self.created_at = str(self.created_at or self.modified_at)
    self.report_title = str(self.report_title or "")
    self.field_labels = dict(self.field_labels)
    if not isinstance(self.orphaned, bool):
      raise TypeError("ReportAnswer.orphaned must be a boolean")

  def to_dict(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "report_id": self.report_id,
      "values": dict(self.values),
      "status": self.status,
      "acks": list(self.acks),
      "modified_at": self.modified_at,
      "created_at": self.created_at,
      "report_title": self.report_title,
      "field_labels": dict(self.field_labels),
      "orphaned": self.orphaned,
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> ReportAnswer:
    return cls(
      id=str(data["id"]),
      report_id=str(data["report_id"]),
      values=dict(data.get("values", {})),
      status=str(data.get("status", "sent")),
      acks=[str(item) for item in data.get("acks", [])],
      modified_at=str(data.get("modified_at", "")),
      created_at=str(data.get("created_at", "")),
      report_title=str(data.get("report_title", "")),
      field_labels=dict(data.get("field_labels", {})),
      orphaned=data.get("orphaned", False),
    )


@dataclass(slots=True)
class Report:
  """Published Markdown report with parsed interactive fields and owner answers."""

  id: str
  title: str
  body: str
  fields: list[ReportField] = field(default_factory=list)
  status: str = "published"
  answers: list[ReportAnswer] = field(default_factory=list)
  seen: bool = False
  ack_seen: bool = False
  ack_seen_at: str = ""
  modified_at: str = ""
  updated_at: str = ""
  pub_at: str = ""
  last_viewed_at: str = ""
  pinned: bool = False
  source_file: str = ""

  def __post_init__(self) -> None:
    validate_entity_id(self.id, "Report.id")
    if not isinstance(self.title, str) or not self.title.strip():
      raise ValueError("Report.title must be a non-empty string")
    self.title = self.title.strip()
    if not isinstance(self.body, str):
      raise TypeError("Report.body must be a string")
    if self.status not in REPORT_STATUSES:
      raise ValueError(f"Invalid Report.status: {self.status!r}")
    self.fields = [
      item if isinstance(item, ReportField) else ReportField.from_dict(item)
      for item in self.fields
    ]
    seen_ids: set[str] = set()
    for item in self.fields:
      if item.id in seen_ids:
        raise ValueError(f"Report {self.id!r} has duplicate field ID {item.id!r}")
      seen_ids.add(item.id)
    self.answers = [
      ans if isinstance(ans, ReportAnswer) else ReportAnswer.from_dict(ans)
      for ans in self.answers
    ]
    self.seen = bool(self.seen)
    self.ack_seen = bool(self.ack_seen)
    self.ack_seen_at = str(self.ack_seen_at or "")
    self.modified_at = str(self.modified_at or "")
    self.updated_at = str(self.updated_at or self.modified_at)
    self.pub_at = str(self.pub_at or "")
    self.last_viewed_at = str(self.last_viewed_at or "")
    self.pinned = bool(self.pinned)
    if not isinstance(self.source_file, str):
      raise TypeError("Report.source_file must be text")

  @property
  def needs_answer(self) -> bool:
    """Return True when the report is published, has fields, and has no answers."""
    return self.status == "published" and bool(self.fields) and not self.answers

  @property
  def latest_unacked_answer(self) -> ReportAnswer | None:
    """Return the newest answer if it has not yet been acknowledged."""
    if not self.answers:
      return None
    latest = self.answers[-1]
    return latest if latest.status != "acked" else None

  def to_dict(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "title": self.title,
      "body": self.body,
      "fields": [item.to_dict() for item in self.fields],
      "status": self.status,
      "answers": [item.to_dict() for item in self.answers],
      "seen": self.seen,
      "ack_seen": self.ack_seen,
      "ack_seen_at": self.ack_seen_at,
      "needs_answer": self.needs_answer,
      "modified_at": self.modified_at,
      "updated_at": self.updated_at,
      "pub_at": self.pub_at,
      "last_viewed_at": self.last_viewed_at,
      "pinned": self.pinned,
      "source_file": self.source_file,
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> Report:
    return cls(
      id=str(data["id"]),
      title=str(data["title"]),
      body=str(data.get("body", "")),
      fields=[ReportField.from_dict(raw) for raw in data.get("fields", [])],
      status=str(data.get("status", "published")),
      answers=[ReportAnswer.from_dict(raw) for raw in data.get("answers", [])],
      seen=bool(data.get("seen", False)),
      ack_seen=bool(data.get("ack_seen", False)),
      ack_seen_at=str(data.get("ack_seen_at", "")),
      modified_at=str(data.get("modified_at", "")),
      updated_at=str(data.get("updated_at", "")),
      pub_at=str(data.get("pub_at", "")),
      last_viewed_at=str(data.get("last_viewed_at", "")),
      pinned=bool(data.get("pinned", False)),
      source_file=data.get("source_file", ""),
    )


def _dedupe_strings(items: list[str]) -> list[str]:
  result: list[str] = []
  seen: set[str] = set()
  for raw in items:
    text = str(raw).strip()
    if text and text not in seen:
      seen.add(text)
      result.append(text)
  return result
