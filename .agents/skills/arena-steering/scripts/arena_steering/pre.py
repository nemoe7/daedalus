"""Pre-command pipeline for the shell hook.

Each check is one small class with `applies` and `run`. A step returns `None` to
continue, a message to warn, or a verdict to stop the line. `PIPELINE` is the
registry, so adding, dropping, or reordering a check is one line here.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from . import git_safety
from .display_paths import home_relative

CONTINUE = 0
END_CALL = 2
BLOCK = 130

GATE_THRESHOLD = 6
CALL_COUNT_META = "pre_call_count"
WIREFRAME_SUFFIX = "-wireframe.png"
SHELL_NAMES = {"bash", "sh", "dash", "zsh", "ksh"}
COMMAND_WRAPPERS = {"env", "command", "nice", "nohup", "stdbuf", "sudo", "time"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
SHELL_WRAPPER = re.compile(
  r"^(?:\S*/)?(?:bash|sh|dash|zsh|ksh)\s+(?:-[\w-]+\s+)*-[\w]*c\s+"
)


def command_words(words: list[str], *, unwrap_timeout: bool = False) -> list[str]:
  """Skip assignments and ordinary shell wrappers before naming the command."""
  index = 0
  while index < len(words):
    head = Path(words[index]).name
    if ASSIGNMENT.match(words[index]):
      index += 1
    elif head in COMMAND_WRAPPERS:
      index += 1
      while index < len(words) and (
        words[index].startswith("-")
        or ASSIGNMENT.match(words[index])
        or re.fullmatch(r"\d+(?:\.\d+)?", words[index])
      ):
        if head == "command" and words[index] in {"-v", "-V"}:
          return []  # A lookup must not become the command it looks up.
        index += 1
    elif head == "timeout" and unwrap_timeout:
      index += 1
      while index < len(words):
        option = words[index]
        if option in {"-s", "--signal", "-k", "--kill-after"}:
          index += 2
        elif option.startswith("-") or re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", option):
          index += 1
        else:
          break
    else:
      break
  return words[index:]


def shell_line(text: str) -> str:
  """Drop the Bash launcher without losing the command it runs."""
  text = SHELL_WRAPPER.sub("", text.strip(), count=1)
  if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
    return text[1:-1]
  return text


ARENA_PROBE_COMMANDS = {"source ~/.profile"}
PROFILE_MKDIR_LAUNCHER = re.compile(r"/bin/bash -l -c mkdir -p '[^'\r\n]+'")
NATIVE_MKDIR_LAUNCHER = re.compile(r"/bin/bash -l -c (mkdir -p '(/[^'\r\n]+)')")
LOGIN_ACK_WRAPPER = re.compile(
  r"""PATH="\$HOME/\.local/bin:\$PATH" steer ack [0-9a-f]{6} '[^'\r\n]+'"""
)
ARENA_PORT_PROBE = (
  "(command -v ss >/dev/null 2>&1 && ss -ltnH) || netstat -ltn 2>/dev/null || true"
)
ARENA_PROCESS_PROBE_SHA256 = (
  "98cec4863048f02988a62faac41ce8c46bb987e3ec4e843769d123b67d48b665"
)
ARENA_WORKSPACE_VERBS = {"read", "edit", "write"}
STOP_PROCESS_NAME = re.compile(r"/tmp/arena-workspace/procs/([^/'\r\n`$\x00]+)")
STOP_PROCESS_SCRIPT_SHA256 = (
  "34229acc3bb28de2035839b6a592ac81c7d4d01bc25f7b74bc361facbb885f39"
)
OUTPUT_PROCESS_SCRIPT_SHA256 = (
  "a54cfd816bdfb02a258842410dd9ea3c3cf7151119ef50c7c90e190f6487cb2d"
)
ISOLATED_READER_SHA256 = (
  "387e3514ed96d946f84178cc5b262b7740321b2638d1de359fd7fd2dac6df6b3"
)
PLATFORM_SNAPSHOT_SCRIPT_SHA256 = (
  "22e1bd74b86c719275edefddba4cb16044f13fe6b84377b1eda100e25dadf663"
)
REMOTE_HEALTH_SCRIPT_SHA256 = (
  "9cbda07ac13c5b39f857f1ef65575b127f7a19b0942413a195598a06a42087e9"
)
REMOTE_HEALTH_REF = re.compile(r"refs/remotes/origin/([A-Za-z0-9][A-Za-z0-9._/-]*)")


def _isolated_reader(line: str, command: str) -> bool:
  """Match the complete isolated read-only script, varying only path and range."""
  launcher = "/bin/bash -l -c "
  if not line.startswith(launcher + "python3 -I - <<'PY'"):
    return False
  text = line.replace("\r\n", "\n").strip()
  if text.removeprefix(launcher) != command.replace("\r\n", "\n").strip():
    return False
  path_pattern = r'^path = ("(?:\\.|[^"\\])*")$'
  path_match = re.search(path_pattern, text, flags=re.MULTILINE)
  if path_match is None:
    return False
  encoded = path_match.group(1)
  try:
    path = json.loads(encoded)
  except json.JSONDecodeError:
    return False
  if (
    not isinstance(path, str)
    or not Path(path).is_absolute()
    or any(marker in path for marker in ("$(", "`", "\x00"))
    or json.dumps(path) != encoded
  ):
    return False
  text, count = re.subn(path_pattern, "path = <path>", text, flags=re.MULTILINE)
  if count != 1:
    return False
  for name in ("offset", "limit"):
    text, count = re.subn(
      rf"^{name} = (None|[1-9][0-9]*)$",
      f"{name} = <{name}>",
      text,
      flags=re.MULTILINE,
    )
    if count != 1:
      return False
  return hashlib.sha256(text.encode()).hexdigest() == ISOLATED_READER_SHA256


def _checkout_directory_validator(
  line: str, command: str, root: Path | None, *, allow_inner: bool = False
) -> bool:
  """Match each checkout prefix check and reject paths escaping through symlinks."""
  launcher = "/bin/bash -l -c "
  if not line.startswith(launcher) or not line.removeprefix(launcher).startswith(
    ("[ -e '", "[ -d '")
  ):
    return False
  checks = line.replace("\r\n", "\n").strip().removeprefix(launcher).split("\n")
  if len(checks) < 2:
    return False
  final = re.fullmatch(
    r"\[ -d '([^'\r\n]+)' \] && \{ printf 'DIR\\n'; exit 0; \}", checks[-2]
  )
  if final is None:
    return False
  target = Path(final.group(1))
  if not target.is_absolute() or any(mark in str(target) for mark in ("'", "\r", "\n")):
    return False
  try:
    checkout = (root or repo_root()).resolve()
    relative = target.relative_to(checkout)
    if not relative.parts or ".." in relative.parts:
      return False
    if not target.resolve().is_relative_to(checkout):
      return False
  except (OSError, RuntimeError, ValueError):
    return False
  prefix_count = len(relative.parts) - 1
  if len(checks) != prefix_count + 2:
    return False
  command_text = command.strip()
  first_command = checks[0].split(" && ", 1)[0]
  native_match = allow_inner and command_text in {
    "exit 0",
    "printf 'DIR\\n'",
    "printf 'OK\\n'",
    f"[ -d '{target}' ]",
  }
  prefix_parts: list[str] = []
  prefix_path = checkout
  for index in range(prefix_count):
    part = relative.parts[index]
    prefix_parts.append(part)
    prefix_path = prefix_path / part
    prefix = "/".join(prefix_parts)
    try:
      if not prefix_path.resolve().is_relative_to(checkout):
        return False
    except (OSError, RuntimeError):
      return False
    expected_check = (
      f"[ -e '{prefix_path}' ] && [ ! -d '{prefix_path}' ] "
      f"&& {{ printf 'FILE:%s\\n' '{prefix}'; exit 0; }}"
    )
    if checks[index] != expected_check:
      return False
    if allow_inner and (
      command_text == f"[ -e '{prefix_path}' ]"
      or command_text == f"[ ! -d '{prefix_path}' ]"
      or command_text == f"printf 'FILE:%s\\n' '{prefix}'"
    ):
      native_match = True
  final_check = f"[ -d '{target}' ] && {{ printf 'DIR\\n'; exit 0; }}"
  if checks[-2] != final_check or checks[-1] != "printf 'OK\\n'":
    return False
  return command_text == first_command or (allow_inner and native_match)


def _platform_stop_process(line: str, command: str) -> bool:
  """Match the complete stop script for one platform process name."""
  current = _normalize_native_redirect(command)
  if not current or (
    current != "source ~/.profile" and current not in shell_line(line)
  ):
    return False
  names = set(STOP_PROCESS_NAME.findall(line))
  if len(names) != 1 or names & {".", ".."}:
    return False
  normalized = STOP_PROCESS_NAME.sub(
    "/tmp/arena-workspace/procs/clankers-steering-{id}",
    line.replace("\r\n", "\n").strip(),
  )
  return hashlib.sha256(normalized.encode()).hexdigest() == STOP_PROCESS_SCRIPT_SHA256


