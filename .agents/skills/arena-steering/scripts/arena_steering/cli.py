"""Command-line interface (`steer`) for Arena Steering."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import __version__, git_safety
from . import pre as pre_mod
from .blocked_bash import append_blocked_bash
from .display_paths import home_relative
from .gates import FileGateStatus, RunCompletionWatcher
from .git_safety import DirtyTreeError, completion_tree_check
from .models import (
  MESSAGE_ID_RE,
  TASK_PRIORITIES,
  TASK_SIZES,
  Message,
  Report,
  ReportAnswer,
  Task,
)
from .server import SteeringApp, create_http_server
from .services import (
  POLL_FRESH_SECONDS,
  MessageService,
  ReportService,
  TaskService,
  with_bible_read,
)
from .store import SqliteStore

BIND_HOST = "0.0.0.0"
SERVE_PORT = 8000
JOB_LOG_HTTP_TIMEOUT = 10
HISTORICAL_PAGE_SIZE = 10
MESSAGE_PAGE_MIN = 5
MESSAGE_PAGE_MAX = 50
PR_CHECK_ANCESTRY_FOLLOWUP = ("steer", "post-exit", "--rc", "0", "--check-ancestry")


def _snippet_words(text: str, max_words: int = 6) -> str:
  words = str(text or "").split()
  if not words:
    return ""
  if len(words) <= max_words:
    return " ".join(words)
  return " ".join(words[:max_words]) + "..."


def resolve_state_dir() -> Path:
  """Return the fixed state directory for this checkout."""
  path = pre_mod.repo_root() / "arena-state"
  path.mkdir(parents=True, exist_ok=True)
  return path


def open_services(
  state_dir: Path,
) -> tuple[SqliteStore, MessageService, TaskService, ReportService]:
  """Open SqliteStore and domain services for a state directory."""
  store = SqliteStore(state_dir / "state.sqlite3")
  messages = MessageService(store, store, store, store)
  tree_check = completion_tree_check(state_dir)
  tasks = TaskService(
    store,
    store,
    messages=store,
    gates=FileGateStatus(state_dir),
    clean_tree_verifier=tree_check,
    on_dirty_finish=messages.flag_dirty_finish if tree_check is not None else None,
  )
  reports = ReportService(
    store,
    store,
    on_answer_callback=messages.notify_new_work,
    on_ack_callback=messages.reset_gate_if_clear,
  )
  return store, messages, tasks, reports


def main(
  argv: list[str] | None = None,
  *,
  out: TextIO | None = None,
  err: TextIO | None = None,
) -> int:
  """Run a `steer` CLI command and return its exit code."""
  stdout = out if out is not None else sys.stdout
  stderr = err if err is not None else sys.stderr
  args_list = list(sys.argv[1:] if argv is None else argv)

  if args_list in (["--version"], ["-V"]):
    print(f"steer {__version__}", file=stdout)
    return 0

  parser = argparse.ArgumentParser(
    prog="steer",
    description="Steer an Arena agent through inbox messages, tasks, and reports.",
  )
  parser.add_argument(
    "-V",
    "--version",
    action="version",
    version=f"steer {__version__}",
  )
  sub = parser.add_subparsers(dest="command")

  serve_p = sub.add_parser("serve", help="Start the local steering HTTP server.")

  read_p = sub.add_parser("read", help="Read pending messages and report answers.")
  poll_p = sub.add_parser(
    "poll", help="Wait for inbox work, a runnable task, or Skip poll."
  )

  ack_p = sub.add_parser("ack", help="Acknowledge a message or report answer.")
  ack_p.add_argument("parts", nargs="+", help="[<id> ...] <markdown>")
  ack_p.add_argument("-i", "--id", dest="task_id", default=None)
  ack_p.add_argument("-t", "--title", dest="task_title", default=None)
  ack_p.add_argument("-p", "--priority", choices=TASK_PRIORITIES)
  ack_p.add_argument("-z", "--size", type=int, choices=TASK_SIZES)
  ack_p.add_argument("-T", "--type", dest="task_types", action="append", default=[])
  ack_p.add_argument("-S", "--scope", dest="task_scope", default="")
  ack_p.add_argument("-d", "--detail", dest="task_details", action="append", default=[])

  msg_p = sub.add_parser("msg", help="List or inspect stored messages.")
  msg_p.add_argument("id", nargs="?", help="Message ID, or list for history.")
  msg_list_p = argparse.ArgumentParser(
    prog="steer msg list", description="List messages newest-first."
  )
  msg_list_p.add_argument("-p", "--page", type=int, default=1)
  msg_list_p.add_argument("-s", "--page-size", type=int, default=MESSAGE_PAGE_MIN)
  msg_show_p = argparse.ArgumentParser(
    prog="steer msg", description="Show one message by ID."
  )
  msg_show_p.add_argument("id")

  tasks_p = sub.add_parser("tasks", help="List upcoming tasks (or finished with -f).")
  tasks_p.add_argument(
    "-f", "--finished", "--full", dest="finished", action="store_true"
  )
  tasks_p.add_argument("-p", "--page", type=int, default=1)

  task_p = sub.add_parser(
    "task",
    help="Show the current task, or create, remove, or modify a task.",
    usage="%(prog)s [-d|--deets] | <id|new|rm|mod|unlink> ...",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=(
      "Commands:\n"
      "  [-d|--deets]                        Show the current task and optional details.\n"
      "  ID                                  Show a task with details.\n"
      "  new ID TITLE -d DETAIL -z SIZE [-p P1..P5] [-T TYPE ...]  Create ordered phases.\n"
      "  finish ID [-c COMMIT]               Validate and advance one phase.\n"
      "  rm ID                               Remove a task.\n"
      "  mod ID FIELD [rm] VALUE             Update a task.\n"
      "  unlink ID [TYPE] LINK               Remove a task link."
    ),
  )

  reports_p = sub.add_parser("reports", help="List active or stale reports (10/page).")
  reports_p.add_argument("-s", "--stale", action="store_true")
  reports_p.add_argument("-p", "--page", type=int, default=1)

  report_p = sub.add_parser(
    "report",
    help="Show, publish, or remove a report.",
    usage="%(prog)s <id|pub|pin|unpin|rm> ...",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=(
      "Commands:\n"
      "  ID                          Show a report.\n"
      "  pub FILE -i ID -t TITLE     Publish a report.\n"
      "  pin ID                      Pin a report.\n"
      "  unpin ID                    Unpin a report.\n"
      "  rm ID [ID ...] | --stale    Remove reports."
    ),
  )

  clear_p = sub.add_parser(
    "clear", help="Clear database state while preserving timezone and files."
  )

  survey_p = sub.add_parser("survey", help="Inspect or submit bible survey answers.")
  survey_p.add_argument("entries", nargs="*", help="JSON array submission")
  survey_p.add_argument(
    "-f",
    "--file",
    default=None,
    help="Read survey JSON array submission from file.",
  )
  survey_p.add_argument(
    "--template",
    action="store_true",
    help="Print the unfilled JSON array template.",
  )

  ren_p = sub.add_parser("ren", help="Move a task or report to a new ID.")
  ren_p.add_argument("old_id")
  ren_p.add_argument("new_id")

  pr_update_p = sub.add_parser(
    "pr-update", help="Update a pull request via the GitHub REST API and verify parity."
  )
  pr_update_p.add_argument("-n", "--number", type=int, required=True)
  pr_update_p.add_argument("-t", "--title", required=True)
  pr_update_p.add_argument("-f", "--body-file", required=True)

  job_log_p = sub.add_parser(
    "job-log", help="Print a fetchable signed job log URL (or a run page URL)."
  )
  job_log_p.add_argument("-j", "--job", dest="job_id", default=None)
  job_log_p.add_argument("-r", "--run", dest="run_id", default=None)

  pre_p = sub.add_parser("pre", help="Run the pre-command pipeline for a shell line.")
  pre_p.add_argument("-l", "--line", default="", help="Whole shell line.")
  pre_p.add_argument(
    "-c", "--command", dest="command_text", default="", help="Command the trap reached."
  )
  pre_p.add_argument("-s", "--shell", default="", help="Shell name, such as bash.")
  pre_p.add_argument(
    "-d", "--done", default="", help="Steps this shell already finished."
  )

  post_exit_p = sub.add_parser(
    "post-exit", help="Watch PR checks after a successful shell push or PR creation."
  )
  post_exit_p.add_argument("--line", default="", help="The completed shell line.")
  post_exit_p.add_argument("--rc", type=int, required=True, help="Shell exit status.")
  post_exit_p.add_argument("--elapsed", type=float, default=0, help="Elapsed seconds.")
  post_exit_p.add_argument(
    "--check-ancestry", action="store_true", help=argparse.SUPPRESS
  )

  skills_p = sub.add_parser("skills", help="List skill names, descriptions, and paths.")

  worktree_p = sub.add_parser(
    "worktree",
    help="Manage native git worktrees under .worktrees/.",
    usage="%(prog)s <add|list|remove|prune|collect> ...",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=(
      "Commands:\n"
      "  add NAME [-b BRANCH]    Add a worktree.\n"
      "  list                    List worktrees.\n"
      "  remove NAME             Remove a worktree.\n"
      "  prune                   Prune worktree records.\n"
      "  collect NAME            Print a worktree patch."
    ),
  )
  run_p = sub.add_parser(
    "run",
    help="Start detached runs and read their logs.",
    usage="%(prog)s <start|logs|pr-checks> ...",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=(
      "Commands:\n"
      "  start [-t TASK] [-a RUN] NAME -- COMMAND    Start a detached run.\n"
      "  logs NAME [-n LINES]                        Read run status and logs.\n"
      "  pr-checks [-n NUMBER]                       Watch PR checks."
    ),
  )

  remaining = args_list
  if not remaining:
    parser.print_help(file=stdout)
    return 0

  cmd = remaining[0]
  cmd_args = remaining[1:]
  if cmd == "post-exit":
    parsed = post_exit_p.parse_args(cmd_args)
    try:
      return _run_post_exit(parsed, resolve_state_dir(), stdout)
    except (OSError, sqlite3.Error, ValueError):
      return 0  # The exit hook must never change the completed line's status.
  group_parsers = {
    "task": task_p,
    "report": report_p,
    "worktree": worktree_p,
    "run": run_p,
  }
  if cmd in group_parsers and cmd_args in (["-h"], ["--help"]):
    group_parsers[cmd].print_help(file=stdout)
    return 0

  resolved_state_dir: Path | None = None
  try:
    resolved_state_dir = resolve_state_dir()
    if cmd == "pre" and not (resolved_state_dir / "state.sqlite3").is_file():
      parsed = pre_p.parse_args(cmd_args)
      return _pre_state_unavailable(
        parsed, "state database is missing", stdout, state_dir=resolved_state_dir
      )
    store, messages, tasks, reports = open_services(resolved_state_dir)
  except (OSError, sqlite3.Error) as exc:
    if cmd == "pre":
      parsed = pre_p.parse_args(cmd_args)
      return _pre_state_unavailable(
        parsed, str(exc), stdout, state_dir=resolved_state_dir
      )
    print(f"Steer error: {exc}", file=stderr)
    return 1
  try:
    if cmd != "serve":
      try:
        messages.touch_agent()
      except (OSError, sqlite3.Error):
        # Activity is advisory and must not block an otherwise readable inbox.
        pass
    if cmd not in {"poll", "pre", "serve"}:
      messages.clear_poll_repeat()
    if cmd == "serve":
      serve_p.parse_args(cmd_args)
      app = SteeringApp(store)
      httpd = create_http_server(app, host=BIND_HOST, port=SERVE_PORT)
      port = int(httpd.server_address[1])
      server_file = resolved_state_dir / "server.json"
      server_file.write_text(
        json.dumps({"host": BIND_HOST, "port": port}, indent=2),
        encoding="utf-8",
      )
      print(f"Steering server listening on http://{BIND_HOST}:{port}", file=stdout)
      try:
        httpd.serve_forever()
      finally:
        server_file.unlink(missing_ok=True)
        httpd.server_close()
      return 0
    if cmd == "pre":
      parsed = pre_p.parse_args(cmd_args)
      try:
        return _run_pre(parsed, messages, store, stdout)
      except (OSError, sqlite3.Error) as exc:
        return _pre_state_unavailable(
          parsed, str(exc), stdout, state_dir=resolved_state_dir
        )
    if cmd == "skills":
      skills_p.parse_args(cmd_args)
      return _run_skills(stdout)
    if cmd == "read":
      read_p.parse_args(cmd_args)
      _print_restore_notice(store, stdout)
      delivered = messages.read_pending(mark_seen=True)
      _print_delivered(delivered, stdout, store.get_meta("timezone", "") or "")
      if delivered["messages"] or delivered["report_answers"]:
        print("If a message asks for work or details, add it to a task.", file=stdout)
      messages.reset_gate_if_clear()
      return 0
    if cmd == "poll":
      poll_p.parse_args(cmd_args)
      _print_restore_notice(store, stdout)
      violation = messages.check_repeat_poll()
      if violation is not None:
        print(violation.body, file=stdout)
        return 2

      listed_tasks = tasks.list_tasks()
      initial_task = next((task for task, blocked in listed_tasks if not blocked), None)
      gate_status = FileGateStatus(resolved_state_dir)
      run_watcher = RunCompletionWatcher(resolved_state_dir)
      if initial_task is None and not messages.has_pending_deliverables():
        running = set(gate_status.running_names())
        linked_runs = dict.fromkeys(
          name
          for task, _ in listed_tasks
          for name in task.gate_names
          if name in running
        )
        for name in linked_runs:
          print(f"running: {name}", file=stdout, flush=True)

      started = time.monotonic()
      delivered = messages.poll(
        timeout_seconds=1799.0,
        initial_task=initial_task,
        live_run=lambda: bool(gate_status.running_names()),
        finished_run=run_watcher.finished,
      )
      messages.record_poll_duration(time.monotonic() - started)
      if delivered["messages"] or delivered["report_answers"]:
        _print_delivered(delivered, stdout, store.get_meta("timezone", "") or "")
      if verdicts := delivered.get("run_verdicts", []):
        for name, exit_code in verdicts:
          print(f"run {name} done: exit {exit_code}", file=stdout)
        print("DO NOT END TURN. POLL AGAIN.", file=stdout)
        messages.request_poll_followup()
      elif delivered["status"] == "timeout":
        print("Poll waited fully. End turn.", file=stdout)
      elif delivered["status"] == "skipped":
        print("User skipped poll. End turn.", file=stdout)
      elif (task := delivered["next_task"]) is not None and not (
        delivered["messages"] or delivered["report_answers"]
      ):
        print(
          f"DO NOT END TURN, CONTINUE TASK: {task.title} | {task.id} | {task.type}",
          file=stdout,
        )
      messages.reset_gate_if_clear()
      return 0
    if cmd == "ack":
      parsed = ack_p.parse_args(cmd_args)
      return _run_ack(parsed, messages, tasks, store, stdout)
    if cmd == "msg":
      return _run_msg(cmd_args, msg_list_p, msg_show_p, messages, stdout)
    if cmd == "tasks":
      parsed = tasks_p.parse_args(cmd_args)
      all_items = tasks.list_tasks(include_finished=parsed.finished)
      if parsed.finished:
        finished_only = [(t, b) for t, b in all_items if t.status == "finished"]
        page = max(1, int(parsed.page))
        start = (page - 1) * HISTORICAL_PAGE_SIZE
        paged = finished_only[start : start + HISTORICAL_PAGE_SIZE]
        _print_task_list(paged, stdout)
      else:
        _print_task_list(all_items, stdout)
      return 0
    if cmd == "task":
      return _run_task(cmd_args, tasks, stdout)
    if cmd == "reports":
      parsed = reports_p.parse_args(cmd_args)
      all_reports = reports.list_reports(stale_only=parsed.stale)
      page = max(1, int(parsed.page))
      start = (page - 1) * HISTORICAL_PAGE_SIZE
      _print_reports_list(all_reports[start : start + HISTORICAL_PAGE_SIZE], stdout)
      return 0
    if cmd == "report":
      return _run_report(cmd_args, reports, store, stdout)
    if cmd == "clear":
      clear_p.parse_args(cmd_args)
      store.clear()
      print("Cleared database state; timezone and files preserved.", file=stdout)
      return 0
    if cmd == "survey":
      parsed = survey_p.parse_args(cmd_args)
      return _run_survey(parsed, messages, stdout, resolved_state_dir)
    if cmd == "ren":
      parsed = ren_p.parse_args(cmd_args)
      return _run_rename(parsed, tasks, reports, stdout)
    if cmd == "pr-update":
      parsed = pr_update_p.parse_args(cmd_args)
      return _run_pr_update(parsed, store, stdout)
    if cmd == "job-log":
      parsed = job_log_p.parse_args(cmd_args)
      return _run_job_log(parsed, stdout)
    if cmd == "worktree":
      return _run_worktree(cmd_args, stdout)
    if cmd == "run":
      return _run_run(cmd_args, resolved_state_dir, stdout, tasks, store)
    if cmd == "init":
      raise ValueError(
        "steer has no init --tz; the browser sets timezone with POST /timezone"
      )

    parsed_root = parser.parse_args(remaining)
    if not parsed_root.command:
      parser.print_help(file=stdout)
    return 0
  except DirtyTreeError as exc:
    print(f"Steer error: {exc}", file=stderr)
    return 1
  except (
    KeyError,
    ValueError,
    IndexError,
    OSError,
    RuntimeError,
    sqlite3.Error,
  ) as exc:
    msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    print(f"Steer error: {msg}", file=stderr)
    return 1
  finally:
    store.close()


def _run_ack(
  parsed: argparse.Namespace,
  messages: MessageService,
  tasks: TaskService,
  store: SqliteStore,
  out: TextIO,
) -> int:
  parts: list[str] = parsed.parts
  target_ids: list[str | None] = list(parts[:-1]) or [None]
  markdown = parts[-1]
  if any(
    target is not None and not MESSAGE_ID_RE.fullmatch(target) for target in target_ids
  ):
    raise ValueError("Usage: steer ack [<6-char-hex-id> ...] '<markdown>'")

  created = None
  with store.transaction():
    if parsed.task_id:
      if len(target_ids) != 1:
        raise ValueError("Task creation on ack requires one target")
      if not parsed.task_title:
        raise ValueError("Task creation on ack requires -t/--title")
      if parsed.size is None:
        raise ValueError("Task creation on ack requires -z/--size")
      target_type, target = messages.resolve_ack_target(target_ids[0])
      created = tasks.create_task(
        parsed.task_id,
        parsed.task_title,
        size=parsed.size,
        priority=parsed.priority,
        type=parsed.task_types[0] if parsed.task_types else "code",
        types=parsed.task_types or None,
        scope=parsed.task_scope,
        details=parsed.task_details,
        msg_ids=[target.id] if target_type == "message" else [],
        report_ids=[target.report_id] if target_type == "report_answer" else [],
      )
      target_ids = [target.id]
    acked = [messages.ack(target, markdown)[1] for target in target_ids]
  print(f"ok {' '.join(item.id for item in acked)}", file=out)
  for item in acked:
    if isinstance(item, Message):
      print("If msg asks for work or details, add to task", file=out)
      print(f"steer task mod <task-id> links msg {item.id} -A '<detail>'", file=out)
    if isinstance(item, ReportAnswer):
      for task_id, details in item.task_details_added.items():
        print(f"Added to task {task_id}:", file=out)
        for detail in details:
          print(f"- {detail}", file=out)
  if created:
    _, blocked = tasks.get_task(created.id)
    _print_single_task(created, blocked, out)
  return 0


def _run_msg(
  args: list[str],
  list_parser: argparse.ArgumentParser,
  show_parser: argparse.ArgumentParser,
  messages: MessageService,
  out: TextIO,
) -> int:
  if not args or args[0] == "list":
    parsed = list_parser.parse_args(args[1:] if args else [])
    if not MESSAGE_PAGE_MIN <= parsed.page_size <= MESSAGE_PAGE_MAX:
      raise ValueError("Message page size must be between 5 and 50")
    items = messages.list_messages(
      unread_only=False,
      include_hidden=True,
      newest_first=True,
      page=parsed.page,
      page_size=parsed.page_size,
    )
    if not items:
      print("No messages.", file=out)
      return 0
    for item in items:
      snippet = _snippet_words(item.body, max_words=6)
      print(f"{item.id} {snippet}".rstrip(), file=out)
    return 0
  parsed = show_parser.parse_args(args)
  item = messages.get_message(parsed.id)
  _print_message(item, out)
  return 0


def _run_task(args: list[str], tasks: TaskService, out: TextIO) -> int:
  if not args or args in (["-d"], ["--deets"]):
    current = next(
      (
        (task, blocked)
        for task, blocked in tasks.list_tasks()
        if task.status == "upcoming" and not blocked
      ),
      None,
    )
    if current is None:
      print("No current task.", file=out)
    else:
      task, blocked = current
      _print_single_task(task, blocked, out, include_details=bool(args))
    return 0
  subcmd = args[0]
  if subcmd == "new":
    new_p = argparse.ArgumentParser(prog="steer task new")
    new_p.add_argument("id")
    new_p.add_argument("title")
    new_p.add_argument("-p", "--priority", choices=TASK_PRIORITIES)
    new_p.add_argument("-z", "--size", type=int, choices=TASK_SIZES, required=True)
    new_p.add_argument("-T", "--type", dest="types", action="append", default=[])
    new_p.add_argument("-s", "--status", default="upcoming")
    new_p.add_argument("-S", "--scope", default="")
    new_p.add_argument("-d", "--detail", action="append", default=[])
    new_p.add_argument("-m", "--msg", action="append", default=[])
    new_p.add_argument("-r", "--report", action="append", default=[])
    new_p.add_argument("-g", "--gate", action="append", default=[])
    new_p.add_argument(
      "-k", "--task", "--dep", dest="task_ids", action="append", default=[]
    )
    parsed = new_p.parse_args(args[1:])
    task = tasks.create_task(
      parsed.id,
      parsed.title,
      priority=parsed.priority,
      size=parsed.size,
      type=parsed.types[0] if parsed.types else "code",
      types=parsed.types or None,
      status=parsed.status,
      scope=parsed.scope,
      details=parsed.detail,
      links={
        "msg_ids": parsed.msg,
        "report_ids": parsed.report,
        "task_ids": parsed.task_ids,
        "gate_names": parsed.gate,
      },
    )
    _, blocked = tasks.get_task(task.id)
    _print_single_task(task, blocked, out)
    return 0

  if subcmd == "finish":
    finish_p = argparse.ArgumentParser(prog="steer task finish")
    finish_p.add_argument("id")
    finish_p.add_argument("-c", "--commit", default=None)
    parsed = finish_p.parse_args(args[1:])
    task = tasks.finish_phase(parsed.id, commit_hash=parsed.commit)
    _, blocked = tasks.get_task(task.id)
    _print_single_task(task, blocked, out)
    return 0

  if subcmd == "rm":
    if len(args) != 2:
      raise ValueError("Usage: steer task rm <id>")
    removed = tasks.remove_task(args[1])
    if not removed:
      raise KeyError(f"Unknown task ID: {args[1]}")
    print(f"Removed task {args[1]}", file=out)
    return 0

  if subcmd == "mod":
    return _run_task_mod(args[1:], tasks, out)

  if subcmd == "unlink":
    if len(args) == 3:
      updated = tasks.unlink_task(args[1], args[2])
    elif len(args) == 4:
      updated = tasks.unlink_task(args[1], args[3], link_type=args[2])
    else:
      raise ValueError(
        "Usage: steer task unlink <id> [<msg|report|task|gate>] <link-id>"
      )
    _, blocked = tasks.get_task(updated.id)
    _print_single_task(updated, blocked, out)
    return 0

  if len(args) == 1:
    task, blocked = tasks.get_task(subcmd)
    _print_single_task(task, blocked, out)
    return 0

  raise ValueError(f"Unrecognized task arguments: {args}")


def _run_task_mod(args: list[str], tasks: TaskService, out: TextIO) -> int:
  if len(args) < 3:
    raise ValueError("Usage: steer task mod <id> <field> [rm] <value>")
  task_id, field = args[0], args[1].lower()
  rest = args[2:]

  if field == "deets":
    if rest[0] == "rm":
      if len(rest) != 2:
        raise ValueError("Usage: steer task mod <id> deets rm <number>")
      updated = tasks.mod_deets_rm(task_id, int(rest[1]))
    else:
      updated = tasks.mod_deets(task_id, " ".join(rest))
  elif field == "links":
    if rest[0] == "rm":
      if len(rest) != 3:
        raise ValueError(
          "Usage: steer task mod <id> links rm <msg|report|task|gate> <id>"
        )
      updated = tasks.mod_links_rm(task_id, rest[1], rest[2])
    else:
      link_parser = argparse.ArgumentParser(prog="steer task mod links")
      link_parser.add_argument("link", nargs="+")
      link_parser.add_argument("-A", "--append", dest="append_detail")
      link_parser.add_argument("--delete", dest="remove_detail", type=int)
      parsed = link_parser.parse_args(rest)
      if len(parsed.link) == 2 and parsed.link[0].lower() in {
        "msg",
        "report",
        "task",
        "dep",
        "gate",
      }:
        link_type, link_id = parsed.link
      elif len(parsed.link) == 1:
        link_type, link_id = None, parsed.link[0]
      else:
        raise ValueError(
          "Usage: steer task mod <id> links [msg|report|task|gate] <link-id> [-A detail] [--delete number]"
        )
      updated = tasks.mod_links_add(
        task_id,
        link_id,
        link_type=link_type,
        append_detail=parsed.append_detail,
        remove_detail=parsed.remove_detail,
      )
  elif field == "deps":
    if rest[0] == "rm":
      if len(rest) != 2:
        raise ValueError("Usage: steer task mod <id> deps rm <task-id>")
      updated = tasks.mod_deps_rm(task_id, rest[1])
    else:
      if len(rest) != 1:
        raise ValueError("Usage: steer task mod <id> deps <task-id>")
      updated = tasks.mod_deps_add(task_id, rest[0])
  elif field == "types":
    updated = tasks.mod_types(task_id, rest)
  elif field == "status" and rest[0] == "finished":
    if len(rest) > 2:
      raise ValueError("Usage: steer task mod <id> status finished [commit-hash]")
    updated = tasks.finish_phase(
      task_id, commit_hash=rest[1] if len(rest) == 2 else None
    )
  elif field in {"priority", "size"}:
    if len(rest) != 1:
      raise ValueError(f"Usage: steer task mod <id> {field} <value>")
    updated = tasks.patch_task(
      task_id, {field: int(rest[0]) if field == "size" else rest[0]}
    )
  elif field in {"status", "title", "type", "scope"}:
    updated = tasks.mod_scalar(task_id, field, " ".join(rest))
  else:
    raise ValueError(
      f"Unknown task mod field {field!r}; expected deets, links, deps, "
      "priority, size, status, title, type, types, or scope"
    )

  _, blocked = tasks.get_task(updated.id)
  _print_single_task(updated, blocked, out)
  return 0


def _run_rename(
  parsed: argparse.Namespace,
  tasks: TaskService,
  reports: ReportService,
  out: TextIO,
) -> int:
  try:
    renamed = tasks.rename_task(parsed.old_id, parsed.new_id)
  except KeyError:
    renamed_report = reports.rename_report(parsed.old_id, parsed.new_id)
    print(f"Renamed report {parsed.old_id} -> {renamed_report.id}", file=out)
    return 0
  print(f"Renamed task {parsed.old_id} -> {renamed.id}", file=out)
  _, blocked = tasks.get_task(renamed.id)
  _print_single_task(renamed, blocked, out)
  return 0


def _run_gh(args: list[str], *, input_text: str | None = None) -> str:
  result = subprocess.run(
    args,
    input=input_text,
    check=False,
    capture_output=True,
    text=True,
  )
  if result.returncode != 0:
    detail = (result.stderr or result.stdout or "").strip()
    raise ValueError(f"{' '.join(args)} failed: {detail}")
  return result.stdout


def _pr_session_id_hits(store: SqliteStore, title: str, body: str) -> list[str]:
  """Find stored message, answer, task, or report IDs at token boundaries."""
  tokens = {
    *(message.id for message in store.list_messages(include_hidden=True)),
    *(task.id for task in store.list_tasks()),
    *(report.id for report in store.list_reports()),
  }
  hits = []
  for label, text in (("title", title), ("body", body)):
    for line_no, line in enumerate(text.splitlines(), 1):
      for token in sorted(tokens):
        if re.search(rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])", line):
          hits.append(f"{label}:{line_no}: {token}")
  return hits


def _run_pr_update(parsed: argparse.Namespace, store: SqliteStore, out: TextIO) -> int:
  body_path = Path(parsed.body_file)
  if not body_path.is_file():
    raise ValueError(f"PR body file does not exist: {body_path}")
  expected_body = body_path.read_text(encoding="utf-8")
  hits = _pr_session_id_hits(store, parsed.title, expected_body)
  if hits:
    raise ValueError(
      with_bible_read(
        "VIOLATION: Cited session-local ID in PR data. "
        "If name clash `steer ren OLD_ID NEW_ID` and retry."
        "\nLocations:\n  " + "\n  ".join(hits)
      )
    )
  endpoint = f"repos/{{owner}}/{{repo}}/pulls/{parsed.number}"
  _run_gh(
    ["gh", "api", endpoint, "-X", "PATCH", "--input", "-"],
    input_text=json.dumps({"title": parsed.title, "body": expected_body}),
  )
  current = json.loads(_run_gh(["gh", "api", endpoint]))
  if (
    not isinstance(current, dict)
    or current.get("title") != parsed.title
    or (current.get("body") or "") != expected_body
  ):
    raise ValueError(f"PR #{parsed.number} title/body did not verify after update")
  print(f"Updated PR #{parsed.number}: {parsed.title}", file=out)
  return 0


def _gh_url(args: list[str]) -> tuple[str, str]:
  """Return a log URL and the reason it is missing, when it is."""
  try:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
  except OSError as exc:
    return "", f"gh could not start: {exc}"
  if result.returncode != 0:
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return "", f"gh exited {result.returncode}: {detail[0] if detail else 'no detail'}"
  url = result.stdout.strip()
  if not url:
    return "", "gh printed an empty URL"
  return url, ""


class _NoLogRedirect(urllib.request.HTTPRedirectHandler):
  """Leave GitHub's short-lived signed log Location available to the caller."""

  def redirect_request(self, request, fp, code, msg, headers, newurl):
    return None


