"""Domain services for Messages, Tasks, and Reports in Arena Steering."""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any

from . import pre as pre_mod
from . import survey as survey_mod
from .display_paths import home_relative
from .git_safety import DirtyTreeError, GitCheckError, verify_completion_commit
from .markdown import render_report_markdown, unparsed_strike_lines
from .markdown_fields import format_answer_for_task_detail, parse_report_markdown
from .models import (
  MESSAGE_ID_RE,
  TASK_DETAIL_MAX_LEN,
  Attachment,
  Message,
  Report,
  ReportAnswer,
  ReportField,
  Task,
  validate_entity_id,
  validate_hex_id,
)
from .ports import (
  Clock,
  GateStatus,
  IdGenerator,
  MessageRepository,
  MetaRepository,
  ReportRepository,
  SecretsHexIdGenerator,
  TaskRepository,
  UtcClock,
)

REPORT_IDLE_INTERVAL = timedelta(minutes=30)
POLL_HEARTBEAT_SECONDS = 1.0
POLL_FRESH_SECONDS = 5.0
POLL_REPEAT_SECONDS = 45
POLL_IMMEDIATE_SECONDS = 1.0
BIBLE_READ = "If full bible missing, use read_file for {paths}"
_BIBLE_PATHS = (
  "AGENTS.md",
  "ARENA.md",
  ".agents/skills/arena-steering/SKILL.md",
)
TOOL_NAME_META = "last_tool_name"
TOOL_AT_META = "last_tool_at"
TOOL_PROBE_NAMES = frozenset({"read", "edit", "write", "bash"})
ENTITY_ID_MAX_LEN = 64


def _validate_new_entity_id(value: str, label: str) -> None:
  validate_entity_id(value, f"{label}.id")
  if len(value) > ENTITY_ID_MAX_LEN:
    raise ValueError(f"{label} ID must be at most {ENTITY_ID_MAX_LEN} characters")


def with_bible_read(body: str) -> str:
  """Append the separate, conditional full-bible reminder to a true violation."""
  root = pre_mod.repo_root()
  paths = ", ".join(home_relative(root / relative) for relative in _BIBLE_PATHS)
  return body + "\n" + BIBLE_READ.format(paths=paths)


def _validate_ack_markdown(markdown: str, target_id: str) -> str:
  if not isinstance(markdown, str) or not markdown.strip():
    raise ValueError("Acknowledgement Markdown cannot be empty")
  reply = re.sub(r"\A(?:[ \t]*\r?\n)*", "", markdown).rstrip()
  words = re.findall(r"[^\W_]+", reply)
  if words and all(word.casefold() == target_id for word in words):
    raise ValueError("Acknowledgement must contain more than the target ID alone")
  return reply


def _report_timestamp(value: str) -> datetime:
  stamp = datetime.fromisoformat(value)
  return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def _append_unique_details(task: Task, lines: list[str]) -> bool:
  known = set(task.details)
  added = False
  for line in lines:
    if line not in known:
      task.details.append(line)
      known.add(line)
      added = True
  return added


def _answer_detail_lines(report: Report, answer: ReportAnswer) -> list[str]:
  current_labels = {field.id: field.label for field in report.fields}
  return [
    format_answer_for_task_detail(
      answer.field_labels.get(field_id) or current_labels.get(field_id, field_id), value
    )
    for field_id, value in answer.values.items()
  ]


def _append_answer_details_to_linked_tasks(
  tasks: TaskRepository, report: Report, answer: ReportAnswer, now: str
) -> dict[str, list[str]]:
  lines = _answer_detail_lines(report, answer)
  if not lines:
    return {}
  queue = tasks.list_tasks()
  added: dict[str, list[str]] = {}
  for task in queue:
    previous_length = len(task.details)
    if report.id in task.report_ids and _append_unique_details(task, lines):
      task.modified_at = now
      added[task.id] = task.details[previous_length:]
  if added:
    tasks.save_task_queue(queue)
  return added


DEFAULT_SURVEY_QUESTIONS: tuple[dict[str, Any], ...] = tuple(
  {
    "id": q["id"],
    "prompt": q["question"],
    "question": q["question"],
    "deterministic": bool(q.get("deterministic")),
    "answer_fields": dict(q["answer_fields"]),
  }
  for q in survey_mod.SPEC["questions"]
)


def _transactional_report_write(method: Any) -> Any:
  @wraps(method)
  def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
    with self._reports.transaction():
      return method(self, *args, **kwargs)

  return wrapped