def _platform_output_process(line: str, command: str) -> bool:
  """Match the complete output script for one platform process name and tail length."""
  current = _normalize_native_redirect(command)
  if not current or (
    current != "source ~/.profile" and current not in shell_line(line)
  ):
    return False
  names = set(STOP_PROCESS_NAME.findall(line))
  if len(names) != 1 or names & {".", ".."}:
    return False
  normalized = STOP_PROCESS_NAME.sub(
    "/tmp/arena-workspace/procs/{id}", line.replace("\r\n", "\n").strip()
  )
  normalized, count = re.subn(r"tail -n [0-9]+", "tail -n {lines}", normalized)
  return (
    count == 1
    and hashlib.sha256(normalized.encode()).hexdigest() == OUTPUT_PROCESS_SCRIPT_SHA256
  )


ARENA_PROCS_DIR = Path("/tmp/arena-workspace/procs")


def expected_steering_process_name(
  cwd: str | Path | None = None, *, checkout_root: Path | None = None
) -> str:
  """Return `{repo} - Steering` for the checkout directory."""
  root = (checkout_root or repo_root()).resolve()
  repo_name = root.name
  if cwd:
    try:
      target = Path(cwd).resolve()
      if target != root and not target.is_relative_to(root) and target.name:
        repo_name = target.name
    except (OSError, RuntimeError, ValueError):
      pass
  return f"{repo_name} - Steering"


def _is_steer_serve_command(command: str) -> bool:
  """Return True when a shell command starts `steer serve`."""
  pieces = [words for words, _ in PreContext(line=shell_line(command)).pieces()]
  pieces.extend(
    inner for words in pieces[:] for inner, _ in embedded_pieces(command_words(words))
  )
  for raw_words in pieces:
    words = command_words(raw_words)
    if not words:
      continue
    head = Path(words[0]).name
    rest: list[str] | None = None
    if head == "steer" and len(words) >= 2 and words[1] == "serve":
      rest = words[2:]
    elif (
      head.startswith("python")
      and len(words) >= 3
      and Path(words[1]).name == "steer"
      and words[2] == "serve"
    ):
      rest = words[3:]
    if rest is not None and not any(arg in {"-h", "--help"} for arg in rest):
      return True
  return False


def _steering_launch_error(
  meta: dict[str, object], *, checkout_root: Path | None = None
) -> str | None:
  command = meta.get("command")
  if not isinstance(command, str) or not _is_steer_serve_command(command):
    return None
  cwd = meta.get("cwd")
  expected = expected_steering_process_name(
    cwd if isinstance(cwd, str) else None, checkout_root=checkout_root
  )
  if meta.get("name") != expected:
    return (
      f"VIOLATION: Name the Steering start_process {expected!r}, "
      f"got {meta.get('name')!r}."
    )
  return None


def _parse_platform_launch(line: str, command: str) -> dict[str, object] | None:
  """Match the full launch wrapper and return its parsed metadata when valid."""
  current = _normalize_native_redirect(command).replace("< /dev/null", "</dev/null")
  if not current or (
    current != "source ~/.profile" and current not in shell_line(line)
  ):
    return None
  text = line.replace("\r\n", "\n").strip()
  try:
    head, setsid, echo, cat = text.rsplit("\n", 3)
    first_line, metadata_line, command_line = head.split("\n", 2)
  except ValueError:
    return None
  first = re.fullmatch(
    r"/bin/bash -l -c mkdir -p '(/tmp/arena-workspace/procs/([^/'\r\n`$\x00]+))'",
    first_line,
  )
  if first is None or first.group(2) in {".", ".."}:
    return None
  directory, process_id = first.groups()
  meta_match = re.fullmatch(
    r"printf '%s' (.+) > '" + re.escape(directory) + r"'/meta.json", metadata_line
  )
  run_match = re.fullmatch(
    r"printf '%s' (.+) > '" + re.escape(directory) + r"'/run.cmd",
    command_line,
    flags=re.DOTALL,
  )
  if meta_match is None or run_match is None:
    return None
  try:
    meta_words = shlex.split(meta_match.group(1))
    run_words = shlex.split(run_match.group(1))
    if len(meta_words) != 1 or len(run_words) != 1:
      return None
    metadata, run_command = meta_words[0], run_words[0]

    def quoted(value: str) -> tuple[str, str]:
      return shlex.quote(value), "'" + value.replace("'", "'\\''") + "'"

    if meta_match.group(1) not in quoted(metadata) or run_match.group(1) not in quoted(
      run_command
    ):
      return None
    meta = json.loads(metadata)
  except (ValueError, TypeError):
    return None
  if (
    not isinstance(meta, dict)
    or set(meta) != {"processId", "name", "command", "cwd", "startedAt"}
    or meta["processId"] != process_id
    or meta["command"] != run_command
    or not all(isinstance(meta[key], str) for key in ("name", "command", "cwd"))
    or type(meta["startedAt"]) not in (int, float)
  ):
    return None
  if [setsid, echo, cat] != [
    (
      'setsid bash -c \'bash "$0/run.cmd" > "$0/out.log" 2>&1; echo $? > "$0/exit"\' '
      f"'{directory}' </dev/null >/dev/null 2>&1 &"
    ),
    f"echo $! > '{directory}'/pid",
    f"cat '{directory}'/pid",
  ]:
    return None
  return meta


def _platform_launch_process(
  line: str, command: str, *, checkout_root: Path | None = None
) -> bool:
  """Match the full launch wrapper and verify its literal metadata and command."""
  meta = _parse_platform_launch(line, command)
  return (
    meta is not None
    and _steering_launch_error(meta, checkout_root=checkout_root) is None
  )


def _pinned_platform_shell(
  line: str, command: str, *, allow_inner: bool = False
) -> bool:
  """Match exact platform shells, varying only their pinned base or branch."""
  launcher = "/bin/bash -l -c "
  text = line.replace("\r\n", "\n").strip()
  current = command.replace("\r\n", "\n").strip()
  if not current or not text.startswith(launcher):
    return False
  script = text.removeprefix(launcher)
  if script.startswith("set -euo pipefail\nBASE='"):
    normalized_script, count = re.subn(
      r"^BASE='[0-9a-f]{40}'$",
      "BASE='<base>'",
      script,
      flags=re.MULTILINE,
    )
    if count != 1:
      return False
    expected = PLATFORM_SNAPSHOT_SCRIPT_SHA256
  elif script.startswith(
    "set -o pipefail\ngit rev-parse --verify -q 'refs/remotes/origin/"
  ):
    refs = list(REMOTE_HEALTH_REF.finditer(script))
    if len(refs) != 2 or len({match.group(1) for match in refs}) != 1:
      return False
    normalized_script = REMOTE_HEALTH_REF.sub("refs/remotes/origin/<branch>", script)
    expected = REMOTE_HEALTH_SCRIPT_SHA256
  else:
    return False
  normalized = launcher + normalized_script
  if hashlib.sha256(normalized.encode()).hexdigest() != expected:
    return False
  return current in script if allow_inner else current == script


def _normalize_native_redirect(command: str) -> str:
  """Treat native stdout/stderr redirect spacing as equivalent for matching only."""
  return re.sub(r"(?<!\S)(2?)> /dev/null", r"\1>/dev/null", command.strip(), count=1)


def arena_maintenance_call(
  line: str, command: str, *, checkout_root: Path | None = None
) -> bool:
  """Recognize whole Arena utility calls, not user lines that merely mention them."""
  if not command.strip():
    return False
  text = shell_line(line).strip()
  if _isolated_reader(line, command):
    return True
  if _checkout_directory_validator(line, command, checkout_root):
    return True
  if (
    _platform_stop_process(line, command)
    or _platform_output_process(line, command)
    or _platform_launch_process(line, command, checkout_root=checkout_root)
  ):
    return True
  if _pinned_platform_shell(line, command):
    return True
  if command.strip() == "source ~/.profile" and PROFILE_MKDIR_LAUNCHER.fullmatch(
    line.strip()
  ):
    return True
  mkdir = NATIVE_MKDIR_LAUNCHER.fullmatch(line.strip())
  if (
    mkdir
    and command.strip() == mkdir.group(1)
    and ".." not in Path(mkdir.group(2)).parts
    and not any(mark in mkdir.group(2) for mark in ("$(", "`", "\x00"))
  ):
    return True
  if text == ARENA_PORT_PROBE and command.strip() in text:
    return True
  if (
    "===ARENA_ENTRY===" in text
    and "===ARENA_PORTS===" in text
    and _normalize_native_redirect(command) in text
    and hashlib.sha256(text.encode()).hexdigest() == ARENA_PROCESS_PROBE_SHA256
  ):
    return True
  if not text or text != shell_line(command).strip():
    return False
  if text in ARENA_PROBE_COMMANDS:
    return True
  if any(operator in text for operator in (";", "|", "&", "\n", "`", "$(")):
    return False
  try:
    words = shlex.split(text)
  except ValueError:
    return False
  direct = command_words(words)
  return (bool(direct) and Path(direct[0]).name in {"ss", "netstat"}) or (
    len(words) >= 2
    and Path(words[0]).name == "arena-workspace"
    and words[1] in ARENA_WORKSPACE_VERBS
  )