def _signed_job_log_url(job_id: str) -> tuple[str, str]:
  """Resolve the job-logs API's 302 without downloading or printing credentials."""
  if not re.fullmatch(r"[1-9][0-9]*", job_id):
    return "", "job ID must be a positive integer"
  repo, reason = _gh_url(
    ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
  )
  if not repo:
    return "", f"repository lookup failed: {reason}"
  if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
    return "", "repository lookup returned an invalid name"
  try:
    token = _run_gh(["gh", "auth", "token"]).strip()
  except (OSError, ValueError) as exc:
    return "", f"GitHub authentication failed: {exc}"
  if not token:
    return "", "GitHub authentication returned an empty token"
  request = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs",
    headers={
      "Authorization": f"Bearer {token}",
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  )
  try:
    with urllib.request.build_opener(_NoLogRedirect()).open(
      request, timeout=JOB_LOG_HTTP_TIMEOUT
    ) as response:
      return "", f"GitHub API returned HTTP {response.status} without a redirect"
  except urllib.error.HTTPError as exc:
    if exc.code != 302:
      return "", f"GitHub API returned HTTP {exc.code}"
    location = exc.headers.get("Location", "")
    try:
      url = urllib.parse.urlsplit(location)
    except ValueError:
      return "", "GitHub API returned an invalid log redirect"
    if (
      url.scheme != "https"
      or not url.hostname
      or any(char in location for char in "\r\n")
    ):
      return "", "GitHub API returned a non-HTTPS log redirect"
    return location, ""
  except (urllib.error.URLError, TimeoutError, OSError) as exc:
    return "", f"GitHub API log lookup failed: {exc}"


