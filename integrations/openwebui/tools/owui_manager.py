"""
title: Open WebUI Manager
author: nemo
description: Open WebUI knowledge, skills, files, tools, and functions. Read and write approval follows permission mode.
required_open_webui_version: 0.12.0
version: 1.0.11
licence: nemoe7 Noncommercial and Public-Interest License 1.2.0
"""

# fmt: off

import asyncio
import inspect
import json
import logging
import re
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal
from urllib.parse import urlsplit

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# The answer of each tool call without a request context.
NO_CONTEXT = "Error: Open WebUI request context is unavailable."


def _split_ids(value: Any) -> list[str]:
  """Split a comma or newline separated id list into clean, unique ids."""
  if value is None:
    return []
  if isinstance(value, (list, tuple, set)):
    items = [str(item) for item in value]
  else:
    items = re.split(r"[,\n]", str(value))
  ids: list[str] = []
  for item in items:
    item = item.strip()
    if item and item not in ids:
      ids.append(item)
  return ids


def _entry_key(item: Any) -> str:
  """Return the id of one stored attachment entry, a dict or a plain id."""
  if isinstance(item, dict):
    return str(item.get("id") or item.get("collection_name") or "")
  return str(item)


def _merge_ids(current: Any, add: list[Any], remove: list[str]) -> list[Any]:
  """Merge IDs or knowledge references with exclusions and additions, preserving the order of retained entries."""
  gone = {str(item) for item in remove}
  merged = [item for item in (current or []) if _entry_key(item) not in gone]
  have = {_entry_key(item) for item in merged}
  for item in add:
    key = _entry_key(item)
    if key and key not in have:
      merged.append(item)
      have.add(key)
  return merged


class SkillFileInput(BaseModel):
  """One UTF-8 supporting file for a skill."""

  path: str = Field(description="Relative path within the skill")
  content: str = Field(description="Complete UTF-8 text content")


class SkillFileOperationInput(BaseModel):
  """One versioned skill-file mutation."""

  op: Literal["put", "move", "delete"] = Field(description="Operation to apply")
  path: str = Field(description="Relative source or target path")
  content: str | None = Field(default=None, description="UTF-8 content for put")
  destination: str | None = Field(default=None, description="Destination path for move")


def _bounded(method):
  signature = inspect.signature(method)
  @wraps(method)
  async def call(self, *args, **kwargs):
    arguments = signature.bind(self, *args, **kwargs).arguments
    emitter = arguments.get("__event_emitter__")
    await self._status(emitter, method.__name__, False)
    try:
      try:
        value = await method(self, *args, **kwargs)
      except Exception as error:
        value = {"error": str(error)}
        if self._output_size(value) <= self.valves.max_output_bytes:
          raise
      return self._output(value, method.__annotations__.get("return") in (str, "str"), arguments)
    finally:
      await self._status(emitter, method.__name__, True)

  return call


def _bounded_tools(cls):
  for name, method in list(vars(cls).items()):
    if not name.startswith("_") and inspect.iscoroutinefunction(method):
      setattr(cls, name, _bounded(method))
  return cls