def arena_maintenance_step(
  line: str, command: str, *, checkout_root: Path | None = None
) -> bool:
  """Exempt the current native step without waiving later shell commands."""
  if not command.strip():
    return False
  if arena_maintenance_call(line, command, checkout_root=checkout_root):
    return True
  text = shell_line(line)
  if text.startswith("mkdir -p '/tmp/arena-workspace/procs/") and "/meta.json" in text:
    return False
  launcher = "/bin/bash -l -c " if line.startswith("/bin/bash -l -c ") else ""
  boundaries = [0, *(index + 1 for index, char in enumerate(text) if char in ";\n")]
  ends = [*(index for index, char in enumerate(text) if char in ";\n"), len(text)]
  for start in boundaries:
    for end in ends:
      if end <= start:
        continue
      segment = text[start:end].strip()
      current = _normalize_native_redirect(command)
      if current == "source ~/.profile":
        if segment.startswith("[ -e '"):
          current = segment.split(" && ", 1)[0]
        elif segment.startswith("echo '===ARENA_STATUS==='"):
          current = segment.split("\n", 1)[0]
        else:
          current = segment
      if current not in segment:
        continue
      candidate = launcher + segment
      if (
        not _platform_output_process(candidate, current)
        and not _platform_launch_process(candidate, current)
        and arena_maintenance_call(candidate, current, checkout_root=checkout_root)
      ):
        return True
      if _pinned_platform_shell(candidate, current, allow_inner=True):
        return True
      if segment.startswith(("[ -e '", "[ -d '")) and _checkout_directory_validator(
        candidate, current, checkout_root, allow_inner=True
      ):
        return True
  return False


TASK_SUBCOMMANDS = frozenset({"task", "tasks", "ren"})


def command_activity(line: str, command: str) -> str:
  """Name a steer or git command for the Messages timeline, or return an empty string."""
  text = shell_line(command or line).strip()
  if not text:
    return ""
  try:
    words = shlex.split(text)
  except ValueError:
    return ""
  if not words:
    return ""
  head, args = Path(words[0]).name, words[1:]
  if head == "git":
    if "push" in args:
      return "push"
    return "commit" if "commit" in args else ""
  if head != "steer" or not args:
    return ""
  verb = args[0]
  if verb == "read":
    return "inbox"
  if verb == "run":
    return "run"
  if verb in TASK_SUBCOMMANDS:
    action = args[1] if verb == "task" and len(args) > 1 else ""
    return {"mod": "task-mod", "finish": "task-finish"}.get(action, "task")
  if verb == "report" and args[1:2] == ["pub"]:
    return "pub"
  return ""


def native_tool_name(
  line: str, command: str, shell: str, *, checkout_root: Path | None = None
) -> str:
  """Name the current agent tool, or return an empty string for platform-only steps."""
  if shell not in SHELL_NAMES or not command.strip():
    return ""
  current = shell_line(command).strip()
  if not current:
    return ""
  launcher = "/bin/bash -l -c "
  if _isolated_reader(launcher + current, current):
    return "read"
  if _checkout_directory_validator(line, command, checkout_root):
    return "edit"
  mkdir = NATIVE_MKDIR_LAUNCHER.fullmatch(line.strip())
  if (
    mkdir
    and command.strip() == mkdir.group(1)
    and arena_maintenance_call(line, command, checkout_root=checkout_root)
  ):
    try:
      root = (checkout_root or repo_root()).resolve()
      path = Path(mkdir.group(2))
      if ".." not in path.parts and path.resolve().is_relative_to(root):
        return "edit"
    except (OSError, RuntimeError, ValueError):
      pass
  if not any(operator in current for operator in (";", "|", "&", "\n", "`", "$(")):
    try:
      words = shlex.split(current)
    except ValueError:
      words = []
    if (
      len(words) >= 2
      and Path(words[0]).name == "arena-workspace"
      and words[1] in ARENA_WORKSPACE_VERBS
    ):
      return words[1]
  if arena_maintenance_step(line, command, checkout_root=checkout_root):
    return ""
  return "bash"


@dataclass
class PreContext:
  """Everything a step may read for one shell line."""

  line: str = ""
  command: str = ""
  shell: str = ""
  done: tuple[str, ...] = ()
  pending_ids: list[str] = field(default_factory=list)
  unread_ids: list[str] = field(default_factory=list)
  call_count: int = 0
  active_runs: tuple[str, ...] = ()
  active_run_ids: tuple[tuple[str, str], ...] = ()
  runs_dir: Path | None = None
  seen_live_run_log_ids: tuple[str, ...] = ()
  routed: bool = False
  cwd: Path | None = None
  checkout_root: Path | None = None
  session_ids: tuple[str, ...] = ()
  renamable_ids: tuple[str, ...] = ()

  def pieces(self) -> list[tuple[list[str], str]]:
    """Name simple commands and the separators that follow them."""
    lexer = shlex.shlex(
      shell_line(self.line or self.command),
      posix=True,
      punctuation_chars="|;&()\n",
    )
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
      tokens = list(lexer)
    except ValueError:
      return []
    result: list[tuple[list[str], str]] = []
    current: list[str] = []
    for token in [*tokens, ";"]:
      if token in {"|", "|&", ";", "&&", "||", "&", "(", ")", "\n"}:
        if current:
          result.append((current, token))
        current = []
      else:
        current.append(token)
    return result

  def touches(self, *words: str) -> bool:
    """Report whether the command contains any of these substrings."""
    text = f"{self.command} {self.line}"
    return any(word in text for word in words)

  def is_push(self) -> bool:
    return any(
      remote_git_push(words)
      for source in (self.line, self.command)
      if source
      for words in _line_commands(PreContext(line=source))
    )

  def is_arena_maintenance(self) -> bool:
    return arena_maintenance_step(
      self.line, self.command, checkout_root=self.checkout_root
    )

  def is_mixed_line(self) -> bool:
    return not arena_maintenance_call(
      self.line, self.command, checkout_root=self.checkout_root
    ) and (len(self.pieces()) > 1 or any(c in shell_line(self.line) for c in ";\n"))

  def is_standalone_mkdir(self) -> bool:
    current = shell_line(self.command).strip()
    if not current or (self.line and shell_line(self.line).strip() != current):
      return False
    if any(mark in current for mark in ("$(", "`", "<", ">")):
      return False
    pieces = PreContext(line=current).pieces()
    if len(pieces) != 1 or pieces[0][1] != ";":
      return False
    words = command_words(pieces[0][0])
    return bool(words) and Path(words[0]).name == "mkdir"

  def is_single_push(self) -> bool:
    """Allow a checked, single push to finish without a second EXIT-trap check."""
    if not self.line or not self.command:
      return False
    text = shell_line(self.line).strip()
    if text != shell_line(self.command).strip():
      return False
    if any(mark in text for mark in (";", "|", "&", "\n", "`", "$(", "<", ">")):
      return False
    pieces = self.pieces()
    return (
      len(pieces) == 1
      and pieces[0][1] == ";"
      and remote_git_push(command_words(pieces[0][0]))
    )

  def is_inbox_recovery(self) -> bool:
    """Allow inbox-only lines and a current initial ACK step."""
    if (
      self.line
      and self.command
      and self.line.strip() == f"/bin/bash -l -c {self.command.strip()}"
      and LOGIN_ACK_WRAPPER.fullmatch(self.command.strip())
    ):
      return True

    def verb(words: list[str]) -> str:
      if len(words) < 2 or Path(words[0]).name != "steer":
        return ""
      return words[1] if words[1] in {"read", "poll", "ack"} else ""

    def has_redirection(text: str) -> bool:
      lexer = shlex.shlex(text, posix=False, punctuation_chars="<>")
      lexer.whitespace_split = True
      lexer.commenters = ""
      try:
        words = list(lexer)
      except ValueError:
        return True
      return any(word and all(char in "<>" for char in word) for word in words)

    text = shell_line(self.line or self.command)
    pieces = self.pieces()
    if not pieces:
      return False
    first_verb = verb(pieces[0][0])
    current = PreContext(line=self.command).pieces() if self.command else []
    if (
      len(pieces) > 1
      and first_verb == "ack"
      and pieces[0][1] in {";", "&&", "||", "\n"}
      and len(current) == 1
      and current[0][1] == ";"
      and current[0][0] == pieces[0][0]
    ):
      return not has_redirection(shell_line(self.command))
    if "\n" not in text:
      lexer = shlex.shlex(text, posix=True, punctuation_chars="|;&<>()")
      lexer.whitespace_split = True
      lexer.commenters = ""
      try:
        words = list(lexer)
      except ValueError:
        return False
      if not verb(words):
        return False
      punctuation = [
        word for word in words[2:] if word and all(c in "|;&<>()" for c in word)
      ]
      if not punctuation:
        return True
      if words[1] != "ack" or punctuation != ["|"]:
        return False
      return not (self.is_push() or PreContext(line=self.command).is_push())
    if len(pieces) == 1 and pieces[0][1] == ";":
      return bool(first_verb)
    return bool(
      len(pieces) == 2
      and pieces[0][1] == "|"
      and pieces[1][1] == ";"
      and first_verb == "ack"
      and not (self.is_push() or PreContext(line=self.command).is_push())
    )

  def blocked_part(self) -> str:
    """Name the last simple command of a chained line."""
    text = self.line or self.command
    parts = [part.strip() for part in re.split(r"&&|\|\||;|\n", text)]
    parts = [part for part in parts if part]
    return parts[-1] if parts else text.strip()