def _run_job_log(parsed: argparse.Namespace, out: TextIO) -> int:
  """Print a fetchable job log URL or run page, else the log text and reason."""
  if bool(parsed.job_id) == bool(parsed.run_id):
    raise ValueError("Usage: steer job-log -j <job-id> or -r <run-id>")
  if parsed.run_id:
    url_args = [
      "gh",
      "run",
      "view",
      str(parsed.run_id),
      "--json",
      "url",
      "--jq",
      ".url",
    ]
    log_args = ["gh", "run", "view", str(parsed.run_id), "--log"]
    target = f"run {parsed.run_id}"
    url, reason = _gh_url(url_args)
  else:
    log_args = ["gh", "run", "view", "--job", str(parsed.job_id), "--log"]
    target = f"job {parsed.job_id}"
    url, reason = _signed_job_log_url(str(parsed.job_id))
  if url:
    print(url, file=out)
    return 0
  try:
    log_text = _run_gh(log_args)
  except (ValueError, OSError) as exc:
    print(f"job-log: no URL for {target} ({reason}); log text failed: {exc}", file=out)
    return 1
  print(log_text, end="" if log_text.endswith("\n") else "\n", file=out)
  print(f"job-log: no URL for {target} ({reason})", file=out)
  return 0


def _run_git(args: list[str]) -> str:
  result = subprocess.run(
    args,
    check=False,
    capture_output=True,
    text=True,
  )
  if result.returncode != 0:
    detail = (result.stderr or result.stdout or "").strip()
    raise ValueError(f"{' '.join(args)} failed: {detail}")
  return result.stdout