class MessageService:
  """Manage steering messages, status transitions, acknowledgements, and polling."""

  def __init__(
    self,
    messages: MessageRepository,
    reports: ReportRepository,
    meta: MetaRepository,
    tasks: TaskRepository,
    clock: Clock | None = None,
    id_gen: IdGenerator | None = None,
  ) -> None:
    self._messages = messages
    self._reports = reports
    self._meta = meta
    self._tasks = tasks
    self._clock = clock or UtcClock()
    self._id_gen = id_gen or SecretsHexIdGenerator()
    self._cond = threading.Condition()
    # Survey answers, results, and the check stamp live in memory only, so a restart clears them.
    self._survey_answers: dict[str, str] = {}
    self._survey_results: list[dict[str, Any]] = []
    self._survey_checked_at = ""
    self._survey_pending = False

  def send_message(
    self,
    body: str,
    *,
    hidden: bool = False,
    blocking: bool = True,
    violation_type: str = "",
    violation_clause_file: str = "",
    attachments: list[Attachment] | None = None,
    parent_ids: list[str] | None = None,
    parent_refs: list[dict[str, str]] | None = None,
    msg_id: str | None = None,
    request_id: str | None = None,
    fingerprint: str = "",
  ) -> Message:
    if not isinstance(body, str) or (not body.strip() and not attachments):
      raise ValueError("Message body or attachment is required")
    resolved_id = (
      validate_hex_id(msg_id, "Message.id")
      if msg_id is not None
      else self._unique_hex_id()
    )
    now = self._clock.now_iso()
    message = Message(
      id=resolved_id,
      body=body,
      hidden=hidden,
      blocking=blocking,
      violation_type=violation_type,
      violation_clause_file=violation_clause_file,
      attachments=list(attachments or []),
      parent_ids=parent_ids or [],
      parent_refs=[] if parent_refs is None else parent_refs,
      status="sent",
      acks=[],
      modified_at=now,
    )
    repositories = {
      "message": self._messages.get_message,
      "task": self._tasks.get_task,
      "report": self._reports.get_report,
    }
    for ref in message.parent_refs:
      if repositories[ref["type"]](ref["id"]) is None:
        raise KeyError(f"Unknown parent {ref['type']} ID: {ref['id']}")
    with self._cond:
      if request_id is not None:
        message = self._messages.save_message_once(message, request_id, fingerprint)
      else:
        with self._reports.transaction():
          if self._messages.get_message(message.id) is not None:
            if msg_id is not None:
              raise ValueError(f"Message ID {message.id!r} already exists")
            message.id = self._unique_hex_id()
          self._messages.save_message(message)
      self._cond.notify_all()
    return message

  def get_message(self, msg_id: str) -> Message:
    validate_hex_id(msg_id, "Message.id")
    msg = self._messages.get_message(msg_id)
    if msg is None:
      raise KeyError(f"Unknown message ID: {msg_id}")
    return msg

  def list_message_page(
    self,
    *,
    unread_only: bool,
    include_hidden: bool,
    query: str,
    before: str,
    limit: int,
  ) -> tuple[list[Message], int, bool]:
    return self._messages.list_message_page(
      unread_only=unread_only,
      include_hidden=include_hidden,
      query=query,
      before=before,
      limit=limit,
    )

  def list_messages(
    self,
    *,
    unread_only: bool = False,
    include_hidden: bool = True,
    newest_first: bool = False,
    page: int = 1,
    page_size: int | None = None,
  ) -> list[Message]:
    items = self._messages.list_messages(
      unread_only=unread_only, include_hidden=include_hidden
    )
    if newest_first:
      items = list(reversed(items))
    if page_size is not None:
      if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be positive integers")
      start = (page - 1) * page_size
      items = items[start : start + page_size]
    return items

  def _latest_pending_answers(self) -> list[ReportAnswer]:
    latest: dict[str, ReportAnswer] = {}
    orphaned: list[ReportAnswer] = []
    for answer in self._reports.list_report_answers(include_orphaned=True):
      if answer.orphaned:
        message = self._messages.get_message(answer.id)
        if message is not None and message.blocking and answer.status != "acked":
          orphaned.append(answer)
      else:
        latest[answer.report_id] = answer
    return orphaned + [answer for answer in latest.values() if answer.status != "acked"]

  def has_pending_deliverables(self) -> bool:
    for msg in self._messages.list_messages(unread_only=True, include_hidden=True):
      if msg.type == "message" and (msg.status == "sent" or msg.requires_ack):
        return True
    return bool(self._latest_pending_answers())

  @_transactional_report_write
  def read_pending(self, *, mark_seen: bool = True) -> dict[str, Any]:
    """Return pending unread messages and the newest unacknowledged answer per report."""
    now = self._clock.now_iso()
    delivered_messages: list[Message] = []
    for msg in self._messages.list_messages(unread_only=True, include_hidden=True):
      if msg.type != "message":
        continue
      if mark_seen and msg.status == "sent":
        msg = self._messages.mark_message_seen(msg.id, now)
      delivered_messages.append(msg)

    delivered_answers: list[tuple[Report, ReportAnswer]] = []
    for latest in self._latest_pending_answers():
      report = None if latest.orphaned else self._reports.get_report(latest.report_id)
      if mark_seen and latest.status == "sent":
        latest = self._messages.mark_message_seen(latest.id, now).as_report_answer()
        if report is not None:
          report.modified_at = now
          self._reports.save_report(report)
      if report is None:
        report = Report(
          id=latest.report_id,
          title=latest.report_title or latest.report_id,
          body="",
          status="answered",
          answers=[latest],
        )
      delivered_answers.append((report, latest))

    return {"messages": delivered_messages, "report_answers": delivered_answers}

  def pending_violation(self) -> Message | None:
    """Return the oldest unacknowledged violation message."""
    return next(
      (
        msg
        for msg in self._messages.list_messages(unread_only=True, include_hidden=True)
        if msg.type == "message" and msg.violation_type and msg.requires_ack
      ),
      None,
    )

  def flag_violation_once(self, violation_type: str, body: str) -> Message:
    """Keep one pending hidden blocking flag for each automatic violation type."""
    with self._reports.transaction():
      for msg in self._messages.list_messages(unread_only=True, include_hidden=True):
        if msg.violation_type == violation_type and msg.requires_ack:
          return msg
      return self.send_message(
        with_bible_read(body), hidden=True, blocking=True, violation_type=violation_type
      )

  def flag_dirty_finish(self) -> Message:
    """Persist one blocking flag after a dirty-tree finish attempt rolls back."""
    return self.flag_violation_once(
      "dirty-task-finish",
      "VIOLATION: Finish task with dirty tree. Clean tree then retry.",
    )

  def pending_ids(self) -> list[str]:
    """Return unacked message and report-answer IDs without marking anything seen."""
    ids = [
      message.id
      for message in self._messages.list_messages(
        unread_only=False, include_hidden=True
      )
      if message.type == "message" and message.requires_ack
    ]
    ids.extend(answer.id for answer in self._latest_pending_answers())
    return ids

  def reset_gate_if_clear(self) -> None:
    """Reset the shell countdown only after all blocking work is acknowledged."""
    with self._reports.transaction():
      if not self.pending_ids() and self._meta.get_meta(
        pre_mod.CALL_COUNT_META
      ) not in (
        None,
        "0",
      ):
        self._meta.set_meta(pre_mod.CALL_COUNT_META, "0")

  def resolve_ack_target(
    self, target_id: str | None
  ) -> tuple[str, Message | ReportAnswer]:
    """Resolve an acknowledgement target without changing it."""
    if target_id is None:
      target_id = self._resolve_single_pending_id()
    validate_hex_id(target_id, "Acknowledgement target ID")
    message = self._messages.get_message(target_id)
    if message is not None:
      if message.type == "report_answer":
        return "report_answer", message.as_report_answer()
      return "message", message
    raise KeyError(f"Unknown message or answer ID: {target_id}")

  @_transactional_report_write
  def ack(
    self, target_id: str | None, markdown: str
  ) -> tuple[str, Message | ReportAnswer]:
    """Acknowledge a message or report answer by appending a Markdown reply."""
    if not isinstance(markdown, str) or not markdown.strip():
      raise ValueError("Acknowledgement Markdown cannot be empty")
    now = self._clock.now_iso()

    if target_id is None:
      target_id = self._resolve_single_pending_id()

    validate_hex_id(target_id, "Acknowledgement target ID")
    msg = self._messages.get_message(target_id)
    if msg is None:
      raise KeyError(f"Unknown message or answer ID: {target_id}")
    reply = _validate_ack_markdown(markdown, target_id)
    first_ack = msg.status != "acked"
    msg = self._messages.append_message_ack(target_id, reply, now)
    if msg.type != "report_answer":
      self.reset_gate_if_clear()
      return ("message", msg)
    answer = msg.as_report_answer()
    report = None if answer.orphaned else self._reports.get_report(answer.report_id)
    if first_ack and report is not None:
      answer.task_details_added = _append_answer_details_to_linked_tasks(
        self._tasks, report, answer, now
      )
    if report is not None and report.answers and report.answers[-1].id == answer.id:
      report.ack_seen = False
      report.ack_seen_at = ""
      report.modified_at = now
      self._reports.save_report(report)
    self.reset_gate_if_clear()
    return ("report_answer", answer)

  def mark_replies_seen(self, msg_id: str, revision: str) -> bool:
    validate_hex_id(msg_id, "Message.id")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
      raise ValueError("Reply revision must be a SHA-256 hex digest")
    return self._messages.mark_replies_seen(msg_id, revision)

  def record_tool(self, name: str) -> None:
    """Record one agent tool without letting platform probes replace its cadence."""
    if name not in TOOL_PROBE_NAMES:
      raise ValueError(f"Unknown tool name: {name}")
    stamp = self._clock.now_iso()
    with self._reports.transaction():
      self._meta.set_meta(TOOL_NAME_META, name)
      self._meta.set_meta(TOOL_AT_META, stamp)
    with self._cond:
      self._cond.notify_all()

  def tool_activity(self) -> dict[str, str] | None:
    """Return the last agent tool in the shared v1-compatible state shape."""
    name = self._meta.get_meta(TOOL_NAME_META)
    stamp = self._meta.get_meta(TOOL_AT_META, "") or ""
    if name not in TOOL_PROBE_NAMES or not stamp:
      return None
    return {"tool": str(name), "at": stamp}

  def calls_since_message(self) -> int:
    """Return the current pending-inbox Bash count for status display."""
    try:
      return max(0, int(self._meta.get_meta(pre_mod.CALL_COUNT_META, "0") or "0"))
    except ValueError:
      return 0

  def touch_agent(self) -> None:
    """Record an agent call and clear the previous turn-end marker."""
    stamp = self._clock.now_iso()
    with self._reports.transaction():
      if self._meta.get_meta("agent_seen_at") != stamp:
        self._meta.set_meta("agent_seen_at", stamp)
      if self._meta.get_meta("turn_ended_at"):
        self._meta.set_meta("turn_ended_at", "")

  def trigger_skip_poll(self) -> None:
    self._meta.set_meta("skip_poll", "1")
    with self._cond:
      self._cond.notify_all()

  def consume_skip_poll(self) -> bool:
    with self._reports.transaction():
      flag = self._meta.get_meta("skip_poll", "0") == "1"
      if flag:
        self._meta.set_meta("skip_poll", "0")
      return flag

  def clear_poll_repeat(self) -> None:
    """Reset consecutive agent polls after another agent command."""
    if self._meta.get_meta("poll_repeat_at") not in (None, "0"):
      self._meta.set_meta("poll_repeat_at", "0")
    if self._meta.get_meta("poll_followup") == "1":
      self._meta.set_meta("poll_followup", "0")

  def request_poll_followup(self) -> None:
    """Permit the immediate poll requested by a completed detached run."""
    self._meta.set_meta("poll_followup", "1")

  def check_repeat_poll(self) -> Message | None:
    """Flag a fast repeat before starting another agent poll."""
    now = int(time.time())
    with self._reports.transaction():
      previous = self._meta.get_meta("poll_repeat_at")
      followup = self._meta.get_meta("poll_followup") == "1"
      if followup:
        self._meta.set_meta("poll_followup", "0")
      self._meta.set_meta("poll_repeat_at", str(now))
      try:
        recent = (
          previous is not None and 0 <= now - int(previous) <= POLL_REPEAT_SECONDS
        )
      except ValueError:
        recent = False
      if followup or not recent or self._meta.get_meta("poll_immediate") != "1":
        return None
      return self.flag_violation_once(
        "repeat-poll",
        "VIOLATION: Repeat poll. Use worktree if more task open.",
      )

  def record_poll_duration(self, seconds: float) -> None:
    """Remember whether the last agent poll returned immediately."""
    self._meta.set_meta(
      "poll_immediate", "1" if seconds < POLL_IMMEDIATE_SECONDS else "0"
    )

  def poll(
    self,
    timeout_seconds: float = 0.0,
    *,
    initial_task: Task | None = None,
    live_run: Callable[[], bool] | None = None,
    finished_run: Callable[[], list[tuple[str, int]]] | None = None,
  ) -> dict[str, Any]:
    """Wait for inbox work, an initial runnable task, a run, or eligible Skip."""
    timeout = max(0.0, min(float(timeout_seconds), 1799.0))
    deadline = time.monotonic() + timeout
    skipped = timed_out = False
    task: Task | None = None
    verdicts: list[tuple[str, int]] = []
    self.touch_agent()
    if not self.has_pending_deliverables():
      task = initial_task
      if task is None:
        verdicts = finished_run() if finished_run else []
        if not verdicts and not (live_run and live_run()):
          skipped = self.consume_skip_poll()
      if task is None and not skipped and not verdicts and timeout > 0:
        with self._reports.transaction():
          self._meta.set_meta("polling_since", self._clock.now_iso())
          self._meta.set_meta("polling_at", self._clock.now_iso())
        try:
          while not self.has_pending_deliverables():
            verdicts = finished_run() if finished_run else []
            if verdicts:
              break
            if not (live_run and live_run()) and self.consume_skip_poll():
              skipped = True
              break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
              timed_out = True
              break
            self._meta.set_meta("polling_at", self._clock.now_iso())
            # Do not hold the condition while accessing the store. An answer
            # callback may notify while its store transaction is open.
            with self._cond:
              self._cond.wait(timeout=min(POLL_HEARTBEAT_SECONDS, remaining))
        finally:
          with self._reports.transaction():
            self._meta.set_meta("polling_at", "")
            self._meta.set_meta("polling_since", "")
    delivered = self.read_pending(mark_seen=True)
    inbox_ready = bool(delivered["messages"] or delivered["report_answers"])
    if (skipped or timed_out) and not inbox_ready and task is None and not verdicts:
      self._meta.set_meta("turn_ended_at", self._clock.now_iso())
    delivered["status"] = (
      "ready"
      if inbox_ready or task is not None
      else "run_finished"
      if verdicts
      else "skipped"
      if skipped
      else "timeout"
    )
    delivered["next_task"] = None if inbox_ready else task
    if verdicts:
      delivered["run_verdicts"] = verdicts
    return delivered

  def notify_new_work(self) -> None:
    with self._cond:
      self._cond.notify_all()

  def get_survey(self) -> dict[str, Any]:
    """Return current survey state from memory, never from the store."""
    return {
      "pending": self._survey_pending,
      "checked_at": self._survey_checked_at,
      "answered_at": self._survey_checked_at,
      "questions": [dict(item) for item in DEFAULT_SURVEY_QUESTIONS],
      "template": survey_mod.template(),
      "results": [dict(row) for row in self._survey_results],
      "answers": dict(self._survey_answers),
    }

  def submit_survey(self, values: Any) -> dict[str, Any]:
    """Grade and keep agent survey answers in memory, rejecting any null values."""
    if isinstance(values, list):
      raw_text = json.dumps(values, ensure_ascii=False)
    elif isinstance(values, str):
      raw_text = values.strip()
    else:
      raise TypeError("Survey submission must be a JSON array")
    results = survey_mod.grade(raw_text)
    answers = {
      str(row["id"]): json.dumps(row["answer"], ensure_ascii=False) for row in results
    }
    with self._cond:
      self._survey_answers = answers
      self._survey_results = results
      self._survey_checked_at = self._clock.now_iso()
      self._survey_pending = False
      self._cond.notify_all()
    return self.get_survey()

  def trigger_bible_check(self, active: bool | None = None) -> dict[str, Any]:
    """Toggle or set the bible check interrupt flag and wake any waiting poll."""
    with self._cond:
      cur = self._meta.get_meta("bible_interrupt", "0") == "1"
      next_state = (not cur) if active is None else bool(active)
      self._meta.set_meta("bible_interrupt", "1" if next_state else "0")
      if next_state:
        self.send_message(
          "Survey again\n"
          "Answer the survey, ack this msg, and continue.\n"
          "For the survey, use existing knowledge only. Run `steer survey` and read "
          "the image path it prints, in JSON order.\n"
          "Do not search, fetch, open other files, run code, or ask another system for answers.\n"
          "Give your best effort. Guesses are allowed; use extremely low confidence "
          "when very unsure. Boolean answers need true or false.\n"
          "Use unknown with confidence 0 only if the whole question is unknown.",
          hidden=True,
          blocking=True,
        )
        self._survey_pending = True
        self._meta.set_meta("skip_poll", "1")
      self._cond.notify_all()
    return self.get_survey()

  def _resolve_single_pending_id(self) -> str:
    candidates: list[str] = [
      msg.id
      for msg in self._messages.list_messages(unread_only=True, include_hidden=True)
      if msg.type == "message" and msg.requires_ack
    ]
    candidates.extend(answer.id for answer in self._latest_pending_answers())
    if not candidates:
      raise ValueError("No pending message or report answer requires acknowledgement")
    if len(candidates) > 1:
      raise ValueError(
        f"Multiple items require acknowledgement ({', '.join(candidates)}); pass an explicit ID"
      )
    return candidates[0]

  def _unique_hex_id(self) -> str:
    for _ in range(64):
      candidate = self._id_gen.new_hex_id()
      if self._messages.get_message(candidate) is None:
        return candidate
    raise RuntimeError("Unable to allocate unique 6-character hex ID")