# Count means eligible for the pending-inbox budget. Block means the six-call gate applies at its limit.
# Unconditional guards, including pending pushes, still run separately before this policy.
BASH_COMMAND_POLICY: dict[str, tuple[bool, bool]] = {
  "python3": (True, True),
  "python": (True, True),
  "standalone mkdir": (True, False),
  "inbox recovery": (False, False),
  "steering exempt": (False, False),
  "steering count exempt": (False, True),
  "arena utility": (False, False),
  "routed background": (False, False),
  "*": (True, True),
}


def bash_command_policy(ctx: PreContext) -> tuple[bool, bool]:
  """Classify one Bash line without weakening the unconditional guards."""
  if ctx.routed:
    return BASH_COMMAND_POLICY["routed background"]
  if ctx.is_arena_maintenance():
    return BASH_COMMAND_POLICY["arena utility"]
  if ctx.is_inbox_recovery():
    return BASH_COMMAND_POLICY["inbox recovery"]
  if ctx.is_standalone_mkdir():
    return BASH_COMMAND_POLICY["standalone mkdir"]
  text = shell_line(ctx.line or ctx.command).strip()
  if (not ctx.command or shell_line(ctx.command).strip() == text) and not any(
    mark in text for mark in ("$(", "`", "\n")
  ):
    lexer = shlex.shlex(text, posix=True, punctuation_chars="|;&<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
      words = list(lexer)
    except ValueError:
      words = []
    if (
      len(words) >= 2
      and words[0] == "steer"
      and not any(word and all(char in "|;&<>()" for char in word) for word in words)
    ):
      if words[1] in {"msg", "survey", "skills", "ren", "serve"}:
        return BASH_COMMAND_POLICY["steering exempt"]
      if words[1] in {"tasks", "task", "reports", "report", "worktree"}:
        return BASH_COMMAND_POLICY["steering count exempt"]
  pieces = ctx.pieces()
  if len(pieces) == 1 and pieces[0][1] == ";":
    words = command_words(pieces[0][0])
    if words:
      key = Path(words[0]).name
      if key in BASH_COMMAND_POLICY:
        return BASH_COMMAND_POLICY[key]
  return BASH_COMMAND_POLICY["*"]


def embedded_pieces(words: list[str]) -> list[tuple[list[str], str]]:
  """Read the command string passed to a shell's -c option, if present."""
  if not words or Path(words[0]).name not in SHELL_NAMES:
    return []
  for index in range(1, len(words) - 1):
    if words[index].startswith("-") and "c" in words[index][1:]:
      return PreContext(line=words[index + 1]).pieces()
  return []


@dataclass(frozen=True)
class RouteRequest:
  """A shell line the CLI must handle instead of executing it again."""

  kind: str
  args: tuple[str, ...]


@dataclass
class PreResult:
  """One step outcome: the text to print, verdict, route, and blocking guard."""

  messages: list[str] = field(default_factory=list)
  verdict: int = CONTINUE
  done: tuple[str, ...] = ()
  route: RouteRequest | None = None
  blocked_by: str = ""


class PreStep(Protocol):
  """A single pre-command check."""

  name: str

  def applies(self, ctx: PreContext) -> bool:
    """Report whether this step runs for the line."""

  def run(self, ctx: PreContext) -> PreResult:
    """Return the messages and verdict for the line."""


class InboxPipeGuard:
  """Keep inbox reads and polls from losing their output to a pipe."""

  name = "inbox-pipe"

  def applies(self, ctx: PreContext) -> bool:
    def inbox_read(words: list[str]) -> bool:
      words = command_words(words)
      return (
        len(words) >= 2
        and Path(words[0]).name == "steer"
        and words[1] in {"read", "poll"}
      )

    for words, separator in ctx.pieces():
      words = command_words(words)
      if separator in {"|", "|&"} and inbox_read(words):
        return True
      if any(
        inbox_read(inner) and (separator in {"|", "|&"} or following in {"|", "|&"})
        for inner, following in embedded_pieces(words)
      ):
        return True
    return False

  def run(self, ctx: PreContext) -> PreResult:
    return PreResult(
      [
        "VIOLATION: Inbox piped.",
        "Run `steer read` or `steer poll` alone to keep the full output visible.",
      ],
      BLOCK,
    )


class PollTimeoutGuard:
  """Keep a shell timeout from cutting off a poll's own wait."""

  name = "poll-timeout"

  def applies(self, ctx: PreContext) -> bool:
    def poll(words: list[str]) -> bool:
      words = command_words(words)
      return len(words) >= 2 and Path(words[0]).name == "steer" and words[1] == "poll"

    for words, _ in ctx.pieces():
      words = command_words(words)
      if not words or Path(words[0]).name != "timeout":
        continue
      args = words[1:]
      while args and args[0].startswith("-"):
        option, *args = args
        if option in {"-s", "--signal", "-k", "--kill-after"} and args:
          args = args[1:]
      target = command_words(args[1:]) if args else []  # Skip the duration.
      if poll(target) or any(poll(inner) for inner, _ in embedded_pieces(target)):
        return True
    return False

  def run(self, ctx: PreContext) -> PreResult:
    return PreResult(
      [
        "VIOLATION: timeout wrapped poll. Set Bash tool timeout 1800s.",
        "Poll returns within 1799s. Do not use shell `timeout`.",
      ],
      BLOCK,
    )


def _line_commands(ctx: PreContext, *, unwrap_timeout: bool = False) -> list[list[str]]:
  """Include direct commands and shell -c scripts without matching quoted prose."""
  pieces = [
    command_words(words, unwrap_timeout=unwrap_timeout) for words, _ in ctx.pieces()
  ]
  return [
    *pieces,
    *(
      command_words(inner, unwrap_timeout=unwrap_timeout)
      for words in pieces
      for inner, _ in embedded_pieces(words)
    ),
  ]


LIVE_RUN_LOG_VIOLATION = (
  "VIOLATION: Repeated read of live-run logs. Create a worktree associated "
  "with the next upcoming task using `steer worktree add <name>`. The live "
  "run will send its verdict in the inbox."
)


MANUAL_RUN_READ_VERBS = (
  "awk",
  "cat",
  "egrep",
  "fgrep",
  "grep",
  "head",
  "less",
  "more",
  "rg",
  "sed",
  "tail",
)
RUN_FILE_SUFFIXES = (".log", ".exit")
READER_PATH_PATTERN = re.compile(r'^path = ("(?:\\.|[^"\\])*")$', flags=re.MULTILINE)


def native_read_paths(line: str) -> list[str]:
  """List absolute paths passed to the native read_file script."""
  text = line.replace("\r\n", "\n").strip()
  if not text.startswith("/bin/bash -l -c python3 -I - <<'PY'"):
    return []
  paths: list[str] = []
  for encoded in READER_PATH_PATTERN.findall(text):
    try:
      path = json.loads(encoded)
    except json.JSONDecodeError:
      continue
    if isinstance(path, str) and Path(path).is_absolute():
      paths.append(path)
  return paths


def live_run_file_names(ctx: PreContext) -> dict[str, set[str]]:
  """Map each live run name to its log and exit file names."""
  if ctx.runs_dir is None:
    return {}
  runs_dir = Path(os.path.abspath(ctx.runs_dir))
  live: dict[str, set[str]] = {}
  for name, exit_name in ctx.active_run_ids:
    if (runs_dir / exit_name).exists():
      continue
    log_name = f"{Path(exit_name).stem}.log"
    live.setdefault(name, set()).update({exit_name, log_name})
  return live


def manual_live_run_read(ctx: PreContext) -> tuple[str, str] | None:
  """Name a manual read of a live run's log or exit file in this line."""
  live = live_run_file_names(ctx)
  if not live:
    return None
  runs_dir = Path(os.path.abspath(ctx.runs_dir or "."))
  cwd = Path(os.path.abspath(ctx.cwd or repo_root()))
  targets = native_read_paths(ctx.line or ctx.command)
  for words in _line_commands(ctx, unwrap_timeout=True):
    if not words or Path(words[0]).name not in MANUAL_RUN_READ_VERBS:
      continue
    targets.extend(word for word in words[1:] if Path(word).suffix in RUN_FILE_SUFFIXES)
  for token in targets:
    path = Path(token)
    target = Path(os.path.abspath(path if path.is_absolute() else cwd / path))
    if target.parent != runs_dir:
      continue
    for name, names in live.items():
      if any(fnmatch.fnmatch(name_, target.name) for name_ in names):
        return name, target.name
  return None


def manual_live_run_read_violation(name: str, file_name: str) -> str:
  """Offer the three legal routes for a live run's files."""
  return (
    f"VIOLATION: Manual read of live run {name} file {file_name}. "
    f"Use `steer run logs {name}`, `steer run rerun {name}` after it exits, "
    "or `steer worktree add <name>` for the next task."
  )


class ManualLiveRunReadGuard:
  """Block shell reads of a live run's log or exit file."""

  name = "manual-live-run-read"

  def applies(self, ctx: PreContext) -> bool:
    return manual_live_run_read(ctx) is not None

  def run(self, ctx: PreContext) -> PreResult:
    found = manual_live_run_read(ctx) or ("a live run", "its log")
    return PreResult(
      [manual_live_run_read_violation(*found)], BLOCK, blocked_by=self.name
    )


def live_run_log_read_ids(ctx: PreContext) -> tuple[str, ...]:
  """Return active run IDs named by `steer run logs` in this shell line."""
  active_by_name = dict(ctx.active_run_ids)
  if not active_by_name:
    return ()
  run_ids: list[str] = []
  for words in _line_commands(ctx, unwrap_timeout=True):
    if (
      len(words) < 4
      or Path(words[0]).name != "steer"
      or words[1:3] != ["run", "logs"]
      or any(flag in words[3:] for flag in ("-h", "--help", "--version", "-V"))
    ):
      continue
    if run_id := active_by_name.get(words[3]):
      run_ids.append(run_id)
  return tuple(run_ids)


class LiveRunLogGuard:
  """Block every repeat read of a run's logs while that run is live."""

  name = "live-run-logs"

  def applies(self, ctx: PreContext) -> bool:
    return bool(live_run_log_read_ids(ctx))

  def run(self, ctx: PreContext) -> PreResult:
    seen = set(ctx.seen_live_run_log_ids)
    for run_id in live_run_log_read_ids(ctx):
      if run_id in seen:
        return PreResult([LIVE_RUN_LOG_VIOLATION], BLOCK, blocked_by=self.name)
      seen.add(run_id)
    return PreResult()


def remote_git_push(words: list[str]) -> bool:
  """Recognize Git's push subcommand, not `git stash push` or option values."""
  if not words or Path(words[0]).name != "git":
    return False
  args = words[1:]
  index = 0
  values = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
  }
  flags = {"-p", "--paginate", "-P", "--no-pager", "--no-replace-objects", "--bare"}
  prefixes = (
    "-C",
    "-c",
    "--git-dir=",
    "--work-tree=",
    "--namespace=",
    "--super-prefix=",
    "--config-env=",
  )
  while index < len(args):
    word = args[index]
    if word in {"-h", "--help", "-v", "--version"}:
      return False
    if word in values:
      index += 2
    elif word in flags or word.startswith(prefixes):
      index += 1
    elif word.startswith("-"):
      return "push" in args[index + 1 :]
    else:
      return word == "push"
  return False