def _safe_local_name(name: str) -> str:
  cleaned = str(name or "").strip()
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", cleaned):
    raise ValueError(f"Invalid name: {name!r}")
  return cleaned


def _call_count(store: SqliteStore) -> int:
  """Read how many shell lines ran since the inbox was last read."""
  raw = store.get_meta(pre_mod.CALL_COUNT_META, "0") or "0"
  try:
    return int(raw)
  except ValueError:
    return 0


def _bump_call_count(store: SqliteStore) -> int:
  """Record one more shell line and return the new count."""
  with store.transaction():
    count = _call_count(store) + 1
    store.set_meta(pre_mod.CALL_COUNT_META, str(count))
    return count


def _count_outer_bash(state_dir: Path, line: str, *, routed: bool) -> None:
  """Count one completed outer agent shell, never its routed child shell."""
  if not line.strip() or routed:
    return
  store, messages, _, _ = open_services(state_dir)
  try:
    pending_ids = messages.pending_ids()
    if not pending_ids:
      messages.reset_gate_if_clear()
      return
    ctx = pre_mod.PreContext(
      line=line,
      command=pre_mod.shell_line(line),
      shell="bash",
      pending_ids=pending_ids,
      call_count=_call_count(store),
      routed=routed,
      cwd=Path.cwd(),
      checkout_root=pre_mod.repo_root(),
    )
    if pre_mod.bash_command_policy(ctx)[0]:
      _bump_call_count(store)
  finally:
    store.close()