@_bounded_tools
class Tools:
  """Manage Open WebUI workspaces with approval rules set by the permission mode."""

  class Valves(BaseModel):
    max_output_bytes: int = Field(default=32768, ge=1024, description="Maximum UTF-8 bytes in one tool result.")
    OWUI_API_BASE: str = "http://127.0.0.1:8080/api/v1"
    PRESET_MODELS: str = ""
    permissions: Literal["Always ask", "Allow reads", "Always allow"] = "Always ask"
    timeout_seconds: int = 60
    INSTALL_FETCH_TIMEOUT: float = 60.0
    TRUSTED_DOMAINS: str = "github.com,huggingface.co,githubusercontent.com"
    SHOW_STATUS: bool = True

  class UserValves(BaseModel):
    mode: Literal["default", "Always ask", "Allow reads", "Always allow"] = "default"
    timeout_seconds: int = 0

  async def _status(self, emitter, operation: str, done: bool) -> None:
    if not self.valves.SHOW_STATUS or not callable(emitter):
      return
    try:
      await emitter({"type": "status", "data": {"description": operation, "done": done}})
    except Exception:
      logger.warning("Could not send manager status for %s", operation)

  @staticmethod
  def _output_size(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
    return len(text.encode("utf-8"))

  def _output(self, value: Any, as_text: bool = False, arguments: dict | None = None) -> Any:
    limit = self.valves.max_output_bytes
    size = self._output_size(value)
    if size <= limit:
      return value
    result = {
      "error": "tool_output_too_large",
      "truncated": True,
      "output_bytes": size,
      "max_output_bytes": limit,
      "message": "Use a smaller page or text range. Do not repeat a write to recover output.",
      "identifiers": {},
    }
    pending = [arguments or {}]
    if isinstance(value, (dict, list)):
      pending.append(value)
    keys = {
      "id", "sha", "node_id", "html_url", "url", "file_id", "thread_id", "skill_id", "version_id",
      "tool_id", "function_id", "knowledge_id", "repository_full_name", "repo_full_name", "commit_sha", "blob_sha",
    }
    for _ in range(100):
      if not pending:
        break
      row = pending.pop()
      if isinstance(row, list):
        pending.extend(item for item in row[:100] if isinstance(item, (dict, list)))
        continue
      for key, item in row.items():
        if key in keys and key not in result["identifiers"] and isinstance(item, (str, int)):
          result["identifiers"][key] = item
          if self._output_size(result) > limit:
            del result["identifiers"][key]
        elif isinstance(item, (dict, list)):
          pending.append(item)
    return json.dumps(result, indent=2, ensure_ascii=False) if as_text else result

  def _page(self, payload: dict, offset: int, max_chars: int, field: str = "content") -> dict:
    text = payload[field]
    start = min(max(0, int(offset)), len(text))
    upper = min(max(1, int(max_chars)), 200000, len(text) - start)

    def page(count):
      end = start + count
      return {
        **payload,
        field: text[start:end],
        "offset": start,
        "next_offset": end if end < len(text) else None,
        "total_chars": len(text),
        "truncated": end < len(text),
      }

    lower = 0
    while lower < upper:
      middle = (lower + upper + 1) // 2
      if self._output_size({"result": page(middle)}) <= self.valves.max_output_bytes:
        lower = middle
      else:
        upper = middle - 1
    if lower == 0 and start < len(text):
      raise ValueError("Output metadata leaves no room for text. Use a smaller result.")
    return page(lower)

  def _list_page(
    self, items: list[dict], offset: int, max_results: int,
    *, page: int | None = None, more: bool = False, base_offset: int = 0, **metadata,
  ) -> str:
    start = min(max(0, int(offset)), len(items))
    upper = min(max(1, int(max_results)), 100, len(items) - start)

    def result(count):
      end = start + count
      within = end < len(items)
      value = {
        "total_count": len(items), **metadata,
        "items": items[start:end], "count": count, "offset": base_offset + start,
        "next_offset": base_offset + end if within or (more and page is None) else 0 if more else None,
      }
      if page is not None:
        value.update(page=page, next_page=page if within else page + 1 if more else None)
      return value

    lower = 0
    while lower < upper:
      middle = (lower + upper + 1) // 2
      if self._output_size(result(middle)) <= self.valves.max_output_bytes:
        lower = middle
      else:
        upper = middle - 1
    if lower == 0 and start < len(items):
      return self._output(result(1), as_text=True)
    return json.dumps(result(lower), indent=2, ensure_ascii=False)

  def __init__(self):
    self.valves = self.Valves()
    self._short_timeout = aiohttp.ClientTimeout(total=30)
    self._long_timeout = aiohttp.ClientTimeout(total=120)

  # ---------------------------------------------------------------- the gate

  @staticmethod
  def _user_valve(__user__: dict | None, name: str) -> Any:
    """Read one user valve, from the model or from a dict."""
    valves = (__user__ or {}).get("valves") or {}
    value = getattr(valves, name, None)
    if value is None and isinstance(valves, dict):
      value = valves.get(name)
    return value

  def _permissions(self, __user__: dict | None = None) -> str:
    """Resolve the permission mode, requiring confirmation for unknown values."""
    user_mode = self._user_valve(__user__, "mode")
    mode = self.valves.permissions if user_mode is None or user_mode == "default" else user_mode
    return mode if isinstance(mode, str) and mode in ("Always ask", "Allow reads", "Always allow") else "Always ask"

  def _wait_seconds(self, __user__: dict | None = None) -> int:
    """Use a nonzero user timeout, else the tool timeout or 60 seconds, with a one-second minimum."""
    user_wait = self._user_valve(__user__, "timeout_seconds")
    value = int(user_wait or 0) or int(self.valves.timeout_seconds or 0) or 60
    return max(1, value)

  async def _ask(
    self,
    action: str,
    detail: str,
    __event_call__: Callable | None = None,
    __user__: dict | None = None,
  ) -> bool:
    """Ask for confirmation within the configured timeout, returning False when approval fails."""
    if not callable(__event_call__):
      return False
    try:
      answer = await asyncio.wait_for(
        __event_call__(
          {
            "type": "confirmation",
            "data": {"title": f"Confirm: {action}", "message": detail},
          }
        ),
        timeout=self._wait_seconds(__user__),
      )
    except TimeoutError:
      return False
    except Exception:
      return False
    return answer is True

  async def _guard(
    self, action, detail, __user__, __event_call__, sensitive: bool = True
  ):
    """Return a refusal if the active permission mode requires confirmation and approval fails."""
    level = self._permissions(__user__)
    asked = level != "Always allow" if sensitive else level == "Always ask"
    if asked and not await self._ask(action, detail, __event_call__, __user__):
      return json.dumps(
        {
          "result": {
            "denied": True,
            "action": action,
            "reason": (
              f"You did not confirm within {self._wait_seconds(__user__)}s, so I "
              "sent nothing."
            ),
          }
        }
      )
    return None

  def _base(self) -> str:
    """The Open WebUI API base, from the valve, with no trailing slash."""
    base = (self.valves.OWUI_API_BASE or "").strip() or "http://127.0.0.1:8080/api/v1"
    return base.rstrip("/")

  def _auth(self, request):
    if request is None:
      return {}, {}
    headers = {}
    authorization = request.headers.get("authorization")
    if authorization:
      headers["Authorization"] = authorization
    return headers, dict(request.cookies)

  async def _request(
    self,
    session,
    method,
    path,
    *,
    timeout=None,
    expected=(200,),
    as_text=False,
    **kwargs,
  ):
    """Send an Open WebUI API request and return response data or an error."""
    try:
      async with session.request(
        method,
        f"{self._base()}{path}",
        timeout=timeout or self._short_timeout,
        **kwargs,
      ) as response:
        if response.status not in expected:
          text = await response.text()
          return (
            None,
            f"Open WebUI API error {response.status}: {text[:2000]}",
          )
        if response.status == 204:
          return {}, None
        if as_text:
          return {"_text": await response.text()}, None
        try:
          return await response.json(content_type=None), None
        except (ValueError, aiohttp.ContentTypeError):
          text = await response.text()
          return {"_text": text}, None
    except TimeoutError:
      return None, "Error: Open WebUI API request timed out."
    except aiohttp.ClientError as exc:
      return None, f"Error: Open WebUI connection failed: {exc}"

  def _path_parts(self, path):
    path = (path or "").strip().replace("\\", "/").strip("/")
    if not path:
      return None, "Error: path is empty."
    parts = [part.strip() for part in path.split("/") if part.strip()]
    if not parts or any(part in (".", "..") for part in parts):
      return None, "Error: invalid path. Do not use '.' or '..'."
    return parts, None

  def _file_item(self, item):
    return item.get("file", item) if isinstance(item, dict) else {}

  async def _directory_listing(self, session, knowledge_id, directory_id=""):
    """Read all pages of a single directory's contents."""
    page = 1
    all_items = []
    directories = []
    breadcrumbs = []
    while True:
      data, err = await self._request(
        session,
        "GET",
        f"/knowledge/{knowledge_id}/files",
        params={"directory_id": directory_id, "page": page},
      )
      if err:
        return None, err
      data = data or {}
      if page == 1:
        directories = data.get("directories", [])
        breadcrumbs = data.get("breadcrumbs", [])
      batch = data.get("items", [])
      all_items.extend(batch)
      total = data.get("total")
      if not batch or total is None or len(all_items) >= total:
        break
      page += 1
      if page > 1000:
        return (
          None,
          "Error: directory listing exceeded 1000 pages. Possible API issue.",
        )
    return {
      "directories": directories,
      "items": all_items,
      "breadcrumbs": breadcrumbs,
    }, None

  async def _resolve_directory(self, session, knowledge_id, parts):
    current = ""
    for part in parts:
      listing, err = await self._directory_listing(session, knowledge_id, current)
      if err:
        return None, err
      matches = [
        d
        for d in listing["directories"]
        if str(d.get("name", "")).casefold() == part.casefold()
      ]
      if not matches:
        return None, f"Directory not found: {part}"
      if len(matches) > 1:
        return None, f"Multiple matching directories found: {part}"
      current = matches[0].get("id")
    return current, None

  async def _find_file_in_directory(
    self, session, knowledge_id, directory_id, filename
  ):
    listing, err = await self._directory_listing(session, knowledge_id, directory_id)
    if err:
      return None, err
    matches = [
      self._file_item(x)
      for x in listing["items"]
      if self._file_item(x).get("filename", "").casefold() == filename.casefold()
    ]
    if not matches:
      return None, f"File not found: {filename}"
    if len(matches) > 1:
      return None, f"Multiple matching files found: {filename}"
    return matches[0], None

  async def _knowledge_has_content(
    self, session, knowledge_id, directory_id="", visited=None
  ):
    """Report whether any files exist at any nesting level of the knowledge base."""
    if visited is None:
      visited = set()
    key = directory_id or "ROOT"
    if key in visited:
      return False, None
    visited.add(key)

    listing, err = await self._directory_listing(session, knowledge_id, directory_id)
    if err:
      return None, err
    if listing["items"]:
      return True, None
    for directory in listing["directories"]:
      child_id = directory.get("id")
      if not child_id:
        continue
      has_content, err = await self._knowledge_has_content(
        session, knowledge_id, child_id, visited
      )
      if err or has_content:
        return has_content, err
    return False, None

  async def _open_session(self, request):
    headers, cookies = self._auth(request)
    return aiohttp.ClientSession(headers=headers, cookies=cookies)

  async def list_knowledge_bases(
    self,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    page: int = 1,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List the current user's Open WebUI knowledge bases with their IDs and write access.

    :param offset: row offset within this page
    :param max_results: maximum rows, subject to the output byte limit
    :param page: API page, from 1
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      current = max(1, int(page))
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", "/knowledge/", params={"page": current})
      if err:
        return err
      items = (data or {}).get("items") or []
      rows = [{key: item[key] for key in (
        "id", "name", "write_access", "access_control", "access_grants", "user_id"
      ) if key in item} for item in items]
      total = (data or {}).get("total")
      more = current * 30 < total if isinstance(total, int) else len(items) == 30
      return self._list_page(rows, offset, max_results, page=current, more=more, total_count=total)

    return await self._read("read list_knowledge_bases", str(page), run, __user__, __event_call__)

  async def list_knowledge_files(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    directory_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    page: int = 1,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List a knowledge directory's files, child directories, and breadcrumbs with their IDs.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param directory_id: the directory id to list, or the knowledge root when absent
    :param offset: row offset within this API page
    :param max_results: maximum rows, subject to the output byte limit
    :param page: API page, from 1
    """

    async def run(
      knowledge_id=knowledge_id,
      directory_id=directory_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      current = max(1, int(page))
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", f"/knowledge/{knowledge_id}/files",
          params={"directory_id": directory_id, "page": current})
      if err:
        return err
      data = data or {}
      rows = []
      if current == 1:
        rows.extend({"kind": "directory", "directory_id": item.get("id"),
          "name": item.get("name"), "parent_id": item.get("parent_id")}
          for item in data.get("directories") or [])
      files = data.get("items") or []
      rows.extend({"kind": "file", "file_id": self._file_item(item).get("id"),
        "filename": self._file_item(item).get("filename")} for item in files)
      total = data.get("total")
      more = current * 30 < total if isinstance(total, int) else len(files) == 30
      breadcrumbs = [{key: item[key] for key in ("id", "name", "parent_id") if key in item}
                     for item in data.get("breadcrumbs") or []]
      return self._list_page(rows, offset, max_results, page=current, more=more,
        knowledge_id=knowledge_id, directory_id=directory_id, breadcrumbs=breadcrumbs,
        total_count=total)

    return await self._read(
      "read list_knowledge_files",
      f"Knowledge base: {knowledge_id}\nDirectory: {directory_id or 'ROOT'}\nPage: {page}",
      run,
      __user__,
      __event_call__,
    )

  async def create_knowledge_base(
    self,
    name: str = Field(..., description="Name of the new Open WebUI knowledge base."),
    description: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create a new Open WebUI knowledge base for the current user.

    :param name: name of the new Open WebUI knowledge base
    :param description: the description of the new knowledge base
    """

    refusal = await self._guard(
      "create a knowledge base",
      str(name),
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    name = name.strip()
    if not name:
      return "Error: knowledge base name is empty."
    async with await self._open_session(__request__) as session:
      data, err = await self._request(
        session,
        "POST",
        "/knowledge/create",
        expected=(200, 201),
        json={
          "name": name,
          "description": description.strip(),
          "access_grants": None,
        },
        timeout=self._short_timeout,
      )
    if err:
      return err
    attach = await self._attach_knowledge_to_presets([data.get("id")], __request__)
    return (
      f"Knowledge base created successfully.\nname={data.get('name', name)}\n"
      f"knowledge_id={data.get('id')}\ndescription={data.get('description', description)}\n"
      f"{attach}"
    )

  async def update_knowledge_base(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    new_name: str = "",
    new_description: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Update a knowledge base's name or description, retaining each current value when its input is empty.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param new_name: the new name, or empty to keep the current one
    :param new_description: the new description, or empty to keep the current one
    """

    refusal = await self._guard(
      "overwrite a knowledge base",
      f"{knowledge_id} -> {new_name}",
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as session:
      current, err = await self._request(session, "GET", f"/knowledge/{knowledge_id}")
      if err:
        return err
      payload = {
        "name": new_name.strip() or current.get("name", ""),
        "description": new_description.strip() or current.get("description", ""),
        "access_grants": current.get("access_grants"),
      }
      data, err = await self._request(
        session,
        "POST",
        f"/knowledge/{knowledge_id}/update",
        expected=(200, 201),
        json=payload,
        timeout=self._short_timeout,
      )
    if err:
      return err
    return (
      f"Knowledge base updated successfully.\n"
      f"knowledge_id={knowledge_id}\n"
      f"name={data.get('name', payload['name'])}\n"
      f"description={data.get('description', payload['description'])}"
    )

  async def create_knowledge_directory(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    name: str = Field(..., description="Name of the directory to create."),
    parent_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create a knowledge directory under the specified parent or at the knowledge root.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param name: name of the directory to create
    :param parent_id: the parent directory id, or empty for the knowledge root
    """

    refusal = await self._guard(
      "create a knowledge directory",
      str(name),
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    name = name.strip()
    if not name:
      return "Error: directory name is empty."
    async with await self._open_session(__request__) as session:
      data, err = await self._request(
        session,
        "POST",
        f"/knowledge/{knowledge_id}/dirs/create",
        expected=(200, 201),
        json={"name": name, "parent_id": parent_id or None},
      )
    if err:
      return err
    return (
      f"Knowledge directory created successfully.\n"
      f"name={data.get('name', name)}\n"
      f"directory_id={data.get('id')}\n"
      f"parent_id={data.get('parent_id')}"
    )

  async def ensure_knowledge_directory_path(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    path: str = Field(
      ...,
      description="Directory path to create or reuse, for example Servers/VPN/Notes.",
    ),
    __request__=None,
    __event_emitter__: Callable | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    Resolve a knowledge directory path, reusing existing directories and creating missing ones.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param path: directory path to create or reuse, for example Servers/VPN/Notes
    """
    refusal = await self._guard(
      "create a knowledge directory path",
      f"{knowledge_id}:{path}",
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    parts, err = self._path_parts(path)
    if err:
      return err
    if __request__ is None:
      return NO_CONTEXT
    created = []
    reused = []
    current = ""
    async with await self._open_session(__request__) as session:
      for part in parts:
        listing, err = await self._directory_listing(session, knowledge_id, current)
        if err:
          return err
        matches = [
          d
          for d in listing["directories"]
          if d.get("name", "").casefold() == part.casefold()
        ]
        if len(matches) > 1:
          return f"Multiple matching directories found: {part}"
        if matches:
          current = matches[0].get("id")
          reused.append(part)
          continue
        data, err = await self._request(
          session,
          "POST",
          f"/knowledge/{knowledge_id}/dirs/create",
          expected=(200, 201),
          json={"name": part, "parent_id": current or None},
        )
        if err:
          return err
        current = data.get("id")
        created.append(part)
    return (
      f"Knowledge directory path ready.\n"
      f"path={'/'.join(parts)}\n"
      f"directory_id={current}\n"
      f"created={', '.join(created) or 'none'}\n"
      f"reused={', '.join(reused) or 'none'}"
    )

  async def _upload(self, session, knowledge_id, directory_id, filename, content):
    form = aiohttp.FormData()
    form.add_field(
      "file",
      content.encode("utf-8"),
      filename=filename,
      content_type="text/markdown; charset=utf-8",
    )
    meta = {"knowledge_id": knowledge_id}
    if directory_id:
      meta["directory_id"] = directory_id
    form.add_field("metadata", json.dumps(meta), content_type="application/json")
    return await self._request(
      session,
      "POST",
      "/files/?process=true&process_in_background=false",
      expected=(200, 201),
      data=form,
      timeout=self._long_timeout,
    )

  async def create_knowledge_markdown(
    self,
    knowledge_id: str = Field(
      ..., description="ID of the target Open WebUI knowledge base."
    ),
    filename: str = Field(
      ...,
      description="Filename for the new Markdown document, for example server-notes.md.",
    ),
    content: str = Field(
      ..., description="Complete Markdown content to write into the new document."
    ),
    directory_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create and index a Markdown file in the specified knowledge directory or at the knowledge root.

    :param knowledge_id: ID of the target Open WebUI knowledge base
    :param filename: filename for the new Markdown document, for example server-notes.md
    :param content: complete Markdown content to write into the new document
    :param directory_id: the directory id to write into, or empty for the knowledge root
    """

    refusal = await self._guard(
      "create a knowledge file",
      str(filename),
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    filename = filename.strip()
    if not filename:
      return "Error: filename is empty."
    if "/" in filename or "\\" in filename:
      return "Error: filename must not contain directory separators."
    if not filename.lower().endswith(".md"):
      filename += ".md"
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as session:
      data, err = await self._upload(
        session, knowledge_id, directory_id, filename, content
      )
    if err:
      return err
    return (
      f"Created and indexed knowledge file successfully.\n"
      f"filename={data.get('filename', filename)}\n"
      f"file_id={data.get('id')}\n"
      f"knowledge_id={knowledge_id}\n"
      f"directory_id={directory_id or 'ROOT'}"
    )

  async def create_knowledge_markdown_at_path(
    self,
    knowledge_id: str = Field(
      ..., description="ID of the target Open WebUI knowledge base."
    ),
    path: str = Field(
      ...,
      description="Full path including filename, for example Projects/Servers/VPN/Notes/readme.md.",
    ),
    content: str = Field(
      ..., description="Complete Markdown content for the new file."
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create and index a Markdown file at a knowledge path, creating missing parent directories.

    :param knowledge_id: ID of the target Open WebUI knowledge base
    :param path: full path including filename, for example Projects/Servers/VPN/Notes/readme.md
    :param content: complete Markdown content for the new file
    """

    refusal = await self._guard(
      "create a knowledge file",
      f"{knowledge_id}:{path}",
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    parts, err = self._path_parts(path)
    if err:
      return err
    filename = parts[-1]
    if not filename.lower().endswith(".md"):
      filename += ".md"
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as session:
      directory, err = await self._ensure_dirs(session, knowledge_id, parts[:-1])
      if err:
        return err
      data, err = await self._upload(
        session, knowledge_id, directory, filename, content
      )
    if err:
      return err
    return (
      f"Knowledge Markdown file created successfully.\n"
      f"path={'/'.join(parts[:-1] + [filename])}\n"
      f"filename={data.get('filename', filename)}\n"
      f"file_id={data.get('id')}\n"
      f"directory_id={directory or 'ROOT'}"
    )

  async def _ensure_dirs(self, session, knowledge_id, parts):
    current = ""
    for part in parts:
      listing, err = await self._directory_listing(session, knowledge_id, current)
      if err:
        return None, err
      matches = [
        d
        for d in listing["directories"]
        if d.get("name", "").casefold() == part.casefold()
      ]
      if len(matches) > 1:
        return None, f"Multiple matching directories found: {part}"
      if matches:
        current = matches[0].get("id")
        continue
      data, err = await self._request(
        session,
        "POST",
        f"/knowledge/{knowledge_id}/dirs/create",
        expected=(200, 201),
        json={"name": part, "parent_id": current or None},
      )
      if err:
        return None, err
      current = data.get("id")
    return current, None

  async def _read_or_update_path(self, knowledge_id, path, content, update, request):
    parts, err = self._path_parts(path)
    if err:
      return err
    if request is None:
      return NO_CONTEXT
    filename = parts[-1]
    async with await self._open_session(request) as session:
      directory, err = await self._resolve_directory(session, knowledge_id, parts[:-1])
      if err:
        return err
      item, err = await self._find_file_in_directory(
        session, knowledge_id, directory, filename
      )
      if err:
        return err
      if update:
        _, err = await self._request(
          session,
          "POST",
          f"/files/{item.get('id')}/data/content/update",
          expected=(200, 201),
          json={"content": content},
          timeout=self._long_timeout,
        )
        if err:
          return err
        return (
          f"Knowledge file updated successfully.\n"
          f"path={path}\n"
          f"file_id={item.get('id')}\n"
          f"directory_id={directory or 'ROOT'}"
        )
      data, err = await self._request(
        session, "GET", f"/files/{item.get('id')}/data/content"
      )
    if err:
      return err
    return (
      f"path={path}\n"
      f"filename={filename}\n"
      f"file_id={item.get('id')}\n"
      f"directory_id={directory or 'ROOT'}\n"
      f"\n"
      f"{(data or {}).get('content', '')}"
    )

  async def update_knowledge_file(
    self,
    file_id: str = Field(
      ..., description="ID of the existing Open WebUI knowledge file."
    ),
    content: str = Field(
      ...,
      description="Complete new Markdown/text content that will replace the existing file content.",
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Replace a knowledge file's content and request reindexing.

    :param file_id: ID of the existing Open WebUI knowledge file
    :param content: complete new Markdown/text content that will replace the existing file content
    """

    refusal = await self._guard(
      "overwrite a knowledge file",
      str(file_id),
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as s:
      _, e = await self._request(
        s,
        "POST",
        f"/files/{file_id}/data/content/update",
        expected=(200, 201),
        json={"content": content},
        timeout=self._long_timeout,
      )
    return e or f"Knowledge file updated and reindexed successfully.\nfile_id={file_id}"

  async def read_knowledge_file(
    self,
    file_id: str = Field(
      ..., description="ID of the Open WebUI knowledge file to read."
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_chars: int = 10000,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Read a text range from a knowledge file before editing it.

    :param file_id: ID of the Open WebUI knowledge file to read
    :param offset: character offset in the returned text
    :param max_chars: maximum characters, subject to the output byte limit
    """

    async def run(
      file_id=file_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as s:
        data, e = await self._request(s, "GET", f"/files/{file_id}/data/content")
      if e:
        return e
      c = (data or {}).get("content", "")
      return c if c else "The file exists, but it holds no extracted text."

    return await self._read(
      "read read_knowledge_file",
      f"File: {file_id}",
      run,
      __user__,
      __event_call__,
      offset=offset, max_chars=max_chars, identifiers={"file_id": file_id},
    )

  async def update_knowledge_file_at_path(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    path: str = Field(
      ...,
      description="Full path to the existing file, for example Projects/Servers/VPN/Notes/readme.md.",
    ),
    content: str = Field(
      ...,
      description="Complete new content that will replace the existing file content.",
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Replace and reindex a knowledge file at an existing path without creating directories or files.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param path: full path to the existing file, for example Projects/Servers/VPN/Notes/readme.md
    :param content: complete new content that will replace the existing file content
    """

    refusal = await self._guard(
      "overwrite a knowledge file",
      f"{knowledge_id}:{path}",
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    return await self._read_or_update_path(
      knowledge_id, path, content, True, __request__
    )

  async def read_knowledge_file_at_path(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    path: str = Field(
      ...,
      description="Exact file path, for example Projects/Servers/VPN/Notes/readme.md.",
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_chars: int = 10000,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Read a knowledge file at an existing path without changing it.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param path: exact file path, for example Projects/Servers/VPN/Notes/readme.md
    :param offset: character offset in the returned text
    :param max_chars: maximum characters, subject to the output byte limit
    """

    async def run(
      knowledge_id=knowledge_id,
      path=path,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      return await self._read_or_update_path(
        knowledge_id, path, None, False, __request__
      )

    return await self._read(
      "read read_knowledge_file_at_path",
      f"Knowledge base: {knowledge_id}\nPath: {path}",
      run,
      __user__,
      __event_call__,
      offset=offset, max_chars=max_chars, identifiers={"knowledge_id": knowledge_id},
    )

  async def upsert_knowledge_markdown_at_path(
    self,
    knowledge_id: str = Field(
      ..., description="ID of the target Open WebUI knowledge base."
    ),
    path: str = Field(
      ...,
      description="Full Markdown file path, for example Projects/Servers/VPN/Notes/readme.md.",
    ),
    content: str = Field(
      ..., description="Complete Markdown content to create or replace."
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create or update a Markdown file at a knowledge path, creating missing directories and refusing ambiguous matches.

    :param knowledge_id: ID of the target Open WebUI knowledge base
    :param path: full Markdown file path, for example Projects/Servers/VPN/Notes/readme.md
    :param content: complete Markdown content to create or replace
    """

    refusal = await self._guard(
      "write a knowledge file",
      f"{knowledge_id}:{path}",
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    parts, err = self._path_parts(path)
    if err:
      return err
    filename = parts[-1]
    if not filename.lower().endswith(".md"):
      filename += ".md"
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as s:
      directory, err = await self._ensure_dirs(s, knowledge_id, parts[:-1])
      if err:
        return err
      item, finderr = await self._find_file_in_directory(
        s, knowledge_id, directory, filename
      )
      if finderr and finderr != f"File not found: {filename}":
        # API errors or ambiguous matches must not lead to creating
        # a new file and a possible duplicate.
        return finderr
      if not finderr:
        _, err = await self._request(
          s,
          "POST",
          f"/files/{item.get('id')}/data/content/update",
          expected=(200, 201),
          json={"content": content},
          timeout=self._long_timeout,
        )
        if err:
          return err
        return (
          f"Knowledge Markdown file updated successfully.\n"
          f"action=updated\n"
          f"path={'/'.join(parts[:-1] + [filename])}\n"
          f"file_id={item.get('id')}"
        )
      data, err = await self._upload(s, knowledge_id, directory, filename, content)
    if err:
      return err
    return (
      f"Knowledge Markdown file created successfully.\n"
      f"action=created\n"
      f"path={'/'.join(parts[:-1] + [filename])}\n"
      f"file_id={data.get('id')}"
    )

  async def rename_knowledge_file(
    self,
    file_id: str = Field(
      ..., description="ID of the Open WebUI knowledge file to rename."
    ),
    new_filename: str = Field(
      ...,
      description="New filename including extension, for example vpn-guide.md.",
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Rename an existing Open WebUI knowledge file.

    :param file_id: ID of the Open WebUI knowledge file to rename
    :param new_filename: new filename including extension, for example vpn-guide.md
    """

    refusal = await self._guard(
      "rename a knowledge file",
      f"{file_id} -> {new_filename}",
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    new_filename = new_filename.strip()
    if not new_filename:
      return "Error: new filename is empty."
    if "/" in new_filename or "\\" in new_filename:
      return "Error: filename must not contain directory separators."
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as s:
      _, e = await self._request(
        s,
        "POST",
        f"/files/{file_id}/rename",
        expected=(200, 201),
        json={"filename": new_filename},
      )
    return (
      e
      or f"Knowledge file renamed successfully.\nfile_id={file_id}\nnew_filename={new_filename}"
    )

  async def rename_knowledge_directory(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    directory_id: str = Field(..., description="ID of the directory to rename."),
    new_name: str = Field(..., description="New directory name."),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Rename a directory inside an Open WebUI knowledge base.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param directory_id: ID of the directory to rename
    :param new_name: new directory name
    """

    refusal = await self._guard(
      "rename a knowledge directory",
      f"{directory_id} -> {new_name}",
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as s:
      d, e = await self._request(
        s,
        "POST",
        f"/knowledge/{knowledge_id}/dirs/{directory_id}/update",
        expected=(200, 201),
        json={"name": new_name.strip()},
      )
    return (
      e
      or f"Knowledge directory renamed successfully.\n"
      f"directory_id={directory_id}\n"
      f"new_name={(d or {}).get('name', new_name)}"
    )

  async def move_knowledge_directory(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    directory_id: str = Field(..., description="ID of the directory to move."),
    target_parent_id: str = Field(
      ...,
      description=(
        "ID of the target parent directory. Use an empty string to move the directory"
        " to the knowledge base root."
      ),
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Move a knowledge directory under the specified parent, or to the root when the parent ID is empty.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param directory_id: ID of the directory to move
    :param target_parent_id: ID of the target parent directory. Use an empty string to move the
      directory to the knowledge base root
    """

    refusal = await self._guard(
      "move a knowledge directory",
      f"{directory_id} -> {target_parent_id}",
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as s:
      d, e = await self._request(
        s,
        "POST",
        f"/knowledge/{knowledge_id}/dirs/{directory_id}/update",
        expected=(200, 201),
        json={"parent_id": target_parent_id or None},
      )
    return (
      e
      or f"Knowledge directory moved successfully.\ndirectory_id={directory_id}\nparent_id={(d or {}).get('parent_id')}"
    )

  async def delete_knowledge_file(
    self,
    file_id: str = Field(
      ..., description="ID of the Open WebUI file to permanently delete."
    ),
    confirm: bool = Field(
      ...,
      description="Must be true. Set true only when the user explicitly requested permanent deletion of the file.",
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Permanently delete a knowledge file and its indexed data only when the user requests deletion and `confirm` is true.

    :param file_id: ID of the Open WebUI file to permanently delete
    :param confirm: Must be true. Set true only when the user explicitly requested permanent deletion of the file
    """

    refusal = await self._guard(
      "delete a knowledge file",
      str(file_id),
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if confirm is not True:
      return "Deletion cancelled: confirm must be true."
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as s:
      _, e = await self._request(s, "DELETE", f"/files/{file_id}", expected=(200, 204))
    return e or f"Knowledge file permanently deleted successfully.\nfile_id={file_id}"

  async def delete_knowledge_file_at_path(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    path: str = Field(..., description="Exact path of the file to permanently delete."),
    confirm: bool = Field(
      ...,
      description="Must be true. Use true only when the user explicitly requested permanent deletion of this file.",
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Permanently delete a knowledge file at an unambiguous path only when `confirm` is true.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param path: exact path of the file to permanently delete
    :param confirm: Must be true. Use true only when the user explicitly requested permanent deletion of this file
    """

    refusal = await self._guard(
      "delete a knowledge file",
      f"{knowledge_id}:{path}",
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if confirm is not True:
      return "Deletion cancelled: confirm must be true."
    parts, err = self._path_parts(path)
    if err:
      return err
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as s:
      directory, err = await self._resolve_directory(s, knowledge_id, parts[:-1])
      if err:
        return err
      item, err = await self._find_file_in_directory(
        s, knowledge_id, directory, parts[-1]
      )
      if err:
        return err
      _, err = await self._request(
        s, "DELETE", f"/files/{item.get('id')}", expected=(200, 204)
      )
    return (
      err
      or f"Knowledge file permanently deleted successfully.\npath={path}\nfile_id={item.get('id')}"
    )

  async def delete_knowledge_directory(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    directory_id: str = Field(..., description="ID of the directory to delete."),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete a knowledge directory and move its files to its parent without deleting them.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param directory_id: ID of the directory to delete
    """

    refusal = await self._guard(
      "delete a knowledge directory",
      str(directory_id),
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as s:
      _, e = await self._request(
        s,
        "DELETE",
        f"/knowledge/{knowledge_id}/dirs/{directory_id}/delete",
        expected=(200, 204),
        params={"move_files": "true"},
      )
    return (
      e or f"Knowledge directory deleted successfully.\ndirectory_id={directory_id}"
    )

  async def search_knowledge_files(
    self,
    query: str = Field(
      ...,
      description="Filename or text to search for across all accessible knowledge bases.",
    ),
    include_content: bool = False,
    page: int = 1,
    max_content_items: int = 10,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Search accessible knowledge files by filename or optional bounded text, with optional content previews.

    :param query: filename or text to search for across all accessible knowledge bases
    :param include_content: true to search the file text too, false for filenames alone
    :param page: the page of results, from 1
    :param max_content_items: how many matching files carry their text
    """

    async def run(
      query=query,
      include_content=include_content,
      page=page,
      max_content_items=max_content_items,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      query = query.strip()
      if not query:
        return "Error: search query is empty."
      if __request__ is None:
        return NO_CONTEXT
      page = max(1, page)
      max_content_items = max(1, min(max_content_items, 50))

      async with await self._open_session(__request__) as s:
        data, error = await self._request(
          s,
          "GET",
          "/knowledge/search/files",
          params={
            "query": query,
            "include_content": str(include_content).lower(),
            "page": page,
          },
        )
        if error:
          return error

        items = (data or {}).get("items", [])
        if not items:
          return f"No matching knowledge files found.\nquery={query}\npage={page}"

        result = [f"Search results for: {query}", f"page={page}", ""]
        for i, item in enumerate(items):
          file_data = self._file_item(item)
          filename = file_data.get("filename", file_data.get("name", "Unnamed"))
          file_id = file_data.get("id")
          result.append(f"- {filename} (file_id={file_id})")

          if include_content and file_id and i < max_content_items:
            # Read content separately for each requested preview.
            content_data, content_error = await self._request(
              s, "GET", f"/files/{file_id}/data/content"
            )
            if content_error:
              result.append(f"  content_preview_error={content_error}")
            else:
              content = (content_data or {}).get("content", "")
              result.append(
                f"  content_preview={content[:1000]}"
                if content
                else "  content_preview=<empty>"
              )

      return "\n".join(result)

    return await self._read(
      "read search_knowledge_files",
      f"Query: {query}\nPage: {page}",
      run,
      __user__,
      __event_call__,
    )

  async def find_and_read_knowledge_file(
    self,
    query: str = Field(
      ..., description="Filename or search text used to find the knowledge file."
    ),
    exact_filename: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Find and read a first-page knowledge match, preferring filenames and listing IDs when matches are ambiguous.

    :param query: filename or search text used to find the knowledge file
    :param exact_filename: the exact filename to match
    """

    async def run(
      query=query,
      exact_filename=exact_filename,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as s:
        d, e = await self._request(
          s,
          "GET",
          "/knowledge/search/files",
          params={
            "query": query.strip(),
            "include_content": "false",
            "page": 1,
          },
        )
        if e:
          return e
        items = (d or {}).get("items", [])
        if exact_filename:
          exact = [
            x
            for x in items
            if x.get("filename", "").casefold() == exact_filename.strip().casefold()
          ]
          if exact:
            items = exact
        if not items:
          return "No matching knowledge file found."
        if len(items) > 1:
          return "Multiple matching files found:\n" + "\n".join(
            f"- {x.get('filename', 'Unnamed')} (file_id={x.get('id')})" for x in items
          )
        x = items[0]
        data, e = await self._request(s, "GET", f"/files/{x.get('id')}/data/content")
      return (
        e
        or f"filename={x.get('filename', 'Unnamed')}\nfile_id={x.get('id')}\n\n{(data or {}).get('content', '')}"
      )

    return await self._read(
      "read find_and_read_knowledge_file",
      f"Query: {query}\nExact filename: {exact_filename}",
      run,
      __user__,
      __event_call__,
    )

  async def get_knowledge_tree(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    max_depth: int = 20,
    max_nodes: int = 1000,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Read a knowledge directory and file tree with IDs, subject to depth, node, and output limits.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param max_depth: how deep the tree goes, 1 or more
    :param max_nodes: the most nodes to return
    """

    async def run(
      knowledge_id=knowledge_id,
      max_depth=max_depth,
      max_nodes=max_nodes,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      max_depth = max(1, min(max_depth, 50))
      max_nodes = max(1, min(max_nodes, 5000))
      lines = []
      count = 0
      visited = set()
      async with await self._open_session(__request__) as s:
        meta, e = await self._request(s, "GET", f"/knowledge/{knowledge_id}")
        if e:
          return e
        lines.append(
          f"{meta.get('name', 'Knowledge Base')} (knowledge_id={knowledge_id})"
        )

        async def walk(directory, depth):
          nonlocal count
          if count >= max_nodes or depth > max_depth:
            return
          key = directory or "ROOT"
          if key in visited:
            return
          visited.add(key)
          listing, e = await self._directory_listing(s, knowledge_id, directory)
          if e:
            lines.append("  " * depth + e)
            return
          for x in listing["items"]:
            if count >= max_nodes:
              return
            count += 1
            f = self._file_item(x)
            lines.append(
              "  " * depth
              + f"📄 {f.get('filename', 'Unnamed')} (file_id={f.get('id')})"
            )
          for x in listing["directories"]:
            if count >= max_nodes:
              return
            count += 1
            lines.append(
              "  " * depth
              + f"📁 {x.get('name', 'Unnamed')} (directory_id={x.get('id')})"
            )
            await walk(x.get("id"), depth + 1)

        await walk("", 1)
      if count >= max_nodes:
        lines.append(f"\nTree truncated after {max_nodes} nodes.")
      return "\n".join(lines)

    return await self._read(
      "read get_knowledge_tree",
      f"Knowledge base: {knowledge_id}",
      run,
      __user__,
      __event_call__,
    )

  async def delete_knowledge_base(
    self,
    knowledge_id: str = Field(
      ...,
      description="ID of the Open WebUI knowledge base to permanently delete.",
    ),
    confirm: bool = Field(
      ...,
      description="Must be true. Use true only when the user explicitly requested deletion of the knowledge base.",
    ),
    allow_nonempty: bool = False,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete a knowledge base only when `confirm` is true, with `allow_nonempty` required when files remain.

    :param knowledge_id: ID of the Open WebUI knowledge base to permanently delete
    :param confirm: Must be true. Use true only when the user explicitly requested deletion of the knowledge base
    :param allow_nonempty: true to delete a base that still holds files
    """

    refusal = await self._guard(
      "delete a knowledge base",
      str(knowledge_id),
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if confirm is not True:
      return "Deletion cancelled: confirm must be true."
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as s:
      meta, e = await self._request(s, "GET", f"/knowledge/{knowledge_id}")
      if e:
        return e
      has_content, e = await self._knowledge_has_content(s, knowledge_id)
      if e:
        return e
      if has_content and not allow_nonempty:
        return "Deletion refused: knowledge base contains files. Use allow_nonempty=true only when explicitly intended."
      _, e = await self._request(
        s,
        "DELETE",
        f"/knowledge/{knowledge_id}/delete",
        expected=(200, 204),
      )
    return (
      e
      or f"Knowledge base permanently deleted successfully.\n"
      f"knowledge_id={knowledge_id}\n"
      f"name={meta.get('name', 'Unnamed')}"
    )

  async def _knowledge_reference(self, s, knowledge_id):
    """Build the stored knowledge reference of one knowledge base id."""
    data, err = await self._request(s, "GET", f"/knowledge/{knowledge_id}")
    if err:
      return None, err
    reference = {
      "id": knowledge_id,
      "name": (data or {}).get("name") or knowledge_id,
      "type": "collection",
    }
    description = (data or {}).get("description")
    if description:
      reference["description"] = description
    return reference, None

  def _preset_payload(self, record, add, remove):
    """Build the update body of one preset and the list of fields the update touches."""
    meta = dict(record.get("meta") or {})
    changed = []
    for key in (
      "knowledge",
      "toolIds",
      "skillIds",
      "filterIds",
      "actionIds",
      "functionIds",
    ):
      before = list(meta.get(key) or [])
      after = _merge_ids(before, add.get(key, []), remove.get(key, []))
      if after != before:
        meta[key] = after
        changed.append(key)
    payload = {
      "id": record.get("id"),
      "base_model_id": record.get("base_model_id"),
      "name": record.get("name"),
      "meta": meta,
      "params": record.get("params") or {},
      "access_grants": record.get("access_grants"),
      "is_active": record.get("is_active", True),
    }
    return payload, changed

  async def _write_preset(self, s, model_id, add, remove):
    """Read one preset, merge the attachment lists and write it back."""
    record, err = await self._request(
      s, "GET", "/models/model", params={"id": model_id}
    )
    if err:
      return None, err
    if not record:
      return None, f"Model preset not found: {model_id}"
    if not record.get("write_access"):
      return None, f"Skipped, no write access: {model_id}"
    resolved = dict(add)
    resolved["knowledge"] = []
    for knowledge_id in add.get("knowledge", []):
      reference, err = await self._knowledge_reference(s, knowledge_id)
      if err:
        return None, err
      resolved["knowledge"].append(reference)
    payload, changed = self._preset_payload(record, resolved, remove)
    if not changed:
      name = record.get("name") or model_id
      return f"- {name} (id={model_id}): no change", None
    _, err = await self._request(
      s,
      "POST",
      "/models/model/update",
      expected=(200, 201),
      json=payload,
      timeout=self._short_timeout,
    )
    if err:
      return None, err
    name = record.get("name") or model_id
    return f"- {name} (id={model_id}): changed {', '.join(changed)}", None

  def _preset_changes(
    self,
    add_knowledge_ids="",
    add_tool_ids="",
    add_skill_ids="",
    add_filter_ids="",
    add_action_ids="",
    add_function_ids="",
    remove_knowledge_ids="",
    remove_tool_ids="",
    remove_skill_ids="",
    remove_filter_ids="",
    remove_action_ids="",
    remove_function_ids="",
  ):
    add = {
      "knowledge": _split_ids(add_knowledge_ids),
      "toolIds": _split_ids(add_tool_ids),
      "skillIds": _split_ids(add_skill_ids),
      "filterIds": _split_ids(add_filter_ids),
      "actionIds": _split_ids(add_action_ids),
      "functionIds": _split_ids(add_function_ids),
    }
    remove = {
      "knowledge": _split_ids(remove_knowledge_ids),
      "toolIds": _split_ids(remove_tool_ids),
      "skillIds": _split_ids(remove_skill_ids),
      "filterIds": _split_ids(remove_filter_ids),
      "actionIds": _split_ids(remove_action_ids),
      "functionIds": _split_ids(remove_function_ids),
    }
    return add, remove

  def _preset_filter(self) -> list[str]:
    """Parse PRESET_MODELS as preset IDs or names, with an empty list for all presets."""
    return _split_ids(self.valves.PRESET_MODELS)

  def _preset_selected(self, item) -> bool:
    """Match a preset ID or name against PRESET_MODELS, or accept every preset when the valve is empty."""
    wanted = self._preset_filter()
    if not wanted:
      return True
    return str(item.get("id")) in wanted or str(item.get("name")) in wanted

  async def _attach_to_presets(self, add: dict, __request__, remove=None) -> str:
    """Update attachments on selected writable presets and report changed, skipped, and failed updates."""
    add = {
      key: [str(i) for i in value if i] for key, value in (add or {}).items() if value
    }
    remove = {
      key: [str(i) for i in value if i]
      for key, value in (remove or {}).items()
      if value
    }
    if not add and not remove:
      return "preset_models: none"
    updated = 0
    skipped = []
    errors = []
    async with await self._open_session(__request__) as session:
      page = 1
      while page <= 100:
        data, err = await self._request(
          session, "GET", "/models/list", params={"page": page}
        )
        if err:
          return f"preset_models: error: {err}"
        items = (data or {}).get("items") or []
        if not items:
          break
        for item in items:
          if not self._preset_selected(item):
            continue
          model_id = item.get("id")
          if not item.get("write_access"):
            skipped.append(str(model_id))
            continue
          line, err = await self._write_preset(session, model_id, add, remove)
          if err:
            errors.append(str(err))
          elif ": changed" in line:
            updated += 1
        page += 1
    text = f"preset_models: added to {updated}"
    if skipped:
      text += f". skipped, no write access: {', '.join(skipped)}"
    if errors:
      text += f". failed: {errors[0]}"
    return text

  async def _attach_knowledge_to_presets(self, knowledge_ids, __request__) -> str:
    """Add knowledge bases to the knowledge list of the selected presets."""
    return await self._attach_to_presets(
      {"knowledge": list(knowledge_ids)}, __request__
    )

  async def _read(
    self,
    action: str,
    detail: str,
    fn: Callable,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int | None = None,
    max_chars: int = 10000,
    identifiers: dict | None = None,
  ):
    """Apply the read permission gate and bound text output for a requested range."""
    if self._permissions(__user__) == "Always ask" and not await self._ask(
      action, detail, __event_call__, __user__
    ):
      return json.dumps(
        {
          "result": {
            "denied": True,
            "action": action,
            "reason": "You did not confirm, so I read nothing.",
          }
        }
      )
    value = await fn()
    if offset is not None and isinstance(value, str) and not value.startswith("Error:"):
      page = self._page({**(identifiers or {}), "content": value}, offset, max_chars)
      return json.dumps(page, indent=2, ensure_ascii=False)
    return value

  # ---------------------------------------------------------------- the files

  async def list_files(
    self,
    page: int = 1,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List the files of the Open WebUI file library, with their ids and sizes.

    :param offset: row offset within this page
    :param max_results: maximum rows, subject to the output byte limit
    :param page: API page, from 1
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      current = max(1, int(page))
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", "/files/", params={"page": current, "content": "false"})
      if err:
        return err
      items = (data or {}).get("items") or []
      rows = [{key: item[key] for key in ("id", "filename") if key in item} for item in items]
      for row, item in zip(rows, items):
        row["size"] = (item.get("meta") or {}).get("size") or 0
      total = (data or {}).get("total")
      more = current * 50 < total if isinstance(total, int) else len(items) == 50
      return self._list_page(rows, offset, max_results, page=current, more=more, total_count=total)

    return await self._read("read list_files", str(page), run, __user__, __event_call__)

  async def search_files(
    self,
    filename: str,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Search the file library by name. Wildcards such as *.md work.

    :param filename: the filename or part of it to search for
    :param offset: row offset
    :param max_results: maximum rows, subject to the output byte limit
    """

    async def run(
      filename=filename,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      name = (filename or "").strip()
      if not name:
        return "Error: filename is empty."
      start = max(0, int(offset))
      size = min(100, max(1, int(max_results)))
      async with await self._open_session(__request__) as s:
        data, err = await self._request(
          s, "GET", "/files/search", expected=(200, 404),
          params={"filename": name, "content": "false", "skip": start, "limit": size}
        )
      if err:
        return err
      items = data if isinstance(data, list) else (data or {}).get("items") or []
      rows = [{key: item[key] for key in ("id", "filename") if key in item} for item in items]
      return self._list_page(rows, 0, size, base_offset=start, more=len(items) == size, total_count=None)

    return await self._read(
      "read search_files",
      f"Filename: {filename}",
      run,
      __user__,
      __event_call__,
    )

  async def read_file_content(
    self,
    file_id: str,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_chars: int = 10000,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Read a character range from the stored text of one library file.

    :param file_id: the id of the Open WebUI file to read
    :param offset: character offset in the stored text
    :param max_chars: maximum characters, subject to the output byte limit
    """

    async def run(
      file_id=file_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as s:
        data, err = await self._request(s, "GET", f"/files/{file_id}/data/content")
      if err:
        return err
      content = (data or {}).get("content")
      if content is None:
        return "Error: the file has no stored text content."
      page = self._page({"file_id": file_id, "content": str(content)}, offset, max_chars)
      return json.dumps(page, indent=2, ensure_ascii=False)

    return await self._read(
      "read read_file_content",
      f"File: {file_id}",
      run,
      __user__,
      __event_call__,
    )

  async def upload_file(
    self,
    filename: str,
    content: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create one text file in the library. A new item runs free.

    :param filename: the name of the new file, with its extension
    :param content: the full content of the new file
    """

    refusal = await self._guard(
      "upload a file",
      str(filename),
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    name = filename or ""
    if "\r" in name or "\n" in name:
      return "Error: filename must not contain line breaks."
    name = name.strip() or "untitled.txt"
    body = aiohttp.FormData(quote_fields=False)
    body.add_field("file", content.encode("utf-8"), filename=name, content_type="text/markdown")
    async with await self._open_session(__request__) as s:
      data, err = await self._request(
        s,
        "POST",
        "/files/",
        data=body,
        expected=(200, 201),
      )
    if err:
      return err
    return f"uploaded {name} (id={(data or {}).get('id')})"

  async def rename_file(
    self,
    file_id: str,
    name: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Rename one file. The rename is a mutation, so it passes the gate.

    :param file_id: the id of the file to rename
    :param name: the new filename, with its extension
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "rename a file", f"{file_id} -> {name}", __user__, __event_call__
    )
    if refusal:
      return refusal
    async with await self._open_session(__request__) as s:
      _, err = await self._request(
        s, "POST", f"/files/{file_id}/rename", json={"filename": name}
      )
    return err or f"renamed file {file_id} to {name}"

  async def delete_file(
    self,
    file_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete one file of the library. The delete passes the gate.

    :param file_id: the id of the file to delete
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard("delete a file", str(file_id), __user__, __event_call__)
    if refusal:
      return refusal
    async with await self._open_session(__request__) as s:
      _, err = await self._request(s, "DELETE", f"/files/{file_id}")
    return err or f"deleted file {file_id}"

  # ---------------------------------------------------------------- the skills

  @staticmethod
  def _format_skill_files(data) -> str:
    """Format one versioned skill-file manifest for model-readable output."""
    data = data or {}
    lines = [f"version_id={data.get('version_id') or ''}", "files:"]
    files = data.get("files") or []
    lines.extend(
      f"- {item.get('path')} (size={item.get('size', 0)}, "
      f"encoding={item.get('encoding') or 'text'})"
      for item in files
    )
    if not files:
      lines.append("- none")
    return "\n".join(lines)

  async def list_skills(
    self,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    page: int = 1,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List the skills of the workspace, with their ids and their state.

    :param offset: row offset within this page
    :param max_results: maximum rows, subject to the output byte limit
    :param page: API page, from 1
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      current = max(1, int(page))
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", "/skills/list", params={"page": current})
      if err:
        return err
      items = (data or {}).get("items") or []
      rows = [{key: item[key] for key in (
        "id", "name", "is_active", "version_id", "write_access",
        "access_control", "access_grants", "user_id"
      ) if key in item} for item in items]
      total = (data or {}).get("total")
      more = current * 30 < total if isinstance(total, int) else len(items) == 30
      return self._list_page(rows, offset, max_results, page=current, more=more, total_count=total)

    return await self._read("read list_skills", str(page), run, __user__, __event_call__)

  async def show_skill(
    self,
    skill_id: str,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_chars: int = 10000,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Show one skill: its name, its state and a source text range.

    :param skill_id: the id of the skill to read
    :param offset: character offset in the returned text
    :param max_chars: maximum characters, subject to the output byte limit
    """

    async def run(
      skill_id=skill_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as s:
        data, err = await self._request(s, "GET", f"/skills/id/{skill_id}")
        if err:
          return err
        if not data:
          return f"Error: skill not found: {skill_id}"
        files, err = await self._request(
          s,
          "GET",
          f"/skills/id/{skill_id}/files",
          params={"version_id": data.get("version_id")},
        )
      if err:
        return err
      return (
        f"name={data.get('name')} (id={data.get('id')}, "
        f"active={data.get('is_active')}, description={data.get('description') or ''})\n"
        f"{self._format_skill_files(files)}\n"
        f"\n{data.get('content') or ''}"
      )

    return await self._read(
      "read show_skill",
      f"Skill: {skill_id}",
      run,
      __user__,
      __event_call__,
      offset=offset, max_chars=max_chars, identifiers={"skill_id": skill_id},
    )

  async def list_skill_files(
    self,
    skill_id: str,
    version_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List a page of files in one skill snapshot, with its size and encoding.

    :param skill_id: the id of the skill to inspect
    :param version_id: a version id from show_skill, or empty for the current version
    :param offset: row offset
    :param max_results: maximum rows, subject to the output byte limit
    """

    async def run(
      skill_id=skill_id,
      version_id=version_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      params = {"version_id": version_id} if version_id else None
      async with await self._open_session(__request__) as s:
        data, err = await self._request(
          s, "GET", f"/skills/id/{skill_id}/files", params=params
        )
      if err:
        return err
      data = data or {}
      rows = [{key: item[key] for key in ("path", "size", "encoding") if key in item}
              for item in data.get("files") or []]
      return self._list_page(rows, offset, max_results, skill_id=skill_id, version_id=data.get("version_id"))

    return await self._read(
      "read list_skill_files",
      str(skill_id),
      run,
      __user__,
      __event_call__,
    )

  async def read_skill_file(
    self,
    skill_id: str,
    path: str,
    version_id: str = "",
    offset: int = 0,
    max_chars: int = 10000,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Read a UTF-8 file from one skill snapshot, with character-offset paging.

    Binary supporting files return their metadata instead of decoded bytes.

    :param skill_id: the id of the skill to inspect
    :param path: the relative file path, for example references/api.md
    :param version_id: a version id from show_skill, or empty for the current version
    :param offset: character offset to start reading from
    :param max_chars: maximum characters to return, up to 100000
    """

    async def run(
      skill_id=skill_id,
      path=path,
      version_id=version_id,
      offset=offset,
      max_chars=max_chars,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      path = (path or "").strip()
      if not path:
        return "Error: skill file path is empty."
      params = {"version_id": version_id} if version_id else None
      async with await self._open_session(__request__) as s:
        manifest, err = await self._request(
          s, "GET", f"/skills/id/{skill_id}/files", params=params
        )
        if err:
          return err
        snapshot = (manifest or {}).get("version_id") or version_id
        item = next(
          (
            item
            for item in (manifest or {}).get("files") or []
            if item.get("path") == path
          ),
          None,
        )
        if not item:
          return f"Error: skill file not found: {path}"
        if item.get("encoding"):
          return (
            f"path={path}\nversion_id={snapshot}\nsize={item.get('size', 0)}\n"
            f"encoding={item.get('encoding')}\ncontent=<binary file>\n"
            "Open the file in the Open WebUI skill editor."
          )
        data, err = await self._request(
          s,
          "GET",
          f"/skills/id/{skill_id}/files/content",
          params={"path": path, "version_id": snapshot},
          as_text=True,
        )
      if err:
        return err
      if isinstance(data, dict) and "_text" in data:
        content = str(data["_text"])
      elif isinstance(data, str):
        content = data
      else:
        content = json.dumps(data, ensure_ascii=False, indent=2)
      payload = {"content": content, "path": path, "version_id": snapshot}
      page = self._page(payload, offset or 0, min(int(max_chars or 10000), 100000))
      next_offset = page["next_offset"]
      return (
        f"path={path}\nversion_id={snapshot}\noffset={page['offset']}\n"
        f"next_offset={next_offset if next_offset is not None else 'none'}\n"
        f"content={page['content']}"
      )

    return await self._read(
      "read read_skill_file",
      f"{skill_id}:{path}",
      run,
      __user__,
      __event_call__,
    )

  async def create_skill(
    self,
    name: str,
    content: str,
    description: str = "",
    skill_id: str = "",
    files: list[SkillFileInput] | None = None,
    commit_message: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create one skill. A new item runs free, and it joins the preset models.

    :param name: the name of the new skill
    :param content: the complete UTF-8 SKILL.md text
    :param description: a short description, or empty
    :param skill_id: an id to use, or empty to let Open WebUI build one
    :param files: optional UTF-8 supporting files, each with path and content
    :param commit_message: an optional description of this version
    """

    refusal = await self._guard(
      "create a skill",
      str(name),
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    identifier = (skill_id or "").strip() or re.sub(
      r"[^a-z0-9]+", "-", (name or "").strip().lower()
    ).strip("-")
    if not identifier:
      return "Error: name is empty, so no skill id can be built."
    supporting = []
    for item in files or []:
      if isinstance(item, BaseModel):
        item = item.model_dump(exclude_none=True)
      if not isinstance(item, dict):
        return "Error: each supporting file must have path and content."
      path = str(item.get("path") or "").strip()
      if not path or "content" not in item:
        return "Error: each supporting file must have path and content."
      if path == "SKILL.md":
        return "Error: pass SKILL.md in content, not files."
      if item.get("encoding"):
        return "Error: create_skill supporting files must be UTF-8 text."
      if not isinstance(item.get("content"), str):
        return f"Error: supporting file content must be text: {path}"
      supporting.append({"path": path, "content": item["content"]})
    skill_files = [{"path": "SKILL.md", "content": content}, *supporting]
    body = {
      "id": identifier,
      "name": name,
      "description": description,
      "content": content,
      "files": skill_files,
      "commit_message": commit_message or None,
      "meta": {},
      "is_active": True,
    }
    async with await self._open_session(__request__) as s:
      data, err = await self._request(
        s, "POST", "/skills/create", json=body, expected=(200, 201)
      )
      if err:
        return err
      identifier = (data or {}).get("id") or identifier
      attach = await self._attach_to_presets({"skillIds": [identifier]}, __request__)
    return f"created skill {name} (id={identifier})\nfiles={len(skill_files)}\n{attach}"

  async def install_skill(
    self,
    url: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Import one or more skills from a trusted URL through Open WebUI v0.12.

    A GitHub repository, folder or SKILL.md URL can carry complete skill
    packages. Raw Markdown, JSON and ZIP URLs also work. The importer preserves
    binary files. Open WebUI restricts URL loading to administrators.

    :param url: the HTTPS URL of a skill file, package, folder or repository
    """

    refusal = await self._guard(
      "install a skill",
      str(url),
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    target = (url or "").strip()
    parsed = urlsplit(target)
    if parsed.scheme != "https" or not parsed.hostname:
      return "Error: the URL must start with https:// and include a host."
    if parsed.username or parsed.password:
      return "Error: the URL must not contain credentials."
    host = parsed.hostname.lower()
    request_url = parsed._replace(fragment="").geturl()
    source_url = parsed._replace(query="", fragment="").geturl()
    trusted = [
      domain.strip().lower() for domain in _split_ids(self.valves.TRUSTED_DOMAINS)
    ]
    if not any(host == domain or host.endswith("." + domain) for domain in trusted):
      return f"Error: {host} is not a trusted domain."

    timeout = aiohttp.ClientTimeout(
      total=max(1.0, float(self.valves.INSTALL_FETCH_TIMEOUT or 60))
    )
    imported = []
    lines = []
    async with await self._open_session(__request__) as session:
      packages, err = await self._request(
        session,
        "POST",
        "/skills/load/url",
        json={"url": request_url},
        expected=(200,),
        timeout=timeout,
      )
      if err:
        return err
      if not isinstance(packages, list) or not packages:
        return "Error: the URL contained no importable skills."
      for package in packages:
        if not isinstance(package, dict):
          lines.append("failed import: Open WebUI returned an invalid skill package")
          continue
        files = package.get("files")
        if not isinstance(files, list) or not files:
          content = package.get("content")
          files = (
            [{"path": "SKILL.md", "content": content}]
            if isinstance(content, str)
            else []
          )
        if not files or any(not isinstance(item, dict) for item in files):
          lines.append("failed import: Open WebUI returned invalid skill files")
          continue
        identifier = str(package.get("id") or "")
        name = str(package.get("name") or identifier or "Imported skill")
        root = next(
          (item.get("content") for item in files if item.get("path") == "SKILL.md"),
          "",
        )
        body = {
          "id": identifier,
          "name": name,
          "description": package.get("description") or "",
          "content": root,
          "files": files,
          "commit_message": f"Imported from {source_url}",
          "meta": package.get("meta") or {},
          "is_active": package.get("is_active", True),
          "access_grants": [],
        }
        created, create_err = await self._request(
          session,
          "POST",
          "/skills/create",
          json=body,
          expected=(200, 201),
        )
        if create_err:
          lines.append(f"failed {name} (id={identifier}): {create_err}")
          continue
        created_id = (created or {}).get("id") or identifier
        imported.append(created_id)
        lines.append(f"imported {name} (id={created_id}, files={len(files)})")
    if imported:
      lines.append(await self._attach_to_presets({"skillIds": imported}, __request__))
    return "\n".join(lines)

  async def update_skill(
    self,
    skill_id: str,
    name: str,
    content: str,
    description: str = "",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Replace one skill. The overwrite passes the gate.

    :param skill_id: the id of the skill to change
    :param name: the new name
    :param content: the new full skill text
    :param description: the new description, or empty
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "overwrite a skill", f"{name} (id={skill_id})", __user__, __event_call__
    )
    if refusal:
      return refusal
    body = {
      "id": skill_id,
      "name": name,
      "description": description,
      "content": content,
      "meta": {},
      "is_active": True,
    }
    async with await self._open_session(__request__) as s:
      _, err = await self._request(
        s, "POST", f"/skills/id/{skill_id}/update", json=body
      )
    return err or f"updated skill {skill_id}"

  async def update_skill_files(
    self,
    skill_id: str,
    operations: list[SkillFileOperationInput],
    expected_version_id: str = "",
    commit_message: str = "",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Apply UTF-8 file operations as one new version of an existing skill.

    Operations are {op: put, path, content}, {op: move, path, destination},
    or {op: delete, path}. Untouched files stay in the snapshot. Pass the
    version from show_skill for strict optimistic concurrency, or leave it
    empty to use the version read immediately before this update.

    :param skill_id: the id of the skill to update
    :param operations: put, move or delete operations for relative skill paths
    :param expected_version_id: the version expected by the caller, or empty to read it now
    :param commit_message: an optional description of this version
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "update skill files", str(skill_id), __user__, __event_call__
    )
    if refusal:
      return refusal
    if not isinstance(operations, list) or not operations:
      return "Error: provide at least one skill file operation."
    operations = [
      operation.model_dump(exclude_none=True)
      if isinstance(operation, BaseModel)
      else operation
      for operation in operations
    ]
    for operation in operations:
      if not isinstance(operation, dict):
        return "Error: each skill file operation must be an object."
      if operation.get("encoding"):
        return "Error: update_skill_files writes UTF-8 text only."
      if operation.get("op") not in {"put", "move", "delete"}:
        return "Error: skill file op must be put, move or delete."
      if not str(operation.get("path") or "").strip():
        return "Error: each skill file operation needs a path."
      if operation.get("op") == "put" and not isinstance(operation.get("content"), str):
        return "Error: a put operation needs text content."
      if (
        operation.get("op") == "move"
        and not str(operation.get("destination") or "").strip()
      ):
        return "Error: a move operation needs a destination."

    async with await self._open_session(__request__) as session:
      current, err = await self._request(session, "GET", f"/skills/id/{skill_id}")
      if err:
        return err
      if not current:
        return f"Error: skill not found: {skill_id}"
      expected = expected_version_id or current.get("version_id")
      if not expected:
        return "Error: Open WebUI returned no current skill version."
      body = {
        "id": skill_id,
        "name": current.get("name") or skill_id,
        "expected_version_id": expected,
        "operations": operations,
        "commit_message": commit_message or None,
      }
      data, err = await self._request(
        session,
        "POST",
        f"/skills/id/{skill_id}/update",
        json=body,
        expected=(200, 201),
      )
    if err:
      return err
    return (
      f"updated skill files {skill_id} "
      f"(version_id={(data or {}).get('version_id') or 'unknown'})"
    )

  async def toggle_skill(
    self,
    skill_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Switch one skill on or off. The toggle passes the gate.

    :param skill_id: the id of the skill to turn on or off
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "toggle a skill", str(skill_id), __user__, __event_call__
    )
    if refusal:
      return refusal
    async with await self._open_session(__request__) as s:
      data, err = await self._request(s, "POST", f"/skills/id/{skill_id}/toggle")
    if err:
      return err
    return f"toggled skill {skill_id} (active={(data or {}).get('is_active')})"

  async def delete_skill(
    self,
    skill_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete one skill. The delete passes the gate.

    :param skill_id: the id of the skill to delete
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "delete a skill", str(skill_id), __user__, __event_call__
    )
    if refusal:
      return refusal
    async with await self._open_session(__request__) as s:
      _, err = await self._request(s, "DELETE", f"/skills/id/{skill_id}/delete")
    return err or f"deleted skill {skill_id}"

  # ---------------------------------------------------------------- the tools

  async def list_tools(
    self,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List the Workspace Tools, with their ids.

    :param offset: row offset within this page
    :param max_results: maximum rows, subject to the output byte limit
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", "/tools/")
      if err:
        return err
      items = data if isinstance(data, list) else (data or {}).get("items") or []
      rows = [{key: item[key] for key in (
        "id", "name", "write_access", "access_control", "access_grants", "user_id"
      ) if key in item} for item in items]
      return self._list_page(rows, offset, max_results)

    return await self._read("read list_tools", str(offset), run, __user__, __event_call__)

  async def show_tool(
    self,
    tool_id: str,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_chars: int = 10000,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Show one Workspace Tool: its name and a source text range.

    :param tool_id: the id of the tool to read
    :param offset: character offset in the returned text
    :param max_chars: maximum characters, subject to the output byte limit
    """

    async def run(
      tool_id=tool_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as s:
        data, err = await self._request(s, "GET", f"/tools/id/{tool_id}")
      if err:
        return err
      if not data:
        return f"Error: tool not found: {tool_id}"
      meta = data.get("meta") or {}
      return (
        f"name={data.get('name')} (id={data.get('id')}, "
        f"description={meta.get('description') or ''})\n"
        f"\n{data.get('content') or ''}"
      )

    return await self._read(
      "read show_tool",
      f"Tool: {tool_id}",
      run,
      __user__,
      __event_call__,
      offset=offset, max_chars=max_chars, identifiers={"tool_id": tool_id},
    )

  async def create_tool(
    self,
    name: str,
    content: str,
    description: str = "",
    tool_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create one Workspace Tool. A new item runs free and joins the presets.

    :param name: the name of the new tool
    :param content: the full Python source of the tool
    :param description: a short description, or empty
    :param tool_id: an id to use, or empty to let Open WebUI build one
    """

    refusal = await self._guard(
      "create a tool",
      str(name),
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    identifier = (tool_id or "").strip() or re.sub(
      r"[^a-z0-9]+", "_", (name or "").strip().lower()
    ).strip("_")
    if not identifier:
      return "Error: name is empty, so no tool id can be built."
    body = {
      "id": identifier,
      "name": name,
      "content": content,
      "meta": {"description": description, "manifest": {}},
    }
    async with await self._open_session(__request__) as s:
      _, err = await self._request(
        s, "POST", "/tools/create", json=body, expected=(200, 201)
      )
      if err:
        return err
      attach = await self._attach_to_presets({"toolIds": [identifier]}, __request__)
    return f"created tool {name} (id={identifier})\n{attach}"

  async def update_tool(
    self,
    tool_id: str,
    name: str,
    content: str,
    description: str = "",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Replace one Workspace Tool. The overwrite passes the gate.

    :param tool_id: the id of the tool to change
    :param name: the new name
    :param content: the new full Python source
    :param description: the new description, or empty
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "overwrite a tool", f"{name} (id={tool_id})", __user__, __event_call__
    )
    if refusal:
      return refusal
    body = {
      "id": tool_id,
      "name": name,
      "content": content,
      "meta": {"description": description, "manifest": {}},
    }
    async with await self._open_session(__request__) as s:
      _, err = await self._request(s, "POST", f"/tools/id/{tool_id}/update", json=body)
    return err or f"updated tool {tool_id}"

  async def toggle_tool(
    self,
    tool_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Toggle one Workspace Tool on the preset models.

    Open WebUI v0.11.4 has no per-tool on and off switch, so this toggles the
    tool id in the toolIds list of each preset the caller can write: a tool in
    every preset leaves them, and a tool in no preset joins them.

    :param tool_id: the id of the tool to turn on or off
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard("toggle a tool", str(tool_id), __user__, __event_call__)
    if refusal:
      return refusal
    present = 0
    total = 0
    async with await self._open_session(__request__) as s:
      page = 1
      while page <= 100:
        data, err = await self._request(s, "GET", "/models/list", params={"page": page})
        if err:
          return err
        items = (data or {}).get("items") or []
        if not items:
          break
        for item in items:
          if not self._preset_selected(item) or not item.get("write_access"):
            continue
          total += 1
          if str(tool_id) in [
            str(x) for x in (item.get("meta") or {}).get("toolIds") or []
          ]:
            present += 1
        page += 1
    if not total:
      return "Error: no writable preset serves the tool toggle."
    if present:
      return await self._attach_to_presets(
        {}, __request__, remove={"toolIds": [str(tool_id)]}
      )
    return await self._attach_to_presets({"toolIds": [str(tool_id)]}, __request__)

  async def delete_tool(
    self,
    tool_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete one Workspace Tool. The delete passes the gate.

    :param tool_id: the id of the tool to delete
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard("delete a tool", str(tool_id), __user__, __event_call__)
    if refusal:
      return refusal
    async with await self._open_session(__request__) as s:
      _, err = await self._request(s, "DELETE", f"/tools/id/{tool_id}/delete")
    return err or f"deleted tool {tool_id}"

  # ---------------------------------------------------------------- the functions

  async def list_functions(
    self,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List the Functions, with their ids and their types.

    :param offset: row offset within this page
    :param max_results: maximum rows, subject to the output byte limit
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", "/functions/")
      if err:
        return err
      items = data if isinstance(data, list) else (data or {}).get("items") or []
      rows = [{key: item[key] for key in (
        "id", "name", "type", "is_active", "is_global"
      ) if key in item} for item in items]
      return self._list_page(rows, offset, max_results)

    return await self._read("read list_functions", str(offset), run, __user__, __event_call__)

  async def show_function(
    self,
    function_id: str,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_chars: int = 10000,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Show one Function: its name, its state and a source text range.

    :param function_id: the id of the function to read
    :param offset: character offset in the returned text
    :param max_chars: maximum characters, subject to the output byte limit
    """

    async def run(
      function_id=function_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as s:
        data, err = await self._request(s, "GET", f"/functions/id/{function_id}")
      if err:
        return err
      if not data:
        return f"Error: function not found: {function_id}"
      meta = data.get("meta") or {}
      return (
        f"name={data.get('name')} (id={data.get('id')}, type={data.get('type')}, "
        f"active={data.get('is_active')}, global={data.get('is_global')}, "
        f"description={meta.get('description') or ''})\n"
        f"\n{data.get('content') or ''}"
      )

    return await self._read(
      "read show_function",
      f"Function: {function_id}",
      run,
      __user__,
      __event_call__,
      offset=offset, max_chars=max_chars, identifiers={"function_id": function_id},
    )

  async def create_function(
    self,
    name: str,
    content: str,
    description: str = "",
    function_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create one Function. A new item runs free and joins the presets.

    :param name: the name of the new function
    :param content: the full Python source of the function
    :param description: a short description, or empty
    :param function_id: an id to use, or empty to let Open WebUI build one
    """

    refusal = await self._guard(
      "create a function",
      str(name),
      __user__,
      __event_call__,
      sensitive=False,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    identifier = (function_id or "").strip() or re.sub(
      r"[^a-z0-9]+", "_", (name or "").strip().lower()
    ).strip("_")
    if not identifier:
      return "Error: name is empty, so no function id can be built."
    body = {
      "id": identifier,
      "name": name,
      "content": content,
      "meta": {"description": description, "manifest": {}},
    }
    async with await self._open_session(__request__) as s:
      _, err = await self._request(
        s, "POST", "/functions/create", json=body, expected=(200, 201)
      )
      if err:
        return err
      attach = await self._attach_to_presets({"functionIds": [identifier]}, __request__)
    return f"created function {name} (id={identifier})\n{attach}"

  async def update_function(
    self,
    function_id: str,
    name: str,
    content: str,
    description: str = "",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Replace one Function. The overwrite passes the gate.

    :param function_id: the id of the function to change
    :param name: the new name
    :param content: the new full Python source
    :param description: the new description, or empty
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "overwrite a function", f"{name} (id={function_id})", __user__, __event_call__
    )
    if refusal:
      return refusal
    body = {
      "id": function_id,
      "name": name,
      "content": content,
      "meta": {"description": description, "manifest": {}},
    }
    async with await self._open_session(__request__) as s:
      _, err = await self._request(
        s, "POST", f"/functions/id/{function_id}/update", json=body
      )
    return err or f"updated function {function_id}"

  async def toggle_function(
    self,
    function_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Switch one Function on or off. The toggle passes the gate.

    :param function_id: the id of the function to turn on or off
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "toggle a function", str(function_id), __user__, __event_call__
    )
    if refusal:
      return refusal
    async with await self._open_session(__request__) as s:
      data, err = await self._request(s, "POST", f"/functions/id/{function_id}/toggle")
    if err:
      return err
    return (
      f"toggled function {function_id} "
      f"(active={(data or {}).get('is_active')}, global={(data or {}).get('is_global')})"
    )

  async def delete_function(
    self,
    function_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete one Function. The delete passes the gate.

    :param function_id: the id of the function to delete
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "delete a function", str(function_id), __user__, __event_call__
    )
    if refusal:
      return refusal
    async with await self._open_session(__request__) as s:
      _, err = await self._request(s, "DELETE", f"/functions/id/{function_id}/delete")
    return err or f"deleted function {function_id}"