def _api_pr_change(args: list[str], client: str) -> bool:
  """Recognize GitHub PR-state writes, not title and body updates."""
  method, endpoint = "", ""
  fields: dict[str, object] = {}
  body = False
  value_flags = {
    "-X",
    "--method",
    "--request",
    "-d",
    "--data",
    "--data-raw",
    "--json",
    "--input",
    "--body-data",
    "--post-data",
    "-f",
    "-F",
    "--field",
    "--raw-field",
  }
  index = 0
  while index < len(args):
    word = args[index]
    option, equal, value = word.partition("=")
    if not equal and len(word) > 2 and word[:2] in {"-X", "-d", "-f", "-F"}:
      option, value, equal = word[:2], word[2:], "="
    if option in value_flags:
      if not equal:
        index += 1
        value = args[index] if index < len(args) else ""
      if option in {"-X", "--method", "--request"}:
        method = value.upper()
      else:
        body = True
        if option in {"-f", "-F", "--field", "--raw-field"}:
          key, _, field = value.partition("=")
          if option in {"-F", "--field"} and field.startswith("@") and field != "@-":
            try:
              field = Path(field[1:]).read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
              field = ""
          fields[key] = field
        else:
          if option == "--input" and value != "-":
            try:
              value = Path(value).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
              value = ""
          try:
            data = json.loads(value)
          except (ValueError, TypeError):
            data = {}
          if isinstance(data, dict):
            fields.update(data)
    elif client == "gh" and (
      word == "graphql" or word.lstrip("/").startswith("repos/")
    ):
      endpoint = word.lstrip("/").split("?", 1)[0]
    elif word.startswith(("https://", "http://")):
      url = urlsplit(word)
      if client == "gh" or url.hostname == "api.github.com":
        endpoint = url.path.lstrip("/")
        fields.update({key: values[-1] for key, values in parse_qs(url.query).items()})
    index += 1
  if endpoint == "graphql":
    query = fields.get("query", "")
    if not isinstance(query, str) or not re.search(r"\bmutation\b", query):
      return False
    if re.search(
      r"\b(?:mergePullRequest|closePullRequest|reopenPullRequest|enablePullRequestAutoMerge|"
      r"enqueuePullRequest|deleteRef|updateRef|mergeBranch)\s*\(",
      query,
    ):
      return True
    if not re.search(r"\bupdatePullRequest\s*\(", query):
      return False
    if re.search(r"\bstate\s*:\s*(?:CLOSED|OPEN)\b", query):
      return True
    variables = fields.get("variables", {})
    if isinstance(variables, str):
      try:
        variables = json.loads(variables)
      except ValueError:
        variables = {}

    def state_change(value: object) -> bool:
      return isinstance(value, dict) and any(
        (key == "state" and item in ("CLOSED", "OPEN")) or state_change(item)
        for key, item in value.items()
      )

    return bool(re.search(r"\binput\s*:\s*\$\w+", query)) and state_change(variables)
  if (method or ("POST" if body else "GET")) in {"GET", "HEAD"}:
    return False
  if re.fullmatch(r"repos/[^/]+/[^/]+/(?:pulls/\d+/merge(?:-async)?|merges)", endpoint):
    return True
  if re.fullmatch(r"repos/[^/]+/[^/]+/git/refs/heads/.+", endpoint):
    return method in {"DELETE", "PATCH"}
  return bool(
    method == "PATCH"
    and re.fullmatch(r"repos/[^/]+/[^/]+/(?:pulls|issues)/\d+", endpoint)
    and fields.get("state") in ("closed", "open")
  )


def _gh_help(args: list[str]) -> bool:
  """A help option used as comment text does not make a PR write read-only."""
  value_flags = {
    "--comment",
    "--body",
    "--subject",
    "--match-head-commit",
    "--author-email",
    "--repo",
    "-R",
    "-b",
  }
  return any(
    word in {"--help", "-h"} and (index == 0 or args[index - 1] not in value_flags)
    for index, word in enumerate(args)
  )


class PrStateGuard:
  """Stop visible PR merges, closures, reopens, and equivalent API writes."""

  name = "pr-state"

  def applies(self, ctx: PreContext) -> bool:
    for words in _line_commands(ctx):
      if not words:
        continue
      head, args = Path(words[0]).name, words[1:]
      if (
        head == "gh"
        and args[:1] == ["pr"]
        and len(args) >= 2
        and args[1] in {"merge", "close", "reopen"}
        and not _gh_help(args[2:])
      ):
        return True
      if head == "gh" and args[:1] == ["api"] and _api_pr_change(args[1:], head):
        return True
      if head in {"curl", "wget"} and _api_pr_change(args, head):
        return True
    return False

  def run(self, ctx: PreContext) -> PreResult:
    return PreResult(
      [
        "VIOLATION: PR mod blocked. NEVER merge, close, reopen.",
        "Use PR reads or metadata edits instead of state writes.",
      ],
      BLOCK,
    )