def _audit_blocked_bash(
  state_dir: Path, *, line: str, command: str, blocked_by: str, out: TextIO
) -> None:
  """Keep a failed audit write from allowing a blocked shell command."""
  try:
    append_blocked_bash(state_dir, line=line, command=command, blocked_by=blocked_by)
  except (OSError, RuntimeError) as exc:
    print(f"BLOCK AUDIT FAILED: {exc}", file=out)


def _pre_state_unavailable(
  parsed: argparse.Namespace,
  reason: str,
  out: TextIO,
  *,
  state_dir: Path | None = None,
) -> int:
  """Keep work open while state is unreadable, but refuse an unverified push."""
  ctx = pre_mod.PreContext(line=parsed.line, command=parsed.command_text)
  if ctx.is_push():
    if parsed.shell:
      if state_dir is None:
        print("BLOCK AUDIT FAILED: state directory unavailable.", file=out)
      else:
        _audit_blocked_bash(
          state_dir,
          line=parsed.line,
          command=parsed.command_text,
          blocked_by="state-unavailable",
          out=out,
        )
    print(f"PUSH BLOCKED: steering state unavailable ({reason}).", file=out)
    return pre_mod.BLOCK
  print(f"STATE UNAVAILABLE: pre-command gate skipped ({reason}).", file=out)
  return pre_mod.CONTINUE


READ_SET_LAST_META = "read_set_last_at"
READ_SET_INTERVAL_SECONDS = 10 * 60


def _schedule_instruction_check(
  line: str,
  messages: MessageService,
  store: SqliteStore,
  *,
  moment: datetime | None = None,
) -> None:
  """Mint one hidden instruction checkpoint after each elapsed interval."""
  words = pre_mod._line_commands(pre_mod.PreContext(line=line))
  if any(
    len(command) >= 2
    and Path(command[0]).name in {"steer", "arena-preview"}
    and command[1] == "poll"
    for command in words
  ):
    return
  now = moment or datetime.now(UTC)
  with store.transaction():
    try:
      heartbeat = datetime.fromisoformat(store.get_meta("polling_at", "") or "")
      if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
      if 0 <= (now - heartbeat).total_seconds() < POLL_FRESH_SECONDS:
        return
    except ValueError:
      pass
    stamp = store.get_meta(READ_SET_LAST_META)
    if stamp is None:
      store.set_meta(READ_SET_LAST_META, now.isoformat())
      return
    try:
      previous = datetime.fromisoformat(stamp)
      if previous.tzinfo is None:
        previous = previous.replace(tzinfo=UTC)
    except ValueError:
      previous = now
    if (now - previous).total_seconds() < READ_SET_INTERVAL_SECONDS:
      return
    if any(
      item.hidden
      and item.requires_ack
      and item.body.startswith(("10min bible check", "15min bible check"))
      for item in messages.list_messages()
    ):
      return
    root = pre_mod.repo_root()
    paths = [
      root / "ARENA.md",
      root / "AGENTS.md",
      root / ".agents/skills/arena-steering/SKILL.md",
    ]
    listed = "\n".join(f"- `{home_relative(path)}`" for path in paths if path.is_file())
    messages.send_message(
      "10min bible check\n"
      "if bible missing from context:\n"
      f"{listed}\n"
      "Read every listed path in full with read_file and follow its rules.\n"
      "Answer the survey, ack this msg, and continue. "
      "If no bible text was missing, only ack and continue.\n"
      "For the survey, use existing knowledge only. Run `steer survey` and read "
      "the image path it prints, in JSON order.\n"
      "Do not search, fetch, open other files, run code, or ask another system "
      "for answers.\n"
      "Give your best effort. Guesses are allowed; use extremely low confidence "
      "when very unsure. Boolean answers need true or false.\n"
      "Use unknown with confidence 0 only if the whole question is unknown.",
      hidden=True,
      blocking=True,
    )
    store.set_meta(READ_SET_LAST_META, now.isoformat())


def _run_pre_route(
  route: pre_mod.RouteRequest, line: str, store: SqliteStore, out: TextIO
) -> int:
  """Complete a routed line before the trap ends the shell call."""
  route_status = 0
  try:
    if route.kind == "mixed":
      script, name = route.args
      if name:
        _start_detached_run(
          Path(store.db_path).parent, name, ["bash", "-c", script], out=out
        )
        print(f"routed {name}: background run. Verdict on later call.", file=out)
      else:
        env = {**os.environ, "ARENA_STEER_ROUTE": "1"}
        result = subprocess.run(
          ["bash", "-c", script], env=env, capture_output=True, text=True, check=False
        )
        print("_steer_route_output=1", file=out)
        if result.stdout:
          print(
            result.stdout, end="" if result.stdout.endswith("\n") else "\n", file=out
          )
        if result.stderr:
          print(
            result.stderr,
            end="" if result.stderr.endswith("\n") else "\n",
            file=sys.stderr,
          )
        route_status = (
          result.returncode if result.returncode >= 0 else 128 - result.returncode
        )
    elif route.kind == "run":
      name = route.args[0]
      _start_detached_run(
        Path(store.db_path).parent,
        name,
        ["bash", "-c", pre_mod.shell_line(line)],
        out=out,
      )
      print(f"routed {name}: background run. Verdict on later call.", file=out)
    elif route.kind == "pr-update":
      number, title, body_file = route.args
      if _run_pr_update(
        argparse.Namespace(number=int(number), title=title, body_file=body_file),
        store,
        out,
      ):
        raise ValueError("pr-update could not verify this pull request")
    elif route.kind == "job-log":
      log_type, number = route.args
      if _run_job_log(
        argparse.Namespace(
          job_id=number if log_type == "jobs" else None,
          run_id=number if log_type == "runs" else None,
        ),
        out,
      ):
        raise ValueError("job-log could not retrieve this log")
    else:
      raise ValueError(f"Unsupported route: {route.kind}")
  except (ValueError, OSError) as exc:
    if str(exc).startswith("VIOLATION:"):
      print(exc, file=out)
    else:
      print(f"Run failed: {exc}. Original did not run. Fix and retry safely.", file=out)
    return pre_mod.BLOCK
  if route.kind == "mixed" and not route.args[1]:
    print(f"_steer_route_rc={route_status}", file=out)
  return pre_mod.END_CALL


def _run_pre(
  parsed: argparse.Namespace,
  messages: MessageService,
  store: SqliteStore,
  out: TextIO,
) -> int:
  """Run the pre-command pipeline and return its verdict."""
  routed = os.environ.get("ARENA_STEER_ROUTE") == "1"
  if not routed and not pre_mod.arena_maintenance_call(
    parsed.line, parsed.command_text
  ):
    _schedule_instruction_check(parsed.line or parsed.command_text, messages, store)
  pending_ids = messages.pending_ids()
  if not pending_ids:
    messages.reset_gate_if_clear()
  count = _call_count(store) if pending_ids else 0
  text = f"{parsed.line} {parsed.command_text}"
  session_ids: tuple[str, ...] = ()
  renamable_ids: tuple[str, ...] = ()
  if "git" in text and "commit" in text:
    task_ids = {task.id for task in store.list_tasks()}
    report_ids = {report.id for report in store.list_reports()}
    renamable_ids = tuple(sorted(task_ids | report_ids))
    session_ids = tuple(
      sorted(
        {message.id for message in store.list_messages(include_hidden=True)}
        | task_ids
        | report_ids
      )
    )
  ctx = pre_mod.PreContext(
    line=str(parsed.line or ""),
    command=str(parsed.command_text or ""),
    shell=str(parsed.shell or ""),
    done=tuple(str(parsed.done or "").split()),
    pending_ids=pending_ids,
    call_count=count,
    active_runs=FileGateStatus(Path(store.db_path).parent).running_names(),
    routed=routed,
    cwd=Path.cwd(),
    checkout_root=pre_mod.repo_root(),
    session_ids=session_ids,
    renamable_ids=renamable_ids,
  )
  result = pre_mod.run_pipeline(ctx)
  if result.verdict == pre_mod.BLOCK and parsed.shell:
    _audit_blocked_bash(
      Path(store.db_path).parent,
      line=parsed.line,
      command=parsed.command_text,
      blocked_by=result.blocked_by,
      out=out,
    )
  for message in result.messages:
    print(
      with_bible_read(message) if message.startswith("VIOLATION:") else message,
      file=out,
    )
  if (
    result.blocked_by == "gate"
    and ctx.shell
    and not ctx.is_push()
    and ctx.call_count >= pre_mod.GATE_THRESHOLD
  ):
    flag = messages.flag_violation_once(
      "bash-cadence",
      f"VIOLATION: Bash blocked after {pre_mod.GATE_THRESHOLD} calls. "
      "`steer read` and `steer ack` now.",
    )
    print(f"Violation flag: {flag.id}", file=out)
  flag_reasons = {
    "inbox-pipe": ("bash-inbox-pipe", "VIOLATION: Inbox piped."),
    "sleep": (
      "bash-sleep",
      "VIOLATION: Sleep blocked. Use worktree if more task open, else poll.",
    ),
  }
  if ctx.shell and (reason := flag_reasons.get(result.blocked_by)) is not None:
    flag = messages.flag_violation_once(*reason)
    print(f"Violation flag: {flag.id}", file=out)
  if parsed.shell and result.done != ctx.done:
    # The shell evals this line, so a later command in the same shell skips
    # the steps that already ran.
    print(f'_arena_preview_done="{" ".join(result.done)}"', file=out)
  if not routed and not ctx.is_inbox_recovery():
    tool_name = pre_mod.native_tool_name(
      ctx.line, ctx.command, ctx.shell, checkout_root=ctx.checkout_root
    )
    if tool_name:
      try:
        messages.record_tool(tool_name)
      except (OSError, sqlite3.Error):
        # Cadence is advisory and must not change the shell verdict.
        pass
  if result.route is not None:
    routed_verdict = _run_pre_route(
      result.route, parsed.line or parsed.command_text, store, out
    )
    if routed_verdict == pre_mod.BLOCK and parsed.shell:
      _audit_blocked_bash(
        Path(store.db_path).parent,
        line=parsed.line,
        command=parsed.command_text,
        blocked_by="route",
        out=out,
      )
    return routed_verdict
  if (
    ctx.shell
    and not pending_ids
    and result.verdict == pre_mod.CONTINUE
    and ctx.is_single_push()
  ):
    print("_steer_single_push_approved=1", file=out)
  return result.verdict


