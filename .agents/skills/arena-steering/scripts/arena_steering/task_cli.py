"""Task commands and terminal rendering for Arena Steering."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

from .display_paths import home_relative
from .models import TASK_PRIORITIES, TASK_SIZES, Task
from .services import TaskService


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
      _print_single_task(task, blocked, out, include_details=bool(args), tasks=tasks)
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
    new_p.add_argument("-S", "--scope", required=True)
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
    _print_single_task(task, blocked, out, tasks=tasks)
    return 0

  if subcmd == "finish":
    finish_p = argparse.ArgumentParser(prog="steer task finish")
    finish_p.add_argument("id")
    finish_p.add_argument("-c", "--commit", default=None)
    parsed = finish_p.parse_args(args[1:])
    task = tasks.finish_phase(parsed.id, commit_hash=parsed.commit)
    _, blocked = tasks.get_task(task.id)
    _print_single_task(task, blocked, out, tasks=tasks)
    next_task, skipped = tasks.finished_task_followup(task)
    if next_task is not None:
      print(f"Next task: {next_task.id} | {next_task.title}", file=out)
    if skipped is not None:
      print(
        f"Warning: {skipped.id} ({skipped.priority}) outranks the finished task.",
        file=out,
      )
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
    _print_single_task(updated, blocked, out, tasks=tasks)
    return 0

  if len(args) == 1:
    task, blocked = tasks.get_task(subcmd)
    _print_single_task(task, blocked, out, tasks=tasks)
    return 0

  raise ValueError(f"Unrecognized task arguments: {args}")


def _parse_task_size_value(raw: str) -> int:
  try:
    size = int(raw)
  except ValueError as exc:
    raise ValueError(f"Task size must be one of {TASK_SIZES}, got {raw!r}") from exc
  if size not in TASK_SIZES:
    raise ValueError(f"Task size must be one of {TASK_SIZES}, got {size}")
  return size


def _extract_size_flag(tokens: list[str]) -> tuple[int | None, list[str]]:
  size: int | None = None
  remaining: list[str] = []
  idx = 0
  while idx < len(tokens):
    token = tokens[idx]
    if token == "--":
      remaining.extend(tokens[idx + 1 :])
      break
    if token in {"-z", "--size"}:
      if idx + 1 >= len(tokens):
        raise ValueError("Option -z/--size requires a value")
      size = _parse_task_size_value(tokens[idx + 1])
      idx += 2
      continue
    if token.startswith("--size="):
      size = _parse_task_size_value(token.split("=", 1)[1])
      idx += 1
      continue
    if token.startswith("-z") and len(token) > 2:
      size = _parse_task_size_value(token[2:])
      idx += 1
      continue
    remaining.append(token)
    idx += 1
  return size, remaining


def _run_task_mod(args: list[str], tasks: TaskService, out: TextIO) -> int:
  if len(args) < 3:
    raise ValueError("Usage: steer task mod <id> <field> [rm] <value>")
  task_id, field = args[0], args[1].lower()
  rest = args[2:]

  if field == "deets":
    size, detail_args = _extract_size_flag(rest)
    if not detail_args:
      raise ValueError("Usage: steer task mod <id> deets <text> -z <size>")
    if detail_args[0] == "rm":
      if len(detail_args) != 2:
        raise ValueError("Usage: steer task mod <id> deets rm <number> -z <size>")
      updated = tasks.mod_deets_rm(task_id, int(detail_args[1]), size=size)
    else:
      updated = tasks.mod_deets(task_id, " ".join(detail_args), size=size)
  elif field == "attach":
    size, attach_args = _extract_size_flag(rest)
    if not attach_args:
      raise ValueError(
        "Usage: steer task mod <id> attach add <path> -z <size> | attach rm <number> -z <size>"
      )
    if attach_args[0] == "rm":
      if len(attach_args) != 2:
        raise ValueError("Usage: steer task mod <id> attach rm <number> -z <size>")
      updated = tasks.mod_attachments_rm(task_id, size=size, number=int(attach_args[1]))
    elif attach_args[0] == "add":
      if len(attach_args) != 2:
        raise ValueError("Usage: steer task mod <id> attach add <path> -z <size>")
      updated = tasks.mod_attachments_add(task_id, attach_args[1], size=size)
    else:
      raise ValueError(
        "Usage: steer task mod <id> attach add <path> -z <size> | attach rm <number> -z <size>"
      )
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
      link_parser.add_argument("-z", "--size", type=int, choices=TASK_SIZES)
      parsed = link_parser.parse_intermixed_args(rest)
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
          "Usage: steer task mod <id> links [msg|report|task|gate] <link-id> "
          "[-A detail] [--delete number] [-z size]"
        )
      before = set(tasks.get_task(task_id)[0].details)
      updated = tasks.mod_links_add(
        task_id,
        link_id,
        link_type=link_type,
        append_detail=parsed.append_detail,
        remove_detail=parsed.remove_detail,
        size=parsed.size,
      )
      if link_type and link_type.lower() == "report":
        added = [detail for detail in updated.details if detail not in before]
        if added:
          print(f"Added to task {updated.id}:", file=out)
          for detail in added:
            print(f"- {detail}", file=out)
          print(
            f"Remove any of these from task {updated.id} if they add nothing: "
            f"steer task mod {updated.id} deets rm <index> -z <size>",
            file=out,
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
  _print_single_task(updated, blocked, out, tasks=tasks)
  return 0


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
  task: Task,
  blocked: bool,
  out: TextIO,
  *,
  include_details: bool = True,
  tasks: TaskService | None = None,
) -> None:
  from .cli import resolve_state_dir
  from .services import format_task_detail_status

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
    rendered_details = (
      tasks.format_task_details(task)
      if tasks is not None
      else [
        format_task_detail_status(line, uploads_dir=resolve_state_dir() / "uploads")
        for line in task.details
      ]
    )
    for idx, line in enumerate(rendered_details, 1):
      print(f"- [{idx}] {line}", file=out)
    for idx, item in enumerate(task.attachments, 1):
      print(f"- ({idx}) {item.name} - {home_relative(Path(item.path))}", file=out)