class DestructivePushGuard:
  """Block deletes and wholesale ref rewrites before any push executes."""

  name = "push-refs"

  def applies(self, ctx: PreContext) -> bool:
    for words in _line_commands(ctx):
      if not remote_git_push(words):
        continue
      args = words[words.index("push") + 1 :]
      if any(arg in {"--help", "-h", "--dry-run", "-n"} for arg in args):
        continue
      if any(arg in {"--delete", "-d", "--mirror", "--all", "--prune"} for arg in args):
        return True
      if any(arg.lstrip("+").startswith(":") for arg in args):
        return True
    return False

  def run(self, ctx: PreContext) -> PreResult:
    return PreResult(
      [
        "VIOLATION: Push blocked. Destructive mod found.",
        "Remove destructive push flags or ref deletion, then retry safely.",
      ],
      BLOCK,
    )


class GitSafetyGuard:
  """Inspect staged commits, managed worktrees, and fresh remote push ancestry."""

  name = "git-safe"

  def applies(self, ctx: PreContext) -> bool:
    return any(self.action(words) for words in _line_commands(ctx))

  def action(self, words: list[str]) -> str:
    if not words:
      return ""
    head, args = Path(words[0]).name, words[1:]
    if remote_git_push(words):
      return "push"
    if head == "git" and "commit" in args:
      return "commit"
    if head == "steer" and args[:2] == ["run", "start"]:
      return "gate"
    return ""

  def run(self, ctx: PreContext) -> PreResult:
    current_commands = _line_commands(PreContext(line=ctx.command or ctx.line))
    if ctx.cwd is None:
      if any(
        self.action(words) == "push"
        and not any(arg in {"--dry-run", "-n", "--help", "-h"} for arg in words)
        for words in current_commands
      ):
        return PreResult(
          ["Push blocked. remote default compare failed. Check remote and retry."],
          BLOCK,
        )
      return PreResult()
    for words in _line_commands(ctx):
      action = self.action(words)
      if action and ctx.checkout_root is not None:
        target = (
          git_safety.target_cwd(ctx.cwd, words)
          if action in {"commit", "push"}
          else ctx.cwd
        )
        if git_safety.managed_worktree(target, ctx.checkout_root):
          command = "run start" if action == "gate" else action
          return PreResult(
            [f"{command} blocked in worktree. Run in the main checkout."],
            BLOCK,
          )
    for current in current_commands:
      action = self.action(current)
      target = (
        git_safety.target_cwd(ctx.cwd, current)
        if action in {"commit", "push"}
        else ctx.cwd
      )
      if action == "commit":
        options = current[current.index("commit") + 1 :]
        auto_stage = any(
          word == "--all"
          or (word.startswith("-") and not word.startswith("--") and "a" in word[1:])
          for word in options
        )
        warning = git_safety.staged_warning(
          target,
          ctx.session_ids,
          renamable_ids=ctx.renamable_ids,
          include_tracked=auto_stage,
        )
        if warning:
          content = warning.startswith(
            (
              "Wireframe found staged.",
              "VIOLATION: Cited session-local ID.",
            )
          )
          return PreResult(
            [warning], BLOCK, blocked_by="commit-content" if content else ""
          )
      elif action == "push":
        warning = git_safety.push_warning(target, current)
        if warning:
          return PreResult([warning], BLOCK)
    return PreResult()


class InboxGate:
  """Refuse a line once the unacked count has waited past the call budget."""

  name = "gate"

  def applies(self, ctx: PreContext) -> bool:
    if ctx.is_inbox_recovery():
      return False
    if ctx.pending_ids and ctx.is_push():
      return True
    if ctx.is_arena_maintenance():
      return False
    if not ctx.pending_ids:
      return False
    return bash_command_policy(ctx)[1]

  def run(self, ctx: PreContext) -> PreResult:
    if ctx.is_push():
      return PreResult(
        [
          "VIOLATION: Push blocked. Inbox has pending. Read and ack all. Retry.",
          "Nothing left the sandbox. Use `steer read`, then `steer ack` for each item.",
          "Pending: " + ", ".join(ctx.pending_ids),
          f"Blocked by the part `{ctx.blocked_part()}`. Run the inbox calls alone.",
        ],
        BLOCK,
      )
    if GATE_THRESHOLD - ctx.call_count > 0:
      return PreResult()
    return PreResult(
      [
        "VIOLATION: Bash blocked. Read and ack all NOW.",
        f"Threshold: {GATE_THRESHOLD} calls. Use `steer read`, then `steer ack` for each item.",
        f"Blocked by the part `{ctx.blocked_part()}`. Run the inbox calls alone.",
      ],
      BLOCK,
    )


class WireframeGuard:
  """Block a commit that stages a wireframe capture."""

  name = "wireframe"

  def applies(self, ctx: PreContext) -> bool:
    return ctx.touches("git add", "git commit")

  def run(self, ctx: PreContext) -> PreResult:
    if WIREFRAME_SUFFIX not in ctx.line.lower():
      return PreResult()
    return PreResult(
      ["Commit has wireframe. Remove and retry."],
      BLOCK,
      blocked_by="commit-content",
    )


ACK_QUOTE = re.compile(r'(?:^|[;&|]\s*)\s*(?:\S*/)?steer\s+ack\b[^;|]*?"')


class AckQuoteReminder:
  """Warn before an ACK's double-quoted backticks run as shell commands."""

  name = "ack-quote"

  def applies(self, ctx: PreContext) -> bool:
    text = shell_line(ctx.line or ctx.command)
    match = ACK_QUOTE.search(text)
    if match is None:
      return False
    escaped = False
    for char in text[match.end() :]:
      if char == "`" and not escaped:
        return True
      if char == '"' and not escaped:
        break
      escaped = char == "\\" and not escaped
      if char != "\\":
        escaped = False
    return False

  def run(self, ctx: PreContext) -> PreResult:
    return PreResult(
      [
        "ACK WARNING: double-quoted backticks run as shell commands. Single-quote the ACK text."
      ]
    )


def unpushed_message(count: int, *, private: bool = False) -> str:
  """Name the push rule that applies to the repo's visibility."""
  if private:
    return f"{count} commit/s unpushed. Private repo: push small batches (<=3)."
  return f"{count} commit/s unpushed. ALWAYS push. NEVER delay for batch."


class UnpushedReminder:
  """Repeat the standing push rule whenever local commits sit ahead of origin."""

  name = "unpushed"

  def applies(self, ctx: PreContext) -> bool:
    return not (ctx.is_push() or ctx.is_arena_maintenance())

  def run(self, ctx: PreContext) -> PreResult:
    count = git_safety.unpushed_commit_count(repo_root())
    return PreResult([unpushed_message(count)] if count else [])


class BarePathReminder:
  """Remind the agent to call the executable by name, not by script path."""

  name = "bare"

  def applies(self, ctx: PreContext) -> bool:
    installed = Path.home() / ".local/bin/steer"
    for words in _line_commands(ctx):
      if words and (
        words[0].endswith("/scripts/steer") or Path(words[0]).expanduser() == installed
      ):
        return True
      if (
        len(words) > 1
        and Path(words[0]).name.startswith("python")
        and words[1].endswith("/scripts/steer")
      ):
        return True
    return False

  def run(self, ctx: PreContext) -> PreResult:
    return PreResult(
      ["Use bare `steer` from any directory. It works without the full path."]
    )


SLEEP_IDLE_CAP_SECONDS = 60
SLEEP_UNIT_SECONDS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
SLEEP_LOG_READERS = {"cat", "head", "tail", "grep", "rg", "sed", "less"}


def _sleep_seconds(args: list[str]) -> float:
  total = 0.0
  for arg in args:
    unit = arg[-1:].lower()
    number = arg[:-1] if unit in SLEEP_UNIT_SECONDS else arg
    try:
      value = float(number)
    except ValueError:
      continue
    if value > 0:
      total += value * SLEEP_UNIT_SECONDS.get(unit, 1.0)
  return total