def _skill_description(skill_md: Path) -> str:
  """Read the frontmatter description without loading the skill body."""
  lines = skill_md.read_text(encoding="utf-8", errors="replace").splitlines()
  if not lines or lines[0].strip() != "---":
    return ""
  for line in lines[1:]:
    if line.strip() == "---":
      break
    if line.startswith("description:"):
      return line.split(":", 1)[1].strip()
  return ""


def _run_skills(out: TextIO) -> int:
  """List repository and installed skills, preferring the installed copy of a name."""
  root = _repo_root()
  found: dict[str, dict[str, str]] = {}
  for source, base in (
    ("repo", root / "skills"),
    ("installed", root / ".agents" / "skills"),
  ):
    if not base.is_dir():
      continue
    for skill_md in sorted(base.glob("*/SKILL.md")):
      name = skill_md.parent.name
      if name in found and source == "repo":
        continue
      found[name] = {
        "name": name,
        "source": source,
        "path": str(skill_md),
        "description": _skill_description(skill_md),
      }
  entries = [found[name] for name in sorted(found)]
  if not entries:
    print("No skills found.", file=out)
    return 0
  for entry in entries:
    print(f"{entry['name']} [{entry['source']}] {entry['path']}", file=out)
    if entry["description"]:
      print(f"  {entry['description']}", file=out)
  return 0


def _repo_root() -> Path:
  top = _run_git(["git", "rev-parse", "--show-toplevel"]).strip()
  return Path(top) if top else Path.cwd()


def _run_worktree(args: list[str], out: TextIO) -> int:
  if not args:
    raise ValueError("Usage: steer worktree <add|list|remove|prune|collect> ...")
  subcmd = args[0]
  if subcmd == "list":
    print(_run_git(["git", "worktree", "list", "--porcelain"]), end="", file=out)
    return 0
  if subcmd == "prune":
    _run_git(["git", "worktree", "prune"])
    print("Pruned worktree records", file=out)
    return 0
  if subcmd == "add":
    add_p = argparse.ArgumentParser(prog="steer worktree add")
    add_p.add_argument("name")
    add_p.add_argument("-b", "--branch", default=None)
    parsed = add_p.parse_args(args[1:])
    name = _safe_local_name(parsed.name)
    target = _repo_root() / ".worktrees" / name
    git_args = ["git", "worktree", "add"]
    if parsed.branch:
      git_args += ["-b", parsed.branch]
    git_args.append(str(target))
    _run_git(git_args)
    print(f"Added worktree {name} at {target}", file=out)
    return 0
  if subcmd == "collect":
    if len(args) != 2:
      raise ValueError("Usage: steer worktree collect <name>")
    name = _safe_local_name(args[1])
    target = _repo_root() / ".worktrees" / name
    if not target.is_dir():
      raise ValueError(f"No worktree at {target}")
    head = _run_git(["git", "-C", str(target), "rev-parse", "HEAD"]).strip()
    base = _run_git(["git", "merge-base", head, "HEAD"]).strip()
    if not base:
      raise ValueError(
        f"No shared base between {name} and HEAD; refuse to guess a diff"
      )
    patch = _run_git(["git", "-C", str(target), "diff", base])
    print(patch, end="", file=out)
    untracked = _run_git(
      ["git", "-C", str(target), "ls-files", "--others", "--exclude-standard"]
    ).strip()
    if untracked:
      print("Untracked files, not in the patch:", file=out)
      print(untracked, file=out)
    return 0
  if subcmd == "remove":
    if len(args) != 2:
      raise ValueError("Usage: steer worktree remove <name>")
    name = _safe_local_name(args[1])
    target = _repo_root() / ".worktrees" / name
    _run_git(["git", "worktree", "remove", str(target)])
    _run_git(["git", "worktree", "prune"])
    print(f"Removed worktree {name}", file=out)
    return 0
  raise ValueError(f"Unknown worktree subcommand: {subcmd!r}")


def _runs_dir(state_dir: Path) -> Path:
  runs = state_dir / "runs"
  runs.mkdir(parents=True, exist_ok=True)
  return runs


def _start_detached_run(
  state_dir: Path,
  name: str,
  command: list[str],
  *,
  after: str | None = None,
  on_finish: list[str] | None = None,
  out: TextIO,
) -> int:
  runs = _runs_dir(state_dir).resolve()
  with sqlite3.connect(runs / ".launch.sqlite3") as lock:
    lock.execute("BEGIN IMMEDIATE")
    return _start_locked_run(
      runs, name, command, after=after, on_finish=on_finish, out=out
    )


def _start_locked_run(
  runs: Path,
  name: str,
  command: list[str],
  *,
  after: str | None,
  on_finish: list[str] | None,
  out: TextIO,
) -> int:
  record_path = runs / f"{name}.json"
  if record_path.exists():
    previous = json.loads(record_path.read_text(encoding="utf-8"))
    if not Path(previous["exit_path"]).is_file():
      raise ValueError(f"Run {name} is already active")
  dependency = None
  if after:
    dep_record_path = runs / f"{after}.json"
    if not dep_record_path.is_file():
      raise ValueError(f"Unknown dependency: {after}")
    dependency = json.loads(dep_record_path.read_text(encoding="utf-8"))
  run_id = uuid.uuid4().hex
  log_path = runs / f"{name}-{run_id}.log"
  exit_path = runs / f"{name}-{run_id}.exit"
  exit_file = shlex.quote(str(exit_path))
  temp_exit_file = shlex.quote(str(exit_path) + ".tmp")
  write_exit = (
    f'printf "%s\\n" "$status" > {temp_exit_file} && mv {temp_exit_file} {exit_file}'
  )
  script_lines: list[str] = []
  if dependency:
    dep_exit = shlex.quote(dependency["exit_path"])
    script_lines.append(f"while [ ! -f {dep_exit} ]; do sleep 0.2; done")
    script_lines.append(
      f'if [ "$(cat {dep_exit})" != "0" ]; then '
      f'echo "dependency {after} did not pass"; '
      f"status=125; {write_exit}; exit 125; fi"
    )
  script_lines.append(f"( {shlex.join(command)} )")
  script_lines.append("status=$?")
  if on_finish:
    script_lines.append(f"( {shlex.join(on_finish)} ) || true")
  script_lines.append(write_exit)
  log_handle = log_path.open("ab")
  try:
    proc = subprocess.Popen(
      ["/bin/sh", "-c", "\n".join(script_lines)],
      env={**os.environ, "ARENA_STEER_ROUTE": "1"},
      stdout=log_handle,
      stderr=subprocess.STDOUT,
      stdin=subprocess.DEVNULL,
      start_new_session=True,
    )
  finally:
    log_handle.close()
  temp_record = runs / f".{name}-{run_id}.json"
  try:
    temp_record.write_text(
      json.dumps(
        {
          "name": name,
          "pid": proc.pid,
          "command": command,
          "after": after,
          "log_path": str(log_path),
          "exit_path": str(exit_path),
        }
      ),
      encoding="utf-8",
    )
    temp_record.replace(record_path)
  except OSError:
    proc.terminate()
    raise
  print(f"Started run {name} (pid {proc.pid})", file=out)
  return 0