def _transactional_task_write(method: Any) -> Any:
  @wraps(method)
  def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
    try:
      with self._tasks.transaction():
        return method(self, *args, **kwargs)
    except DirtyTreeError:
      on_dirty_finish = getattr(self, "_on_dirty_finish", None)
      if on_dirty_finish is not None:
        on_dirty_finish()
      raise

  return wrapped


class TaskService:
  """Manage the mutable task queue, dependency ordering, and derived blocking."""

  def __init__(
    self,
    tasks: TaskRepository,
    reports: ReportRepository,
    clock: Clock | None = None,
    gates: GateStatus | None = None,
    commit_verifier: Callable[[str], str] | None = None,
    clean_tree_verifier: Callable[[], None] | None = None,
    on_dirty_finish: Callable[[], None] | None = None,
    messages: MessageRepository | None = None,
  ) -> None:
    self._tasks = tasks
    self._reports = reports
    self._messages = messages
    self._clock = clock or UtcClock()
    self._gates = gates
    self._clean_tree_verifier = clean_tree_verifier
    self._on_dirty_finish = on_dirty_finish
    self._commit_verifier = commit_verifier or (
      lambda commit: verify_completion_commit(pre_mod.repo_root(), commit)
    )

  def has_running_gate(self, task: Task) -> bool:
    """Distinguish an active linked run from other task blockers."""
    return (
      task.status == "upcoming"
      and self._gates is not None
      and any(self._gates.is_running(gate) for gate in task.gate_names)
    )

  def is_task_blocked(
    self,
    task: Task,
    *,
    task_map: dict[str, Task] | None = None,
    report_map: dict[str, Report] | None = None,
  ) -> bool:
    """Derive blocking from unanswered linked reports and unfinished dependencies."""
    if task.status == "finished":
      return False
    if self.has_running_gate(task):
      return True
    tasks_by_id = (
      task_map
      if task_map is not None
      else {item.id: item for item in self._tasks.list_tasks()}
    )
    reports_by_id = (
      report_map
      if report_map is not None
      else {item.id: item for item in self._reports.list_reports()}
    )
    for dep_id in task.deps:
      dep_task = tasks_by_id.get(dep_id)
      if dep_task is not None and dep_task.status != "finished":
        return True
    if task.type == "report":
      return False  # Publication itself satisfies a report phase.
    completed_reports = {
      evidence
      for phase_type, evidence in zip(
        task.types[: task.phase_index], task.phase_evidence[: task.phase_index]
      )
      if phase_type == "report" and evidence is not None
    }
    for rep_id in task.report_ids:
      report = reports_by_id.get(rep_id)
      if rep_id not in completed_reports and report is not None and report.needs_answer:
        return True
    return False

  def list_tasks(self, *, include_finished: bool = False) -> list[tuple[Task, bool]]:
    queue = self._enforce_dependency_order(self._tasks.list_tasks())
    task_map = {item.id: item for item in queue}
    report_map = {item.id: item for item in self._reports.list_reports()}
    result: list[tuple[Task, bool]] = []
    for item in queue:
      if not include_finished and item.status == "finished":
        continue
      blocked = self.is_task_blocked(item, task_map=task_map, report_map=report_map)
      result.append((item, blocked))
    return result

  def get_task(self, task_id: str) -> tuple[Task, bool]:
    validate_entity_id(task_id, "Task.id")
    task = self._tasks.get_task(task_id)
    if task is None:
      raise KeyError(f"Unknown task ID: {task_id}")
    self._apply_age_priority(task, self._clock.now_iso())
    return (task, self.is_task_blocked(task))

  @_transactional_task_write
  def create_task(
    self,
    task_id: str,
    title: str,
    *,
    size: int,
    priority: str | None = None,
    type: str = "code",
    types: list[str] | None = None,
    status: str = "upcoming",
    scope: str = "",
    details: list[str] | None = None,
    links: dict[str, list[str]] | None = None,
    msg_ids: list[str] | None = None,
    report_ids: list[str] | None = None,
    task_ids: list[str] | None = None,
    deps: list[str] | None = None,
    gate_names: list[str] | None = None,
  ) -> Task:
    _validate_new_entity_id(task_id, "Task")
    if self._tasks.get_task(task_id) is not None:
      raise ValueError(f"Task {task_id!r} already exists")
    if self._reports.get_report(task_id) is not None:
      raise ValueError(f"Report {task_id!r} already exists")
    if status == "finished":
      raise ValueError("Create an upcoming task, then finish its phase with evidence")
    now = self._clock.now_iso()
    task = Task(
      id=task_id,
      title=title,
      size=size,
      priority="P4" if priority is None else priority,
      priority_explicit=priority is not None,
      insertion_order=self._tasks.next_task_insertion_order(),
      type=type,
      types=types,
      status=status,
      scope=scope,
      details=list(details or []),
      links=links,
      msg_ids=list(msg_ids) if msg_ids is not None else None,
      report_ids=list(report_ids) if report_ids is not None else None,
      task_ids=list(task_ids) if task_ids is not None else None,
      deps=list(deps) if deps is not None else None,
      gate_names=list(gate_names) if gate_names is not None else None,
      modified_at=now,
    )
    if priority is None and all(phase == "report" for phase in task.types):
      task.priority = "P3"
    task = self._with_message_attachments(task, task.msg_ids)
    self._validate_details(task)
    queue = self._tasks.list_tasks()
    self._validate_deps_exist(task, queue)
    self._validate_dependency_priorities(queue, [*queue, task])
    ordered = self._enforce_dependency_order([*queue, task])
    self._tasks.save_task_queue(ordered)
    return task

  def _copy_task(self, task: Task, **changes: Any) -> Task:
    data = task.to_dict()
    data.update(changes)
    data["modified_at"] = self._clock.now_iso()
    return Task.from_dict(data)

  def _with_message_attachments(self, task: Task, msg_ids: list[str]) -> Task:
    if self._messages is None:
      return task
    details = list(task.details)
    attached = False
    for msg_id in msg_ids:
      message = self._messages.get_message(msg_id)
      if message is None or message.type != "message":
        continue
      for attachment in message.attachments:
        attached = True
        path = home_relative(Path(attachment.path).expanduser())
        if path not in details:
          details.append(path)
    if not attached:
      return task
    if details == task.details:
      return task
    return self._copy_task(task, details=details)

  def _complete_phase(
    self, task: Task, queue: list[Task], commit_hash: str | None = None
  ) -> Task:
    if task.status == "finished":
      raise ValueError("Task is already finished")
    if self._clean_tree_verifier is not None:
      try:
        self._clean_tree_verifier()
      except DirtyTreeError:
        raise
      except GitCheckError as exc:
        raise ValueError(str(exc)) from exc
    reports = {item.id: item for item in self._reports.list_reports()}
    if self.is_task_blocked(
      task, task_map={item.id: item for item in queue}, report_map=reports
    ):
      raise ValueError(
        "Task is blocked by a dependency, report answer, or running gate"
      )
    evidence: str | None = None
    if task.type == "report":
      evidence = next(
        (
          rep_id
          for rep_id in task.report_ids
          if (rep := reports.get(rep_id)) is not None
          and (rep.status == "published" or bool(rep.answers))
        ),
        None,
      )
      if evidence is None:
        raise ValueError(
          "Report phase requires a linked published report or received answer"
        )
    elif task.type in {"code", "docs"}:
      if not isinstance(commit_hash, str) or not re.fullmatch(
        r"[0-9a-f]{40}", commit_hash
      ):
        raise ValueError("Code and docs phases require a full commit hash")
      used_commits = {
        value
        for item in queue
        for phase_type, value in zip(item.types, item.phase_evidence)
        if phase_type in {"code", "docs"} and value is not None
      }
      if commit_hash in used_commits:
        raise ValueError(
          "Commit hash already used by another task phase; use a unique commit"
        )
      try:
        evidence = self._commit_verifier(commit_hash)
      except DirtyTreeError:
        raise
      except GitCheckError as exc:
        raise ValueError(str(exc)) from exc
      if evidence in used_commits:
        raise ValueError(
          "Commit hash already used by another task phase; use a unique commit"
        )
    elif commit_hash is not None:
      raise ValueError("Only code and docs phases take a commit hash")
    index = task.phase_index
    phase_evidence = list(task.phase_evidence)
    phase_evidence[index] = evidence
    next_index = index + 1
    return self._copy_task(
      task,
      status="finished" if next_index == len(task.types) else "upcoming",
      phase_index=next_index,
      phase_evidence=phase_evidence,
    )

  @_transactional_task_write
  def finish_phase(self, task_id: str, *, commit_hash: str | None = None) -> Task:
    """Validate the current phase and advance once, without rewinding later phases."""
    queue, target = self._load_queue_and_target(task_id)
    updated = self._complete_phase(target, queue, commit_hash)
    self._replace_in_queue(queue, updated)
    return updated

  @_transactional_task_write
  def remove_task(self, task_id: str) -> bool:
    validate_entity_id(task_id, "Task.id")
    queue = self._tasks.list_tasks()
    remaining = [item for item in queue if item.id != task_id]
    if len(remaining) == len(queue):
      return False
    now = self._clock.now_iso()
    for item in remaining:
      if task_id in item.task_ids:
        item.task_ids = [dep for dep in item.task_ids if dep != task_id]
        item.modified_at = now
    self._tasks.save_task_queue(self._enforce_dependency_order(remaining))
    return True

  @_transactional_task_write
  def rename_task(self, old_task_id: str, new_task_id: str) -> Task:
    validate_entity_id(old_task_id, "Task.id")
    validate_entity_id(new_task_id, "Task.id")
    if old_task_id == new_task_id:
      task, _blocked = self.get_task(old_task_id)
      return task
    _validate_new_entity_id(new_task_id, "Task")
    queue, target = self._load_queue_and_target(old_task_id)
    if self._reports.get_report(new_task_id) is not None:
      raise ValueError(f"Report {new_task_id!r} already exists")
    if any(item.id == new_task_id for item in queue):
      raise ValueError(f"Task {new_task_id!r} already exists")
    now = self._clock.now_iso()
    renamed = self._copy_task(target, id=new_task_id)
    self._validate_details(renamed)
    updated_queue: list[Task] = []
    for item in queue:
      if item.id == old_task_id:
        updated_queue.append(renamed)
        continue
      if old_task_id in item.task_ids:
        item.task_ids = [
          new_task_id if dep_id == old_task_id else dep_id for dep_id in item.task_ids
        ]
        item.modified_at = now
      updated_queue.append(item)
    ordered = self._enforce_dependency_order(updated_queue)
    self._tasks.save_task_queue(ordered)
    return renamed

  @_transactional_task_write
  def unlink_task(
    self, task_id: str, link_id: str, *, link_type: str | None = None
  ) -> Task:
    if link_type is not None:
      return self.mod_links_rm(task_id, link_type, link_id)
    queue, target = self._load_queue_and_target(task_id)
    cleaned_id = link_id.strip()
    updated = self._copy_task(
      target,
      links={
        "msg_ids": [item for item in target.msg_ids if item != cleaned_id],
        "report_ids": [item for item in target.report_ids if item != cleaned_id],
        "task_ids": [item for item in target.task_ids if item != cleaned_id],
        "gate_names": [item for item in target.gate_names if item != cleaned_id],
      },
    )
    if updated.links == target.links:
      raise ValueError(f"Task {task_id!r} has no link {cleaned_id!r}")
    self._replace_in_queue(queue, updated)
    return updated

  @_transactional_task_write
  def mod_deets(self, task_id: str, detail: str) -> Task:
    queue, target = self._load_queue_and_target(task_id)
    self._validate_input_details([detail])
    updated = self._copy_task(target, details=[*target.details, detail])
    self._replace_in_queue(queue, updated)
    return updated

  @_transactional_task_write
  def mod_deets_rm(self, task_id: str, number: int) -> Task:
    queue, target = self._load_queue_and_target(task_id)
    idx = number - 1 if number >= 1 else number
    if idx < 0 or idx >= len(target.details):
      raise IndexError(
        f"Detail number {number} out of range (1..{len(target.details)})"
      )
    new_details = [item for i, item in enumerate(target.details) if i != idx]
    updated = self._copy_task(target, details=new_details)
    self._replace_in_queue(queue, updated)
    return updated

  @_transactional_task_write
  def mod_links_add(
    self,
    task_id: str,
    link_id: str,
    *,
    link_type: str | None = None,
    append_detail: str | None = None,
    remove_detail: int | None = None,
  ) -> Task:
    queue, target = self._load_queue_and_target(task_id)
    cleaned = link_id.strip()
    explicit_type = link_type.strip().lower() if link_type else None
    msg_ids = list(target.msg_ids)
    report_ids = list(target.report_ids)
    task_ids = list(target.task_ids)
    gate_names = list(target.gate_names)
    known_task_ids = {item.id for item in queue}

    if explicit_type == "gate":
      if cleaned not in gate_names:
        gate_names.append(cleaned)
    elif explicit_type in {"task", "dep", "deps"} or (
      explicit_type is None and cleaned in known_task_ids
    ):
      validate_entity_id(cleaned, "Dependency task ID")
      if cleaned not in task_ids:
        task_ids.append(cleaned)
    elif explicit_type == "msg" or (
      explicit_type is None
      and MESSAGE_ID_RE.fullmatch(cleaned)
      and self._reports.get_report(cleaned) is None
    ):
      if cleaned not in msg_ids:
        msg_ids.append(cleaned)
    elif explicit_type in {None, "report"}:
      validate_entity_id(cleaned, "Report or message link ID")
      if cleaned not in report_ids:
        report_ids.append(cleaned)
    else:
      raise ValueError(
        f"Link type must be 'msg', 'report', 'task', or 'gate', got {link_type!r}"
      )

    details = list(target.details)
    if remove_detail is not None:
      if remove_detail < 1 or remove_detail > len(details):
        raise IndexError(
          f"Detail number {remove_detail} out of range (1..{len(details)})"
        )
      details.pop(remove_detail - 1)
    if append_detail is not None:
      details.append(append_detail)
    self._validate_input_details(details, target.details)
    updated = self._copy_task(
      target,
      details=details,
      links={
        "msg_ids": msg_ids,
        "report_ids": report_ids,
        "task_ids": task_ids,
        "gate_names": gate_names,
      },
    )
    added_msg_ids = [msg_id for msg_id in msg_ids if msg_id not in target.msg_ids]
    updated = self._with_message_attachments(updated, added_msg_ids)
    self._validate_message_link_details(target, updated, added_msg_ids)
    if remove_detail is not None or added_msg_ids:
      self._validate_details(updated)
    self._validate_deps_exist(updated, queue)
    self._replace_in_queue(queue, updated)
    return updated

  @_transactional_task_write
  def mod_links_rm(self, task_id: str, link_type: str, link_id: str) -> Task:
    queue, target = self._load_queue_and_target(task_id)
    cleaned_type = link_type.strip().lower()
    cleaned_id = link_id.strip()
    if cleaned_type not in {"msg", "report", "task", "dep", "deps", "gate"}:
      raise ValueError(
        f"Link type must be 'msg', 'report', 'task', or 'gate', got {link_type!r}"
      )
    msg_ids = (
      [item for item in target.msg_ids if item != cleaned_id]
      if cleaned_type == "msg"
      else list(target.msg_ids)
    )
    report_ids = (
      [item for item in target.report_ids if item != cleaned_id]
      if cleaned_type == "report"
      else list(target.report_ids)
    )
    task_ids = (
      [item for item in target.task_ids if item != cleaned_id]
      if cleaned_type in {"task", "dep", "deps"}
      else list(target.task_ids)
    )
    gate_names = (
      [item for item in target.gate_names if item != cleaned_id]
      if cleaned_type == "gate"
      else list(target.gate_names)
    )
    updated = self._copy_task(
      target,
      links={
        "msg_ids": msg_ids,
        "report_ids": report_ids,
        "task_ids": task_ids,
        "gate_names": gate_names,
      },
    )
    self._replace_in_queue(queue, updated)
    return updated

  @_transactional_task_write
  def mod_deps_add(self, task_id: str, dep_task_id: str) -> Task:
    return self.mod_links_add(task_id, dep_task_id, link_type="task")

  @_transactional_task_write
  def mod_deps_rm(self, task_id: str, dep_task_id: str) -> Task:
    return self.mod_links_rm(task_id, "task", dep_task_id)

  @_transactional_task_write
  def mod_scalar(self, task_id: str, field_name: str, value: str) -> Task:
    queue, target = self._load_queue_and_target(task_id)
    key = field_name.strip().lower()
    if key not in {"status", "title", "type", "scope"}:
      raise ValueError(f"Unsupported scalar task field: {field_name!r}")
    if key == "status":
      if value not in {"upcoming", "finished"}:
        raise ValueError(f"Invalid Task.status: {value!r}")
      if value == "finished":
        updated = self._complete_phase(target, queue)
      elif target.status == "finished":
        raise ValueError("Cannot rewind finished task phases")
      else:
        updated = self._copy_task(target)
    elif key == "type":
      if target.phase_index or len(target.types) > 1:
        raise ValueError("Cannot change a task type after setting its phase chain")
      updated = self._copy_task(target, type=value, types=[value])
    else:
      updated = self._copy_task(target, **{key: value})
    self._replace_in_queue(queue, updated)
    return updated

  def mod_types(self, task_id: str, types: list[str]) -> Task:
    """Set the ordered phase types before the first phase is finished."""
    return self.patch_task(task_id, {"types": types})

  @_transactional_task_write
  def patch_task(self, task_id: str, changes: dict[str, Any]) -> Task:
    """Apply validated updates and derive queue rank from priority and size."""
    if "position" in changes or "insertion_order" in changes:
      raise ValueError("Task position and insertion_order are not manually editable")
    if "priority_explicit" in changes:
      raise ValueError("Task priority provenance is not manually editable")
    queue, target = self._load_queue_and_target(task_id)
    if any(
      key in changes
      for key in ("phase_index", "phase_evidence", "current_phase", "remaining_phases")
    ):
      raise ValueError("Task phase progress changes only through finishing a phase")
    if "details" in changes:
      self._validate_input_details(changes["details"], target.details)
    types = changes.get("types", target.types)
    if "type" in changes and "types" not in changes and changes["type"] != target.type:
      if len(target.types) > 1 or target.phase_index:
        raise ValueError("Cannot change a task type after setting its phase chain")
      types = [changes["type"]]
    if types != target.types and target.phase_index:
      raise ValueError("Cannot change phase types after phase progress")
    finishing = changes.get("status") == "finished"
    if target.status == "finished" and changes.get("status") == "upcoming":
      raise ValueError("Cannot rewind finished task phases")
    if "commit_hash" in changes and not finishing:
      raise ValueError("A commit hash can only accompany phase completion")
    raw_links = changes.get("links")
    merged_links = {
      "gate_names": list(
        changes.get(
          "gate_names",
          raw_links.get("gate_names", target.gate_names)
          if isinstance(raw_links, dict)
          else target.gate_names,
        )
      ),
      "msg_ids": list(
        changes.get(
          "msg_ids",
          raw_links.get("msg_ids", target.msg_ids)
          if isinstance(raw_links, dict)
          else target.msg_ids,
        )
      ),
      "report_ids": list(
        changes.get(
          "report_ids",
          raw_links.get("report_ids", target.report_ids)
          if isinstance(raw_links, dict)
          else target.report_ids,
        )
      ),
      "task_ids": list(
        changes.get(
          "task_ids",
          changes.get(
            "deps",
            raw_links.get("task_ids", target.task_ids)
            if isinstance(raw_links, dict)
            else target.task_ids,
          ),
        )
      ),
    }
    updated = self._copy_task(
      target,
      title=str(changes.get("title", target.title)),
      type=str(changes.get("type", target.type)),
      types=types,
      phase_evidence=target.phase_evidence
      if types == target.types
      else [None] * len(types),
      status=target.status if finishing else str(changes.get("status", target.status)),
      scope=str(changes.get("scope", target.scope)),
      details=list(changes.get("details", target.details)),
      priority=changes.get("priority", target.priority),
      priority_explicit=target.priority_explicit or "priority" in changes,
      size=changes.get("size", target.size),
      links=merged_links,
    )
    added_msg_ids = [
      msg_id for msg_id in updated.msg_ids if msg_id not in target.msg_ids
    ]
    updated = self._with_message_attachments(updated, added_msg_ids)
    self._validate_message_link_details(target, updated, added_msg_ids)
    self._validate_details(updated)
    self._validate_deps_exist(updated, queue)
    if finishing:
      updated = self._complete_phase(updated, queue, changes.get("commit_hash"))
    new_queue = [updated if item.id == task_id else item for item in queue]
    self._validate_dependency_priorities(queue, new_queue)
    ordered = self._enforce_dependency_order(new_queue)
    self._tasks.save_task_queue(ordered)
    return updated

  def _load_queue_and_target(self, task_id: str) -> tuple[list[Task], Task]:
    validate_entity_id(task_id, "Task.id")
    queue = self._tasks.list_tasks()
    for item in queue:
      if item.id == task_id:
        return queue, item
    raise KeyError(f"Unknown task ID: {task_id}")

  def _replace_in_queue(self, queue: list[Task], updated: Task) -> None:
    self._validate_details(updated)
    replaced = [updated if item.id == updated.id else item for item in queue]
    self._validate_dependency_priorities(queue, replaced)
    ordered = self._enforce_dependency_order(replaced)
    self._tasks.save_task_queue(ordered)

  @staticmethod
  def _validate_input_details(
    details: list[str], existing: list[str] | None = None
  ) -> None:
    """Allow existing long generated details without accepting new long input."""
    carried = Counter(
      line for line in (existing or []) if len(line) > TASK_DETAIL_MAX_LEN
    )
    for line in details:
      if not isinstance(line, str):
        continue
      text = line.strip()
      if len(text) > TASK_DETAIL_MAX_LEN:
        if carried[text]:
          carried[text] -= 1
        else:
          raise ValueError(
            f"Task detail exceeds {TASK_DETAIL_MAX_LEN} characters ({len(text)}): {text!r}"
          )

  @staticmethod
  def _validate_message_link_details(
    previous: Task, updated: Task, added_msg_ids: list[str]
  ) -> None:
    if added_msg_ids and Counter(previous.details) == Counter(updated.details):
      raise ValueError("Adding a message link requires an added or deleted task detail")

  @staticmethod
  def _validate_details(task: Task) -> None:
    if not task.details:
      raise ValueError("Task requires at least one detail")

  @staticmethod
  def _validate_deps_exist(task: Task, existing_queue: list[Task]) -> None:
    known_ids = {item.id for item in existing_queue if item.id != task.id}
    for dep_id in task.deps:
      if dep_id == task.id:
        raise ValueError(f"Task {task.id!r} cannot depend on itself")
      if dep_id not in known_ids:
        raise ValueError(f"Unknown dependency task ID: {dep_id!r}")

  @staticmethod
  def _validate_dependency_priorities(before: list[Task], after: list[Task]) -> None:
    def invalid_edges(tasks: list[Task]) -> set[tuple[str, str]]:
      by_id = {task.id: task for task in tasks}
      return {
        (task.id, dep_id)
        for task in tasks
        for dep_id in task.deps
        if dep_id in by_id and int(by_id[dep_id].priority[1]) > int(task.priority[1])
      }

    added = invalid_edges(after) - invalid_edges(before)
    if added:
      task_id, dep_id = min(added)
      raise ValueError(
        f"Task {task_id!r} cannot depend on lower-priority task {dep_id!r}"
      )

  @staticmethod
  def _apply_age_priority(task: Task, now: str) -> None:
    task.effective_priority = task.priority
    if task.status != "upcoming" or not task.created_at:
      return
    try:
      age = datetime.fromisoformat(now) - datetime.fromisoformat(task.created_at)
    except (ValueError, TypeError):
      return
    if age >= timedelta(hours=1):
      task.effective_priority = f"P{max(1, int(task.priority[1]) - 1)}"

  def _enforce_dependency_order(self, tasks: list[Task]) -> list[Task]:
    """Rank upcoming tasks while promoting prerequisites and preserving history."""
    now = self._clock.now_iso()
    for task in tasks:
      self._apply_age_priority(task, now)
    all_tasks = {item.id: item for item in tasks}
    cycle_state: dict[str, int] = {}

    def check_cycle(task_id: str) -> None:
      state = cycle_state.get(task_id, 0)
      if state == 2:
        return
      if state == 1:
        raise ValueError(f"Task dependency cycle detected at {task_id!r}")
      cycle_state[task_id] = 1
      for dep_id in all_tasks[task_id].deps:
        if dep_id in all_tasks:
          check_cycle(dep_id)
      cycle_state[task_id] = 2

    for item in tasks:
      check_cycle(item.id)

    upcoming = {item.id: item for item in tasks if item.status != "finished"}
    finished = sorted(
      (item for item in tasks if item.status == "finished"),
      key=lambda task: (
        task.insertion_order if task.insertion_order is not None else len(tasks)
      ),
    )
    visited: dict[str, int] = {}
    ordered: list[Task] = []

    def visit(task_id: str) -> None:
      state = visited.get(task_id, 0)
      if state == 2:
        return
      if state == 1:
        raise ValueError(f"Task dependency cycle detected at {task_id!r}")
      visited[task_id] = 1
      task = upcoming[task_id]
      for dep_id in task.deps:
        if dep_id in upcoming:
          visit(dep_id)
      visited[task_id] = 2
      ordered.append(task)

    for item in sorted(
      upcoming.values(),
      key=lambda task: (
        int(task.effective_priority[1]),
        task.size,
        task.insertion_order if task.insertion_order is not None else len(tasks),
      ),
    ):
      visit(item.id)
    return [*ordered, *finished]