class SleepGuard:
  """Block sleeps beside steering work, logs, or a live run, and long waits."""

  name = "sleep"

  def applies(self, ctx: PreContext) -> bool:
    return (
      not _platform_stop_process(ctx.line, ctx.command)
      and not ctx.routed
      and any(
        words and Path(words[0]).name == "sleep" for words, _ in self.commands(ctx)
      )
    )

  def commands(self, ctx: PreContext) -> list[tuple[list[str], str]]:
    """Include shell -c commands and skip harmless wrapper words."""
    pieces = ctx.pieces()
    commands = [(command_words(words), separator) for words, separator in pieces]
    return [
      *commands,
      *(
        (command_words(inner), following)
        for words, _ in commands
        for inner, following in embedded_pieces(words)
      ),
    ]

  def run(self, ctx: PreContext) -> PreResult:
    commands = [
      (Path(words[0]).name, words[1:]) for words, _ in self.commands(ctx) if words
    ]
    if any(head == "steer" for head, _ in commands):
      return PreResult(
        ["VIOLATION: Sleep BANNED. Run alone."],
        BLOCK,
      )
    if ctx.pending_ids:
      return PreResult(
        ["VIOLATION: Sleep blocked while a violation waits for an ack. Run alone."],
        BLOCK,
      )
    if any(
      head in SLEEP_LOG_READERS and any(arg.endswith(".log") for arg in args)
      for head, args in commands
    ):
      return PreResult(
        [
          "VIOLATION: Sleep log read BANNED. Read log later, start worktree for open task, poll if no more task."
        ],
        BLOCK,
      )
    if ctx.active_runs:
      names = ", ".join(ctx.active_runs)
      return PreResult(
        [
          "VIOLATION: Sleep run BANNED. Read log later, start worktree for open task, poll if no more task.",
          f"Live run: {names}.",
        ],
        BLOCK,
      )
    if any(
      head == "sleep" and _sleep_seconds(args) >= SLEEP_IDLE_CAP_SECONDS
      for head, args in commands
    ):
      return PreResult(
        [f"VIOLATION: Sleep BANNED over {SLEEP_IDLE_CAP_SECONDS}s. Use detached runs."],
        BLOCK,
      )
    return PreResult()


class BibleReadGuard:
  """Block a partial read of the bible instead of a full read."""

  name = "bible"

  VERBS = ("awk", "cat", "grep", "head", "sed", "tail", "rg")
  INSTALLED_SKILL = Path(".agents/skills/arena-steering/SKILL.md")

  def applies(self, ctx: PreContext) -> bool:
    return self.bible_path(ctx) is not None

  def bible_path(self, ctx: PreContext) -> str | None:
    """Name a protected read target, leaving checked-in skill source searchable."""
    root = Path(os.path.abspath(ctx.checkout_root or repo_root()))
    cwd = Path(os.path.abspath(ctx.cwd or root))
    protected = {root / "AGENTS.md", root / "ARENA.md", root / self.INSTALLED_SKILL}
    for words in _line_commands(ctx):
      if not words or Path(words[0]).name not in self.VERBS:
        continue
      if Path(words[0]).name == "rg" and "--files" in words[1:]:
        continue  # File discovery does not read file contents.
      for token in words[1:]:
        path = Path(token)
        if path.name not in {"AGENTS.md", "ARENA.md", "SKILL.md"}:
          continue
        target = Path(os.path.abspath(path if path.is_absolute() else cwd / path))
        if target in protected:
          return home_relative(target)
    return None

  def run(self, ctx: PreContext) -> PreResult:
    found = self.bible_path(ctx) or "the bible"
    return PreResult(
      [
        f"VIOLATION: Partial read blocked: `{found}`. Use `read_file` only if absent from context.",
        "Read protected bible files in full, never through Bash.",
      ],
      BLOCK,
    )


TEST_RUNNERS = {"pytest", "py.test", "tox", "nox"}
TEST_COMMANDS = (
  (("npm", "test"), "npm-tests"),
  (("npm", "run", "test*"), "npm-tests"),
  (("yarn", "test"), "yarn-tests"),
  (("pnpm", "test"), "pnpm-tests"),
  (("make", "test"), "make-tests"),
  (("npx", "jest"), "npx-tests"),
  (("npx", "vitest"), "npx-tests"),
  (("npx", "mocha"), "npx-tests"),
  (("npx", "ava"), "npx-tests"),
  (("go", "test"), "go-tests"),
  (("cargo", "test"), "cargo-tests"),
  (("dotnet", "test"), "dotnet-tests"),
  (("mvn", "test"), "mvn-tests"),
  (("mvnw", "test"), "mvn-tests"),
  (("gradle", "test"), "gradle-tests"),
  (("gradlew", "test"), "gradle-tests"),
)
GITHUB_LOG = re.compile(r"/?repos/[^/]+/[^/]+/actions/(jobs|runs)/(\d+)/logs/?$")
GITHUB_PR = re.compile(r"/?repos/[^/]+/[^/]+/pulls/(\d+)/?$")


def _test_route(words: list[str]) -> RouteRequest | None:
  if not words:
    return None
  head, args = Path(words[0]).name, words[1:]
  if any(flag in args for flag in ("--help", "-h", "--version", "-V")):
    return None
  if head in TEST_RUNNERS:
    return RouteRequest("run", ("pytest" if head == "py.test" else head,))
  if head.startswith("python") and args[:2] == ["-m", "pytest"]:
    return RouteRequest("run", ("pytest",))
  if head == "node" and args[:1] == ["--test"]:
    return RouteRequest("run", ("node-tests",))
  if head == "gh" and args[:2] == ["pr", "checks"] and "--watch" in args:
    return RouteRequest("run", ("pr-checks",))
  if head == "gh" and args[:2] == ["run", "watch"]:
    return RouteRequest("run", ("run-watch",))
  for command, name in TEST_COMMANDS:
    if head != command[0]:
      continue
    selected = args[: len(command) - 1]
    if len(selected) == len(command) - 1 and all(
      value.startswith(expected[:-1]) if expected.endswith("*") else value == expected
      for value, expected in zip(selected, command[1:])
    ):
      return RouteRequest("run", (name,))
  return None


def _pr_patch_fields(args: list[str]) -> tuple[str, str]:
  title, body_file = "", ""
  for index, word in enumerate(args):
    value = ""
    if word in {"-f", "-F", "--field", "--raw-field"} and index + 1 < len(args):
      value = args[index + 1]
    elif word.startswith(("--field=", "--raw-field=")):
      value = word.split("=", 1)[1]
    elif word.startswith(("-f", "-F")) and len(word) > 2:
      value = word[2:]
    key, sep, field = value.partition("=")
    if sep and key == "title":
      title = field
    elif sep and key == "body" and field.startswith("@"):
      body_file = field[1:]
  return title, body_file


def _github_route(words: list[str]) -> RouteRequest | None:
  if not words:
    return None
  head, args = Path(words[0]).name, words[1:]
  if head not in {"gh", "curl"} or (head == "gh" and args[:1] != ["api"]):
    return None
  if any(flag in args for flag in ("--help", "-h", "--version", "-V")):
    return None
  for arg in args:
    url = urlsplit(arg)
    if url.scheme:
      if url.hostname != "api.github.com":
        continue
      path = url.path
    elif head == "gh":
      path = arg.split("?", 1)[0]
    else:
      continue
    if match := GITHUB_LOG.fullmatch(path):
      log_type, number = match.groups()
      return RouteRequest("job-log", (log_type, number))
    if match := GITHUB_PR.fullmatch(path):
      method = ""
      for index, flag in enumerate(args):
        if flag in {"-X", "--method", "--request"} and index + 1 < len(args):
          method = args[index + 1].upper()
        elif flag.startswith(("-X", "--method=", "--request=")):
          method = flag.split("=", 1)[-1].upper() if "=" in flag else flag[2:].upper()
      if method == "PATCH":
        title, body_file = _pr_patch_fields(args)
        if title and body_file:
          return RouteRequest("pr-update", (match.group(1), title, body_file))
        if head == "curl":
          return RouteRequest("pr-update-manual", (match.group(1),))
        return RouteRequest("pr-update-possible", (match.group(1),))
  return None


def _line_routes(ctx: PreContext) -> list[RouteRequest]:
  """Return the routes a line names, in command order."""
  commands = _line_commands(ctx, unwrap_timeout=True)
  if ctx.command:
    commands.extend(_line_commands(PreContext(line=ctx.command), unwrap_timeout=True))
  commands.extend(
    words[1:]
    for words in commands[:]
    if words and words[0] in {"if", "then", "elif", "do"}
  )
  return [
    request
    for words in commands
    if (request := _test_route(words) or _github_route(words)) is not None
  ]


def _pr_patch_message(number: str) -> str:
  """Name the sanctioned path for a PR title and body change."""
  return (
    f"PR PATCH routed. Use `steer pr-update -n {number} "
    "-t <title> -f <workspace-file>` instead of direct curl PATCH."
  )


def post_push_watch(line: str) -> bool:
  """A successful line needs a check watch only after a real push or PR creation."""
  for words in _line_commands(PreContext(line=line), unwrap_timeout=True):
    if not words:
      continue
    head, args = Path(words[0]).name, words[1:]
    if any(flag in args for flag in ("--help", "-h", "--version", "-V")):
      continue
    if head == "gh" and args[:2] == ["pr", "create"]:
      return True
    if remote_git_push(words):
      options = args[args.index("push") + 1 :]
      if not any(flag in options for flag in ("-n", "--dry-run")):
        return True
  return False