def _unpushed_commit_count(root: Path) -> int:
  """Count local commits ahead of the matching origin branch or configured upstream."""
  if not (root / ".git").exists():
    return 0
  try:
    branch = subprocess.run(
      ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
      cwd=root,
      capture_output=True,
      text=True,
      check=False,
      timeout=5,
    )
    if branch.returncode or not branch.stdout.strip():
      return 0
    for target in (f"refs/remotes/origin/{branch.stdout.strip()}", "@{upstream}"):
      result = subprocess.run(
        ["git", "rev-list", "--count", f"{target}..HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
      )
      if result.returncode == 0:
        return max(0, int(result.stdout.strip()))
  except (OSError, subprocess.TimeoutExpired, ValueError):
    pass
  return 0


def _slow_call_hint(line: str, elapsed: float) -> str:
  """Suggest a detached run only for slow ordinary shell work."""
  if elapsed < 10 or pre_mod.PreContext(line=line).is_inbox_recovery():
    return ""
  if any(
    words
    and Path(words[0]).name in {"steer", "arena-preview"}
    and words[1:2] == ["run"]
    for words in pre_mod._line_commands(pre_mod.PreContext(line=line))
  ):
    return ""
  return (
    f"Bash call took {elapsed:.1f}s (>= 10s). "
    "Use `steer run start -t <task-id> <name> -- <cmd>` for slow commands."
  )


def _exit_state_reminders(state_dir: Path) -> list[str]:
  """Read pending, stale, and unpushed work without changing the inbox."""
  if not (state_dir / "state.sqlite3").is_file():
    return []
  store, messages, _, reports = open_services(state_dir)
  try:
    pending = messages.read_pending(mark_seen=False)
    pending_messages = [
      msg for msg in pending["messages"] if msg.type == "message" and msg.requires_ack
    ]
    violations = sum(bool(msg.violation_type) for msg in pending_messages)
    msgs = len(pending_messages) - violations
    answers = len(pending["report_answers"])
    parts = []
    counts = []
    if violations:
      counts.append(f"{violations} violation{'s' if violations != 1 else ''}")
    if msgs:
      counts.append(f"{msgs} msg{'s' if msgs != 1 else ''}")
    if answers:
      counts.append(f"{answers} answer{'s' if answers != 1 else ''}")
    if counts:
      parts.append("unacked " + ", ".join(counts))
    if len(reports.list_reports()) >= 10:
      stale = len(reports.list_reports(stale_only=True))
      if stale:
        parts.append(f"Unpublish {stale} stale report{'s' if stale != 1 else ''}.")
    unpushed = _unpushed_commit_count(pre_mod.repo_root())
    if unpushed:
      if store.get_meta("repo_private") == "1":
        parts.append(
          f"{unpushed} commit/s unpushed. Private repo: push small batches (<=3)."
        )
      else:
        parts.append(
          f"{unpushed} commit/s unpushed. ALWAYS push. NEVER delay for batch."
        )
    return parts
  finally:
    store.close()


def _note_background_ancestry(state_dir: Path) -> None:
  """Notify once per confirmed HEAD/main pair after a background PR check."""
  pair = git_safety.behind_main_pair(pre_mod.repo_root())
  if pair is None:
    return
  store, messages, _, _ = open_services(state_dir)
  try:
    key = f"ancestry_note:{pair[0]}:{pair[1]}"
    with store.transaction():
      if store.get_meta(key) == "1":
        return
      messages.send_message(
        "Branch cannot merge into main: origin/main is not an ancestor of HEAD. "
        "Rebase main and retry.",
        hidden=True,
        blocking=False,
      )
      store.set_meta(key, "1")
  finally:
    store.close()


def _run_post_exit(parsed: argparse.Namespace, state_dir: Path, out: TextIO) -> int:
  """Watch checks and print advice without changing the completed shell status."""
  if parsed.check_ancestry:
    _note_background_ancestry(state_dir)
    return 0
  _count_outer_bash(
    state_dir,
    str(parsed.line or ""),
    routed=os.environ.get("ARENA_STEER_ROUTE") == "1",
  )
  if (
    parsed.rc == 0
    and pre_mod.post_push_watch(parsed.line)
    and "pr-checks" not in FileGateStatus(state_dir).running_names()
  ):
    try:
      _start_detached_run(
        state_dir,
        "pr-checks",
        ["gh", "pr", "checks", "--watch"],
        on_finish=list(PR_CHECK_ANCESTRY_FOLLOWUP),
        out=out,
      )
      print(
        "routed pr-checks: background watch after push. Verdict on later call.",
        file=out,
      )
    except (OSError, ValueError) as exc:
      print(f"PR checks watch could not start: {exc}", file=out)
  slow = _slow_call_hint(parsed.line, parsed.elapsed)
  if slow:
    print(slow, file=out)
  for reminder in _exit_state_reminders(state_dir):
    print(reminder, file=out)
  return 0


def _run_run(
  args: list[str], state_dir: Path, out: TextIO, tasks: TaskService, store: SqliteStore
) -> int:
  if not args:
    raise ValueError("Usage: steer run <start|logs|pr-checks> ...")
  subcmd = args[0]
  if subcmd == "start":
    raw_args = args[1:]
    if "--" in raw_args:
      split_at = raw_args.index("--")
      option_args = raw_args[:split_at]
      command = raw_args[split_at + 1 :]
    else:
      option_args = raw_args
      command = []
    start_p = argparse.ArgumentParser(prog="steer run start")
    start_p.add_argument("name")
    start_p.add_argument("-a", "--after", default=None)
    start_p.add_argument("-t", "--task", default=None)
    parsed = start_p.parse_args(option_args)
    if not command:
      raise ValueError("Usage: steer run start <name> [--after <run>] -- <command...>")
    name = _safe_local_name(parsed.name)
    after = _safe_local_name(parsed.after) if parsed.after else None
    if parsed.task:
      with store.transaction():
        tasks.get_task(parsed.task)
        tasks.mod_links_add(parsed.task, name, link_type="gate")
        return _start_detached_run(state_dir, name, command, after=after, out=out)
    return _start_detached_run(state_dir, name, command, after=after, out=out)

  if subcmd == "logs":
    logs_p = argparse.ArgumentParser(prog="steer run logs")
    logs_p.add_argument("name")
    logs_p.add_argument("-n", "--lines", type=int, default=50)
    parsed = logs_p.parse_args(args[1:])
    name = _safe_local_name(parsed.name)
    runs = _runs_dir(state_dir)
    if not (runs / f"{name}.json").is_file():
      raise KeyError(f"Unknown run: {name}")
    record = json.loads((runs / f"{name}.json").read_text(encoding="utf-8"))
    exit_path = Path(record["exit_path"])
    if exit_path.is_file():
      status = f"exit {exit_path.read_text(encoding='utf-8').strip()}"
    else:
      status = "running"
    print(f"run {name} {status}", file=out)
    log_path = Path(record["log_path"])
    if log_path.is_file():
      lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
      for line in lines[-parsed.lines :]:
        print(line, file=out)
    return 0
  if subcmd == "pr-checks":
    checks_p = argparse.ArgumentParser(prog="steer run pr-checks")
    checks_p.add_argument("-n", "--number", type=int, default=None)
    parsed = checks_p.parse_args(args[1:])
    command = ["gh", "pr", "checks"]
    if parsed.number is not None:
      command.append(str(parsed.number))
    command.append("--watch")
    return _start_detached_run(
      state_dir,
      "pr-checks",
      command,
      on_finish=list(PR_CHECK_ANCESTRY_FOLLOWUP),
      out=out,
    )
  raise ValueError(f"Unknown run subcommand: {subcmd!r}")


def _run_report(
  args: list[str], reports: ReportService, store: SqliteStore, out: TextIO
) -> int:
  if not args:
    raise ValueError("Usage: steer report <id|pub|pin|unpin|rm> ...")
  subcmd = args[0]
  if subcmd == "pub":
    pub_p = argparse.ArgumentParser(prog="steer report pub")
    pub_p.add_argument("source")
    pub_p.add_argument("-i", "--id", required=True)
    pub_p.add_argument("-t", "--title", required=True)
    pub_p.add_argument("-u", "--allow-unparsed-markers", action="store_true")
    parsed = pub_p.parse_args(args[1:])
    source_path = Path(parsed.source)
    if not source_path.is_file():
      raise ValueError(f"Report source file does not exist: {source_path}")
    body = source_path.read_text(encoding="utf-8")
    report = reports.publish_report(
      parsed.id,
      parsed.title,
      body,
      allow_unparsed_markers=parsed.allow_unparsed_markers,
      source_file=str(source_path.resolve()),
    )
    SteeringApp(store).verify_published_report(report)
    print(
      f"Published {report.id}, {len(report.fields)} fields. Verified.",
      file=out,
    )
    return 0

  if subcmd in {"pin", "unpin"}:
    if len(args) != 2:
      raise ValueError(f"Usage: steer report {subcmd} <id>")
    reports.pin_report(args[1], subcmd == "pin")
    print(f"{'Pinned' if subcmd == 'pin' else 'Unpinned'} {args[1]}.", file=out)
    return 0

  if subcmd == "rm":
    targets = args[1:]
    if not targets or ("--stale" in targets and len(targets) != 1):
      raise ValueError("Usage: steer report rm <id> [<id> ...] | --stale")
    removed: list[str] = []
    with store.transaction():
      for target in dict.fromkeys(targets):
        removed.extend(reports.remove_report(target))
    print(
      f"Dismissed reports: {', '.join(removed) if removed else 'none'}",
      file=out,
    )
    return 0

  if len(args) == 1:
    report = reports.get_report(subcmd)
    _print_single_report(report, out)
    return 0

  raise ValueError(f"Unrecognized report arguments: {args}")


def _print_restore_notice(store: SqliteStore, out: TextIO) -> None:
  """Print and clear the one-line notice left by a state merge."""
  raw = store.get_meta("state_restore_notice", "")
  if not raw:
    return
  store.set_meta("state_restore_notice", "")
  try:
    notice = json.loads(raw)
  except json.JSONDecodeError:
    return
  source = str(notice.get("source") or "a container")
  added = int(notice.get("added") or 0)
  updated = int(notice.get("updated") or 0)
  print(f"State restored from {source}: {added} added, {updated} updated.", file=out)


def _print_delivered(
  delivered: dict[str, list], out: TextIO, timezone_name: str = ""
) -> None:
  msgs: list[Message] = delivered["messages"]
  ans_pairs: list[tuple[Report, ReportAnswer]] = delivered["report_answers"]
  total = len(msgs) + len(ans_pairs)
  try:
    zone = ZoneInfo(timezone_name) if timezone_name else UTC
  except (ZoneInfoNotFoundError, ValueError):
    zone = UTC
  stamp = datetime.now(UTC).astimezone(zone).strftime("%H:%M:%S")
  if total == 0:
    print(f"Inbox {stamp}: 0 pending", file=out)
    return
  print(f"Inbox {stamp}: {total} pending", file=out)
  for msg in msgs:
    _print_message(msg, out)
  for report, ans in ans_pairs:
    print(f"`{ans.id}` answer report {report.id} ({report.title})", file=out)
    for key, val in ans.values.items():
      label = ans.field_labels.get(key, key)
      formatted = (
        ", ".join(str(item) for item in val) if isinstance(val, list) else str(val)
      )
      print(f"  {label}: {formatted}", file=out)


def _print_message(msg: Message, out: TextIO) -> None:
  print(f"`{msg.id}`", file=out)
  if msg.parent_refs:
    parents = (
      ", ".join(msg.parent_ids)
      if all(ref["type"] == "message" for ref in msg.parent_refs)
      else ", ".join(f"{ref['type']} {ref['id']}" for ref in msg.parent_refs)
    )
    print("parents: " + parents, file=out)
  if msg.body:
    print(msg.body, file=out)
  for att in msg.attachments:
    print(f"attachment: {att.name} -> {att.path} ({att.size_bytes} B)", file=out)
  for ack_text in msg.acks:
    print(f"ack: {ack_text}", file=out)


def _task_type_label(task: Task) -> str:
  if len(task.types) == 1:
    return task.type
  phases = " → ".join(
    f"{phase_type}{' (done)' if index < task.phase_index else ' (current)' if index == task.phase_index else ''}"
    for index, phase_type in enumerate(task.types)
  )
  return f"{task.type}; {phases}"


def _print_task_list(items: list[tuple[Task, bool]], out: TextIO) -> None:
  for task, _blocked in items:
    print(f"{task.id} - {task.title}", file=out)


def _print_single_task(
  task: Task, blocked: bool, out: TextIO, *, include_details: bool = True
) -> None:
  state_str = f"{task.status}, blocked" if blocked else task.status
  print(f"task {task.id}, status {state_str}, type {_task_type_label(task)}", file=out)
  print(task.title, file=out)
  if task.scope:
    print(f"- Scope: {task.scope}", file=out)
  if task.deps:
    print(f"- Deps: {', '.join(task.deps)}", file=out)
  if task.report_ids:
    print(f"- Reports: {', '.join(task.report_ids)}", file=out)
  if task.msg_ids:
    print(f"- Messages: {', '.join(task.msg_ids)}", file=out)
  if task.gate_names:
    print(f"- Gates: {', '.join(task.gate_names)}", file=out)
  if include_details:
    print(f"- Priority: {task.effective_priority}", file=out)
    print(f"- Size: {task.size}", file=out)
    for idx, line in enumerate(task.details, 1):
      print(f"- [{idx}] {line}", file=out)


def _print_reports_list(items: list[Report], out: TextIO) -> None:
  if not items:
    print("Reports: 0", file=out)
    return
  print(f"Reports: {len(items)}", file=out)
  for rep in items:
    print(
      f"- {rep.id} [{rep.status}] ({len(rep.fields)} fields, {len(rep.answers)} answers): {rep.title}",
      file=out,
    )


def _print_single_report(rep: Report, out: TextIO) -> None:
  print(
    f"report {rep.id} [{rep.status}] - {rep.title} (modified {rep.modified_at})",
    file=out,
  )
  for field in rep.fields:
    print(f"  field {field.id} ({field.type}): {field.label}", file=out)
  for ans in rep.answers:
    print(f"  answer {ans.id} [{ans.status}]: {ans.values}", file=out)


def _survey_server_url(state_dir: Path) -> str | None:
  """Return the running server base URL recorded in the state directory."""
  server_file = state_dir / "server.json"
  if not server_file.is_file():
    return None
  try:
    data = json.loads(server_file.read_text(encoding="utf-8"))
    host = str(data.get("host") or "127.0.0.1")
    port = int(data.get("port") or 0)
  except (OSError, ValueError, TypeError):
    return None
  if not port:
    return None
  return f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"


def _post_survey(url: str, payload: str) -> dict | None:
  """Submit survey answers to the running server so its memory holds them."""
  # The HTTP layer takes objects only, so array submissions travel under "answers".
  try:
    decoded = json.loads(payload)
  except json.JSONDecodeError:
    decoded = payload
  body = json.dumps({"answers": decoded}).encode("utf-8")
  request = urllib.request.Request(
    f"{url}/survey",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
  )
  opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
  try:
    with opener.open(request, timeout=5) as response:
      body = json.loads(response.read().decode("utf-8"))
  except (urllib.error.URLError, OSError, ValueError):
    return None
  survey = body.get("survey") if isinstance(body, dict) else None
  return survey if isinstance(survey, dict) else None


def _submit_survey(
  payload: str,
  messages: MessageService,
  state_dir: Path,
  out: TextIO,
) -> dict:
  """Send answers to the live server, or grade them locally when none runs."""
  url = _survey_server_url(state_dir)
  survey = _post_survey(url, payload) if url else None
  if survey is not None:
    print("survey-source=server", file=out)
    return survey
  if url:
    print("survey-source=local", file=out)
  return messages.submit_survey(payload)


def _run_survey(
  parsed: argparse.Namespace,
  messages: MessageService,
  out: TextIO,
  state_dir: Path | None = None,
) -> int:
  import json

  from . import survey as survey_mod

  if getattr(parsed, "template", False):
    print(json.dumps(survey_mod.template(), indent=2), file=out)
    return 0
  state_dir = state_dir or resolve_state_dir()
  if getattr(parsed, "file", None):
    payload = Path(parsed.file).read_text(encoding="utf-8")
    survey = _submit_survey(payload, messages, state_dir, out)
    print(
      f"Submitted {len(survey['results'])} survey answer(s) at {survey['checked_at']}",
      file=out,
    )
    return 0
  entries: list[str] = list(parsed.entries or [])
  if entries:
    if len(entries) != 1:
      raise ValueError("Survey submission must be a JSON array")
    survey = _submit_survey(entries[0], messages, state_dir, out)
    print(
      f"Submitted {len(survey['results'])} survey answer(s) at {survey['checked_at']}",
      file=out,
    )
    return 0
  survey = messages.get_survey()
  print(
    f"Survey: pending={survey['pending']} checked_at={survey['checked_at'] or 'never'}",
    file=out,
  )
  for q in survey["questions"]:
    qid = q["id"]
    ans = survey["answers"].get(qid, "(unanswered)")
    prompt = q["prompt"]
    if qid == "V1S0N":
      prompt = prompt.replace("{path}", survey_mod.smallest_image())
    print(f"- {qid}: {prompt} -> {ans}", file=out)
  print("\nSubmission template:", file=out)
  print(json.dumps(survey_mod.template(), indent=2), file=out)
  return 0