class ReportService:
  """Manage fielded Markdown reports, owner answer submissions, and dismissals."""

  def __init__(
    self,
    reports: ReportRepository,
    tasks: TaskRepository,
    clock: Clock | None = None,
    id_gen: IdGenerator | None = None,
    on_answer_callback: Any | None = None,
    on_ack_callback: Any | None = None,
  ) -> None:
    self._reports = reports
    self._tasks = tasks
    self._clock = clock or UtcClock()
    self._id_gen = id_gen or SecretsHexIdGenerator()
    self._on_answer = on_answer_callback
    self._on_ack = on_ack_callback

  def publish_report(
    self,
    report_id: str,
    title: str,
    body: str,
    *,
    allow_unparsed_markers: bool = False,
    source_file: str = "",
  ) -> Report:
    if not isinstance(source_file, str) or "\n" in source_file or "\r" in source_file:
      raise ValueError("Report source file must be a single-line path")
    blocks, fields = parse_report_markdown(body)
    if any(not field.annotated for field in fields):
      raise ValueError("Fielded reports require question and option comments")
    for field in fields:
      if field.type in {"choice", "checkbox"} and not field.recommended:
        raise ValueError(f"Report field {field.id!r} needs a preselected option")
    warnings = unparsed_strike_lines(body)
    if warnings and not allow_unparsed_markers:
      raise ValueError(f"Possible unparsed strike markers on lines: {warnings}")
    rendered = [
      render_report_markdown(value)
      for block_type, value in blocks
      if block_type == "markdown"
    ]
    saved = self._publish_report(report_id, title, body, fields, source_file)
    verified = self._reports.get_report(report_id)
    if (
      verified is None
      or verified.id != saved.id
      or verified.modified_at != saved.modified_at
      or len(verified.fields) != len(fields)
      or verified.body != body
      or verified.source_file != source_file
    ):
      raise RuntimeError("Report publication verification failed after saving")
    actual_blocks, _ = parse_report_markdown(verified.body)
    if [
      render_report_markdown(value)
      for block_type, value in actual_blocks
      if block_type == "markdown"
    ] != rendered:
      raise RuntimeError("Report rendered verification failed after saving")
    return verified

  @_transactional_task_write
  def _publish_report(
    self,
    report_id: str,
    title: str,
    body: str,
    fields: list[ReportField],
    source_file: str,
  ) -> Report:
    validate_entity_id(report_id, "Report.id")
    existing = self._reports.get_report(report_id)
    if existing is None:
      _validate_new_entity_id(report_id, "Report")
      if self._tasks.get_task(report_id) is not None:
        raise ValueError(f"Task {report_id!r} already exists")
      # Old databases may retain answers from a report removed before orphaning.
      self._reports.orphan_report_answers(report_id)
    preserved_answers = self._reports.list_report_answers(report_id)
    if existing is not None and existing.body != body and preserved_answers:
      raise ValueError(
        "Cannot edit report body after an answer; remove the report before republishing"
      )
    now = self._clock.now_iso()
    report = Report(
      id=report_id,
      title=title,
      body=body,
      fields=fields,
      status="published",
      answers=preserved_answers,
      seen=False,
      modified_at=now,
      updated_at=now,
      pub_at=existing.pub_at if existing else "",
      last_viewed_at=existing.last_viewed_at if existing else "",
      pinned=existing.pinned if existing else False,
      source_file=source_file,
    )
    self._reports.save_report(report)
    return report

  @_transactional_task_write
  def unpublish_report(self, report_id: str) -> Report:
    """Unlink a flagged report while preserving its answer history."""
    report = self.get_report(report_id)
    if report.status not in {"published", "answered"}:
      raise ValueError("Only a published or answered report can be flagged")
    now = self._clock.now_iso()
    report.status = "dismissed"
    report.modified_at = now
    self._reports.save_report(report)
    self._detach_report_links({report_id}, now)
    return report

  @_transactional_task_write
  def mark_seen(self, report_id: str) -> Report:
    report = self.get_report(report_id)
    report.seen = True
    report.last_viewed_at = self._clock.now_iso()
    report.modified_at = report.last_viewed_at
    self._reports.save_report(report)
    return report

  @_transactional_task_write
  def pin_report(self, report_id: str, pinned: bool) -> Report:
    report = self.get_report(report_id)
    if report.pinned != pinned:
      report.pinned = pinned
      report.modified_at = self._clock.now_iso()
      self._reports.save_report(report)
    return report

  @_transactional_task_write
  def mark_ack_seen(self, report_id: str) -> Report:
    report = self.get_report(report_id)
    latest = report.answers[-1] if report.answers else None
    if (
      latest is not None
      and latest.status == "acked"
      and (not report.ack_seen or not report.ack_seen_at)
    ):
      report.ack_seen = True
      report.ack_seen_at = self._clock.now_iso()
      report.modified_at = report.ack_seen_at
      self._reports.save_report(report)
    return report

  def get_report(self, report_id: str) -> Report:
    validate_entity_id(report_id, "Report.id")
    report = self._reports.get_report(report_id)
    if report is None:
      raise KeyError(f"Unknown report ID: {report_id}")
    return report

  def list_reports(self, *, stale_only: bool = False) -> list[Report]:
    all_reports = [
      report for report in self._reports.list_reports() if report.status != "dismissed"
    ]
    if not stale_only:
      return all_reports
    linked: dict[str, list[Task]] = {}
    for task in self._tasks.list_tasks():
      for report_id in task.report_ids:
        linked.setdefault(report_id, []).append(task)
    now = _report_timestamp(self._clock.now_iso())
    stale = []
    for report in all_reports:
      if report.pinned:
        continue
      tasks = linked.get(report.id, [])
      if tasks:
        if all(task.status == "finished" for task in tasks):
          stale.append(report)
        continue
      if (
        report.ack_seen
        and report.ack_seen_at
        and now - _report_timestamp(report.ack_seen_at) >= REPORT_IDLE_INTERVAL
      ):
        stale.append(report)
    return stale

  def submit_answer(
    self, report_id: str, values: dict[str, Any], *, answer_id: str | None = None
  ) -> ReportAnswer:
    answer, created = self._submit_answer(report_id, values, answer_id=answer_id)
    if created and self._on_answer is not None:
      self._on_answer()
    return answer

  @_transactional_task_write
  def _submit_answer(
    self,
    report_id: str,
    values: dict[str, Any],
    *,
    answer_id: str | None = None,
  ) -> tuple[ReportAnswer, bool]:
    report = self.get_report(report_id)
    if not report.fields:
      raise ValueError(f"Report {report_id!r} has no interactive fields to answer")
    cleaned_values = self._validate_submission_values(report, values)
    for field in report.fields:
      value = cleaned_values[field.id]
      if field.type == "choice":
        if isinstance(value, dict) and value.get("custom"):
          cleaned_values[field.id] = value["custom"]
        elif isinstance(value, str):
          if value.lower().startswith("something else:"):
            cleaned_values[field.id] = value.partition(":")[2].strip()
          else:
            cleaned_values[field.id] = field.option_labels.get(value, value)
      elif field.type == "checkbox":
        if isinstance(value, dict):
          selected = [
            field.option_labels.get(item, item) for item in value.get("selected", [])
          ]
          custom = value.get("custom", "")
          cleaned_values[field.id] = selected + ([custom] if custom else [])
        elif isinstance(value, list):
          cleaned_values[field.id] = [
            field.option_labels.get(item, item) for item in value
          ]
    latest = report.answers[-1] if report.answers else None
    if answer_id is not None:
      ans_id = validate_hex_id(answer_id, "ReportAnswer.id")
      if self._reports.get_message(ans_id) is not None and not (
        latest is not None and latest.id == ans_id and latest.values == cleaned_values
      ):
        raise ValueError(f"Message ID {ans_id!r} already exists")
    if latest is not None and latest.values == cleaned_values:
      return latest, False
    now = self._clock.now_iso()
    if answer_id is None:
      for _ in range(64):
        ans_id = self._id_gen.new_hex_id()
        if self._reports.get_message(ans_id) is None:
          break
      else:
        raise RuntimeError("Unable to allocate unique 6-character hex ID")
    created_at = datetime.fromisoformat(now)
    for previous in report.answers:
      previous_at = datetime.fromisoformat(previous.created_at)
      if previous_at >= created_at:
        created_at = previous_at + timedelta(microseconds=1)
      previous_message = self._reports.get_message(previous.id)
      if previous_message is not None and previous_message.blocking:
        previous_message.blocking = False
        previous_message.modified_at = now
        self._reports.save_message(previous_message)
    answer = ReportAnswer(
      id=ans_id,
      report_id=report.id,
      values=cleaned_values,
      report_title=report.title,
      field_labels={field.id: field.label for field in report.fields},
      status="sent",
      acks=[],
      modified_at=now,
      created_at=created_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
    )
    self._reports.save_report_answer(answer)
    report.answers.append(answer)
    report.status = "answered"
    report.seen = True
    report.ack_seen = False
    report.ack_seen_at = ""
    report.modified_at = now
    self._reports.save_report(report)
    return answer, True

  @_transactional_task_write
  def ack_answer(self, report_id: str, answer_id: str, markdown: str) -> ReportAnswer:
    report = self.get_report(report_id)
    validate_hex_id(answer_id, "ReportAnswer.id")
    reply = _validate_ack_markdown(markdown, answer_id)
    now = self._clock.now_iso()
    for ans in report.answers:
      if ans.id == answer_id:
        first_ack = ans.status != "acked"
        ans = self._reports.append_message_ack(answer_id, reply, now).as_report_answer()
        if first_ack:
          ans.task_details_added = _append_answer_details_to_linked_tasks(
            self._tasks, report, ans, now
          )
        if report.answers[-1].id == answer_id:
          report.ack_seen = False
          report.ack_seen_at = ""
          report.modified_at = now
          self._reports.save_report(report)
        if self._on_ack is not None:
          self._on_ack()
        return ans
    raise KeyError(f"Unknown answer ID {answer_id!r} on report {report_id!r}")

  @_transactional_task_write
  def rename_report(self, old_report_id: str, new_report_id: str) -> Report:
    validate_entity_id(old_report_id, "Report.id")
    validate_entity_id(new_report_id, "Report.id")
    if old_report_id == new_report_id:
      return self.get_report(old_report_id)
    _validate_new_entity_id(new_report_id, "Report")
    existing = self.get_report(old_report_id)
    if self._tasks.get_task(new_report_id) is not None:
      raise ValueError(f"Task {new_report_id!r} already exists")
    if self._reports.get_report(new_report_id) is not None:
      raise ValueError(f"Report {new_report_id!r} already exists")
    now = self._clock.now_iso()
    renamed_answers = [
      ReportAnswer(
        id=answer.id,
        report_id=new_report_id,
        values=answer.values,
        status=answer.status,
        acks=answer.acks,
        modified_at=answer.modified_at,
        created_at=answer.created_at,
        report_title=answer.report_title,
        field_labels=answer.field_labels,
      )
      for answer in existing.answers
    ]
    renamed = Report(
      id=new_report_id,
      title=existing.title,
      body=existing.body,
      fields=existing.fields,
      status=existing.status,
      answers=renamed_answers,
      seen=existing.seen,
      ack_seen=existing.ack_seen,
      ack_seen_at=existing.ack_seen_at,
      modified_at=now,
      updated_at=existing.updated_at,
      pub_at=existing.pub_at,
      last_viewed_at=existing.last_viewed_at,
      pinned=existing.pinned,
    )
    self._reports.rename_report_answers(old_report_id, new_report_id, now)
    self._reports.save_report(renamed)
    if hasattr(self._reports, "delete_report"):
      self._reports.delete_report(old_report_id)
    queue = self._tasks.list_tasks()
    changed = False
    for task in queue:
      if old_report_id in task.report_ids or old_report_id in task.phase_evidence:
        task.report_ids = [
          new_report_id if report_id == old_report_id else report_id
          for report_id in task.report_ids
        ]
        task.phase_evidence = [
          new_report_id
          if evidence == old_report_id and task.types[index] == "report"
          else evidence
          for index, evidence in enumerate(task.phase_evidence)
        ]
        task.modified_at = now
        changed = True
    if changed:
      self._tasks.save_task_queue(queue)
    return self.get_report(new_report_id)

  @_transactional_task_write
  def remove_report(self, target: str) -> list[str]:
    """Dismiss one report by ID or all stale reports when target == '--stale'."""
    now = self._clock.now_iso()
    candidates = (
      [rep for rep in self.list_reports(stale_only=True) if rep.status != "dismissed"]
      if target == "--stale"
      else [self.get_report(target)]
    )
    removed: list[str] = []
    for rep in candidates:
      if hasattr(self._reports, "delete_report"):
        self._reports.delete_report(rep.id)
      else:
        self._reports.orphan_report_answers(rep.id)
        rep.status = "dismissed"
        rep.modified_at = now
        self._reports.save_report(rep)
      removed.append(rep.id)

    self._detach_report_links(set(removed), now)
    return removed

  def _detach_report_links(self, report_ids: set[str], now: str) -> None:
    if not report_ids:
      return
    queue = self._tasks.list_tasks()
    changed = False
    for task in queue:
      if any(rid in report_ids for rid in task.report_ids):
        task.report_ids = [rid for rid in task.report_ids if rid not in report_ids]
        task.modified_at = now
        changed = True
    if changed:
      self._tasks.save_task_queue(queue)

  @staticmethod
  def _validate_submission_values(
    report: Report, raw_values: dict[str, Any]
  ) -> dict[str, Any]:
    if not isinstance(raw_values, dict) or not raw_values:
      raise ValueError("Submission values must be a non-empty dictionary")
    fields_by_id = {field.id: field for field in report.fields}
    unknown = sorted(set(raw_values) - set(fields_by_id))
    if unknown:
      raise ValueError(f"Submission contains unknown field IDs: {unknown}")
    cleaned: dict[str, Any] = {}
    for field in report.fields:
      if field.id not in raw_values:
        raise ValueError(f"Missing answer for required field {field.id!r}")
      val = raw_values[field.id]
      if field.type == "text":
        text = str(val or "").strip()
        if not text:
          raise ValueError(f"Text field {field.id!r} requires a non-empty response")
        cleaned[field.id] = text
      elif field.type == "choice":
        cleaned[field.id] = _validate_choice_value(field.id, field.options, val)
      else:
        cleaned[field.id] = _validate_checkbox_value(field.id, field.options, val)
    return cleaned