def _mixed_route(line: str, command: str) -> RouteRequest | None:
  text = shell_line(line).strip()
  if "\n" in text:
    return None
  quote = ""
  escaped = False
  chunks: list[str] = []
  connectors: list[str] = []
  start = 0
  index = 0
  while index < len(text):
    char = text[index]
    if escaped:
      escaped = False
    elif char == "\\" and quote != "'":
      escaped = True
    elif quote:
      if char == quote:
        quote = ""
      elif quote == '"' and char in "$`":
        return None
    elif char in "'\"":
      quote = char
    elif char in "$`*?[]~#\n<>()":
      return None
    elif char in ";&|":
      connector = text[index : index + 2] if char in "&|" else char
      if connector not in {";", "&&", "||"}:
        return None
      chunks.append(text[start:index])
      connectors.append(connector)
      index += len(connector) - 1
      start = index + 1
    index += 1
  if quote or escaped or not connectors:
    return None
  chunks.append(text[start:])
  try:
    parts = [shlex.split(chunk) for chunk in chunks]
  except ValueError:
    return None
  if any(not part for part in parts):
    return None
  forbidden = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "for",
    "while",
    "until",
    "do",
    "done",
    "case",
    "esac",
    "function",
    "select",
    "time",
    "!",
    "{",
    "}",
  }
  if any(
    re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", part[0])
    or part[0] in forbidden
    or embedded_pieces(part)
    for part in parts
  ):
    return None
  if command and shell_line(command).strip() != text:
    try:
      if shlex.split(shell_line(command)) != parts[0]:
        return None
    except ValueError:
      return None
  scripts: list[str] = []
  routes: list[RouteRequest] = []
  for part in parts:
    route = _test_route(part) or _github_route(part)
    if route and route.kind in {"pr-update-manual", "pr-update-possible"}:
      return None
    if route:
      routes.append(route)
    if route and route.kind == "job-log":
      part = [
        "steer",
        "job-log",
        "-j" if route.args[0] == "jobs" else "-r",
        route.args[1],
      ]
    elif route and route.kind == "pr-update":
      part = [
        "steer",
        "pr-update",
        "-n",
        route.args[0],
        "-t",
        route.args[1],
        "-f",
        route.args[2],
      ]
    scripts.append(shlex.join(part))
  if not routes:
    return None
  script = scripts[0] + "".join(
    f" {connector} {part}" for connector, part in zip(connectors, scripts[1:])
  )
  background = next((route.args[0] for route in routes if route.kind == "run"), "")
  return RouteRequest("mixed", (script, background))


class RouteGuard:
  """Route single test, check-wait, PR-update, and job-log lines safely."""

  name = "route"

  def applies(self, ctx: PreContext) -> bool:
    if ctx.routed:
      return False
    commands = _line_commands(ctx, unwrap_timeout=True)
    commands.extend(_line_commands(PreContext(line=ctx.command), unwrap_timeout=True))
    commands.extend(
      words[1:]
      for words in commands[:]
      if words and words[0] in {"if", "then", "elif", "do"}
    )
    return any(_test_route(words) or _github_route(words) for words in commands)

  def run(self, ctx: PreContext) -> PreResult:
    pieces = ctx.pieces()
    routes = _line_routes(ctx)
    if len(pieces) > 1:
      patch = next((item for item in routes if item.kind.startswith("pr-update")), None)
      if patch is not None:
        return PreResult([_pr_patch_message(patch.args[0])], BLOCK)
      if routes and all(item.kind == "job-log" for item in routes):
        return PreResult()  # A plain GitHub read passes through with its pipeline.
      route = _mixed_route(ctx.line, ctx.command)
      if route is not None and route.kind == "mixed" and route.args[1]:
        return PreResult(verdict=END_CALL, route=route)
    mixed_syntax = ctx.is_mixed_line() and not (
      len(pieces) == 1 and _mixed_route(f"{shell_line(ctx.line)}; true", "")
    )
    if (
      mixed_syntax
      or len(pieces) != 1
      or pieces[0][1] != ";"
      or (ctx.command and shell_line(ctx.line) != shell_line(ctx.command))
    ):
      return PreResult(
        [
          (
            "Mixed shell blocked for auto detach. "
            "Use separate calls or explicit `steer run start`."
          ),
          "For a detached command, use `steer run start <name> -- <remaining command>`.",
        ],
        BLOCK,
      )
    route = routes[0]
    if route.kind == "pr-update-possible":
      return PreResult()  # Ordinary single-field metadata PATCHes remain allowed.
    if route.kind == "pr-update-manual":
      return PreResult([_pr_patch_message(route.args[0])], BLOCK)
    return PreResult(verdict=END_CALL, route=route)


class StartProcessTitleGuard:
  """Block `start_process` launches of `steer serve` without `{repo} - Steering`."""

  name = "start-title"

  def applies(self, ctx: PreContext) -> bool:
    meta = _parse_platform_launch(ctx.line, ctx.command)
    return (
      meta is not None
      and _steering_launch_error(meta, checkout_root=ctx.checkout_root) is not None
    )

  def run(self, ctx: PreContext) -> PreResult:
    meta = _parse_platform_launch(ctx.line, ctx.command)
    if meta is None:
      return PreResult()
    error = _steering_launch_error(meta, checkout_root=ctx.checkout_root)
    return PreResult([error], BLOCK) if error else PreResult()


def _asks_for_logs(args: list[str]) -> bool:
  """Return True when a `gh run view` argument list requests log output."""
  for arg in args:
    if arg in {"--log", "--log-failed", "-l"}:
      return True
    if arg.startswith("--log"):
      return True
  return False


class JobLogRedirectGuard:
  """Point `gh run view` log reads at the direct job-log helper."""

  name = "job-log-redirect"

  def applies(self, ctx: PreContext) -> bool:
    for words in _line_commands(ctx):
      if not words:
        continue
      head, args = Path(words[0]).name, words[1:]
      if head == "gh" and args[:2] == ["run", "view"] and _asks_for_logs(args[2:]):
        return True
    return False

  def run(self, ctx: PreContext) -> PreResult:
    # A redirect the shell cannot follow is a capability limit, so this reminds
    # and lets the command run.
    return PreResult(
      [
        "Reminder: `gh run view` log reads hit an egress redirect that the shell cannot follow.",
        (
          "Use the job-log recipe: `gh pr checks <PR>` for the job ID, then "
          "`steer job-log -j <job-id>`, then `functions.fetch_page` on the printed URL."
        ),
      ],
      CONTINUE,
    )


# Standing reminders rerun on every call and never join the finished list.
STANDING_STEPS = frozenset({"unpushed"})


PIPELINE: tuple[PreStep, ...] = (
  StartProcessTitleGuard(),
  ManualLiveRunReadGuard(),
  LiveRunLogGuard(),
  SleepGuard(),
  InboxPipeGuard(),
  PollTimeoutGuard(),
  PrStateGuard(),
  JobLogRedirectGuard(),
  DestructivePushGuard(),
  WireframeGuard(),
  InboxGate(),
  GitSafetyGuard(),
  BibleReadGuard(),
  RouteGuard(),
  AckQuoteReminder(),
  UnpushedReminder(),
  BarePathReminder(),
)


def run_pipeline(ctx: PreContext) -> PreResult:
  """Run every step in order and stop at the first verdict."""
  messages: list[str] = []
  mixed = ctx.is_mixed_line()
  finished = [name for name in ctx.done if name != "gate" or not mixed]
  for step in PIPELINE:
    if step.name in finished and not (
      (step.name == "gate" and ctx.is_push())
      or (step.name == "sleep" and ctx.active_runs)
      or step.name in STANDING_STEPS
      or step.name in {"git-safe", "route", "ack-quote", "live-run-logs"}
    ):
      continue
    if not step.applies(ctx):
      if step.name == "gate" and ctx.is_arena_maintenance() and not mixed:
        # An exact utility shell still runs profile cleanup before EXIT.
        finished.append(step.name)
      continue
    result = step.run(ctx)
    messages.extend(result.messages)
    if result.verdict == CONTINUE:
      if (
        step.name not in finished
        and step.name not in STANDING_STEPS
        and not (step.name == "gate" and mixed)
      ):
        finished.append(step.name)
      continue
    return PreResult(
      messages,
      result.verdict,
      tuple(finished),
      result.route,
      (result.blocked_by or step.name) if result.verdict == BLOCK else "",
    )
  return PreResult(messages, CONTINUE, tuple(finished))


def repo_root() -> Path:
  """Return the checkout root that holds the state directory."""
  checkout = Path(__file__).resolve().parents[3].parent
  return checkout.parent if checkout.name == ".agents" else checkout
