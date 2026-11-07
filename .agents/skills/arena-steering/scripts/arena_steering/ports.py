"""Protocol ports and default clock/ID providers for Arena Steering."""

from __future__ import annotations

import secrets
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Protocol

from .models import Message, Report, ReportAnswer, Task


class Clock(Protocol):
  """Provide UTC ISO-8601 timestamps."""

  def now_iso(self) -> str:
    """Return the current UTC timestamp in ISO-8601 format."""


class IdGenerator(Protocol):
  """Generate 6-character lowercase hex identifiers."""

  def new_hex_id(self) -> str:
    """Return a random 6-character lowercase hex string."""


class UtcClock:
  """System clock returning second-precision UTC ISO-8601 strings."""

  def now_iso(self) -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SecretsHexIdGenerator:
  """Cryptographic 6-character hex ID generator."""

  def new_hex_id(self) -> str:
    return secrets.token_hex(3)


class MessageRepository(Protocol):
  """Persistence port for steering messages."""

  def get_message(self, msg_id: str) -> Message | None:
    """Fetch a message by its 6-character hex ID."""

  def list_messages(
    self, *, unread_only: bool = False, include_hidden: bool = True
  ) -> list[Message]:
    """List stored messages in creation order."""

  def list_message_page(
    self,
    *,
    unread_only: bool,
    include_hidden: bool,
    query: str,
    before: str,
    limit: int,
  ) -> tuple[list[Message], int, bool]:
    """Search and page stored messages."""

  def save_message(self, message: Message) -> Message:
    """Insert or update a message."""

  def save_message_once(
    self, message: Message, request_id: str, fingerprint: str
  ) -> Message:
    """Atomically create or retrieve a message for a client request."""

  def append_message_ack(self, msg_id: str, reply: str, now: str) -> Message:
    """Atomically append a reply and reset its receipt."""

  def mark_message_seen(self, msg_id: str, now: str) -> Message:
    """Mark message delivery without overwriting concurrent replies."""

  def mark_replies_seen(self, msg_id: str, revision: str) -> bool:
    """Set the receipt if the supplied reply revision is current."""


class GateStatus(Protocol):
  """Read the current state of named gates."""

  def is_running(self, name: str) -> bool:
    """Return whether the named gate is running."""


class TaskRepository(Protocol):
  """Persistence port for the mutable task queue."""

  def transaction(self) -> AbstractContextManager[None]:
    """Group queue reads and writes in a transaction."""

  def get_task(self, task_id: str) -> Task | None:
    """Fetch one task by ID."""

  def list_tasks(self) -> list[Task]:
    """Return all tasks in queue order."""

  def next_task_insertion_order(self) -> int:
    """Allocate a durable, never-reused insertion rank."""

  def save_task_queue(self, tasks: list[Task]) -> list[Task]:
    """Atomically replace the ordered task queue."""

  def delete_task(self, task_id: str) -> bool:
    """Delete one task from the queue and return True when removed."""


class ReportRepository(Protocol):
  """Persistence port for reports and their answers."""

  def transaction(self) -> AbstractContextManager[None]:
    """Group report reads and writes in a transaction."""

  def get_report(self, report_id: str) -> Report | None:
    """Fetch one report by ID."""

  def list_reports(self) -> list[Report]:
    """Return all reports in creation order."""

  def save_report(self, report: Report) -> Report:
    """Insert or update one report."""

  def get_message(self, msg_id: str) -> Message | None:
    """Check the shared message ID namespace."""

  def save_message(self, message: Message) -> Message:
    """Update a shared answer message."""

  def list_report_answers(
    self, report_id: str | None = None, *, include_orphaned: bool = False
  ) -> list[ReportAnswer]:
    """Project active answers, optionally including orphans."""

  def orphan_report_answers(self, report_id: str) -> None:
    """Detach retained answers from a removed report."""

  def save_report_answer(self, answer: ReportAnswer) -> ReportAnswer:
    """Save one answer as a hidden message."""

  def append_message_ack(self, msg_id: str, reply: str, now: str) -> Message:
    """Append an answer acknowledgement to its message."""

  def rename_report_answers(self, old_id: str, new_id: str, now: str) -> None:
    """Update answer references when a report is renamed."""


class MetaRepository(Protocol):
  """Key-value metadata port for poll signals and counters."""

  def get_meta(self, key: str, default: str | None = None) -> str | None:
    """Read a metadata value by key."""

  def set_meta(self, key: str, value: str) -> None:
    """Write a metadata value by key."""