def _validate_choice_value(
  field_id: str, options: list[str], val: Any
) -> str | dict[str, str]:
  if isinstance(val, dict):
    custom = str(val.get("custom", "")).strip()
    selected = str(val.get("selected", "")).strip()
    if custom:
      return {"selected": selected or "Something else...", "custom": custom}
    val = selected
  text = str(val or "").strip()
  if not text:
    raise ValueError(f"Choice field {field_id!r} requires a selection")
  if text in options:
    return text
  if text.lower().startswith("something else"):
    _, _, custom_part = text.partition(":")
    if not custom_part.strip():
      raise ValueError(f"Choice field {field_id!r} custom option requires text")
    return text
  return {"selected": "Something else...", "custom": text}


def _validate_checkbox_value(
  field_id: str, options: list[str], val: Any
) -> list[str] | dict[str, Any]:
  if isinstance(val, dict):
    selected_raw = val.get("selected", [])
    selected = [
      str(item).strip() for item in selected_raw if str(item).strip() in options
    ]
    custom = str(val.get("custom", "")).strip()
    if not selected and not custom:
      raise ValueError(f"Checkbox field {field_id!r} requires at least one selection")
    if custom:
      return {"selected": selected, "custom": custom}
    return selected
  if not isinstance(val, list) or not val:
    raise ValueError(f"Checkbox field {field_id!r} requires a non-empty list")
  selected_list: list[str] = []
  custom_items: list[str] = []
  for raw_item in val:
    text = str(raw_item).strip()
    if not text:
      continue
    if text in options:
      selected_list.append(text)
    else:
      custom_items.append(text)
  if not selected_list and not custom_items:
    raise ValueError(f"Checkbox field {field_id!r} requires at least one selection")
  if custom_items:
    return {"selected": selected_list, "custom": ", ".join(custom_items)}
  return selected_list
