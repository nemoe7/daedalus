"""
title: Open WebUI Manager
author: nemo
description: Open WebUI knowledge, skills, files, tools, and functions. Read and write approval follows permission mode.
required_open_webui_version: 0.12.0
version: 1.0.15
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

NO_CONTEXT = "Error: Open WebUI request context is unavailable."


def _split_ids(value: Any) -> list[str]:
  """Split a comma- or newline-separated list into unique IDs."""
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
  """Return the identifier from an attachment dict or plain ID."""
  if isinstance(item, dict):
    return str(item.get("id") or item.get("collection_name") or "")
  return str(item)


def _merge_ids(current: Any, add: list[Any], remove: list[str]) -> list[Any]:
  """Merge IDs or knowledge references with exclusions and additions in existing order."""
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
  """Represent one UTF-8 supporting file for a skill."""

  path: str = Field(description="Relative path within the skill")
  content: str = Field(description="Complete UTF-8 text content")


class SkillFileOperationInput(BaseModel):
  """Represent one versioned skill-file mutation."""

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
      return self._output(
        value, method.__annotations__.get("return") in (str, "str"), arguments
      )
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
  """Manage Open WebUI workspaces under the configured permission mode."""

  class Valves(BaseModel):
    max_output_bytes: int = Field(
      default=32768,
      ge=1024,
      description="Maximum UTF-8 bytes in one tool result.",
    )
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
      await emitter(
        {"type": "status", "data": {"description": operation, "done": done}}
      )
    except Exception:
      logger.warning("Could not send manager status for %s", operation)

  @staticmethod
  def _output_size(value: Any) -> int:
    text = (
      value
      if isinstance(value, str)
      else json.dumps(value, indent=2, ensure_ascii=False)
    )
    return len(text.encode("utf-8"))

  def _output(
    self, value: Any, as_text: bool = False, arguments: dict | None = None
  ) -> Any:
    limit = self.valves.max_output_bytes
    size = self._output_size(value)
    if size <= limit:
      return value
    result = {
      "error": "tool_output_too_large",
      "truncated": True,
      "output_bytes": size,
      "max_output_bytes": limit,
      "message": (
        "Use a smaller page or text range. Do not repeat a write to recover output."
      ),
      "identifiers": {},
    }
    pending = [arguments or {}]
    if isinstance(value, (dict, list)):
      pending.append(value)
    keys = {
      "id",
      "sha",
      "node_id",
      "html_url",
      "url",
      "file_id",
      "thread_id",
      "skill_id",
      "version_id",
      "tool_id",
      "function_id",
      "knowledge_id",
      "repository_full_name",
      "repo_full_name",
      "commit_sha",
      "blob_sha",
    }
    for _ in range(100):
      if not pending:
        break
      row = pending.pop()
      if isinstance(row, list):
        pending.extend(item for item in row[:100] if isinstance(item, (dict, list)))
        continue
      for key, item in row.items():
        if (
          key in keys
          and key not in result["identifiers"]
          and isinstance(item, (str, int))
        ):
          result["identifiers"][key] = item
          if self._output_size(result) > limit:
            del result["identifiers"][key]
        elif isinstance(item, (dict, list)):
          pending.append(item)
    return json.dumps(result, indent=2, ensure_ascii=False) if as_text else result

  def _page(
    self, payload: dict, offset: int, max_chars: int, field: str = "content"
  ) -> dict:
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
    self,
    items: list[dict],
    offset: int,
    max_results: int,
    *,
    page: int | None = None,
    more: bool = False,
    base_offset: int = 0,
    **metadata,
  ) -> str:
    start = min(max(0, int(offset)), len(items))
    upper = min(max(1, int(max_results)), 100, len(items) - start)

    def result(count):
      end = start + count
      within = end < len(items)
      value = {
        "total_count": len(items),
        **metadata,
        "items": items[start:end],
        "count": count,
        "offset": base_offset + start,
        "next_offset": (
          base_offset + end
          if within or (more and page is None)
          else 0
          if more
          else None
        ),
      }
      if page is not None:
        value.update(
          page=page, next_page=page if within else page + 1 if more else None
        )
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

  @staticmethod
  def _user_valve(__user__: dict | None, name: str) -> Any:
    """Read one user valve from an object or dict."""
    valves = (__user__ or {}).get("valves") or {}
    value = getattr(valves, name, None)
    if value is None and isinstance(valves, dict):
      value = valves.get(name)
    return value

  def _permissions(self, __user__: dict | None = None) -> str:
    """Resolve the permission mode, defaulting unknown values to `Always ask`."""
    user_mode = self._user_valve(__user__, "mode")
    mode = (
      self.valves.permissions
      if user_mode is None or user_mode == "default"
      else user_mode
    )
    return (
      mode
      if isinstance(mode, str) and mode in ("Always ask", "Allow reads", "Always allow")
      else "Always ask"
    )

  def _wait_seconds(self, __user__: dict | None = None) -> int:
    """Return user timeout, tool timeout, or 60 seconds, with a one-second floor."""
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
    """Request user confirmation within timeout and return False when denied."""
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
    """Return a JSON refusal when active permissions require confirmation and approval fails."""
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
    """Return the Open WebUI API base URL without a trailing slash."""
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
    """Send an Open WebUI API request and return `(data, error)`."""
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
    """Read all pages from one knowledge directory."""
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
    """Report whether any files exist in a knowledge base."""
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

  async def _knowledge_reference(self, session, knowledge_id):
    """Build a stored knowledge reference from one knowledge base ID."""
    data, err = await self._request(session, "GET", f"/knowledge/{knowledge_id}")
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
    """Build an updated preset body and list modified attachment fields."""
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

  async def _write_preset(self, session, model_id, add, remove):
    """Read one preset, merge attachment lists, and post changes back."""
    record, err = await self._request(
      session, "GET", "/models/model", params={"id": model_id}
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
      reference, err = await self._knowledge_reference(session, knowledge_id)
      if err:
        return None, err
      resolved["knowledge"].append(reference)
    payload, changed = self._preset_payload(record, resolved, remove)
    if not changed:
      name = record.get("name") or model_id
      return f"- {name} (id={model_id}): no change", None
    _, err = await self._request(
      session,
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

  def _preset_filter(self) -> list[str]:
    """Parse `PRESET_MODELS` into preset IDs or names."""
    return _split_ids(self.valves.PRESET_MODELS)

  def _preset_selected(self, item) -> bool:
    """Match a preset ID or name against `PRESET_MODELS`, or accept all when empty."""
    wanted = self._preset_filter()
    if not wanted:
      return True
    return str(item.get("id")) in wanted or str(item.get("name")) in wanted

  async def _attach_to_presets(self, add: dict, __request__, remove=None) -> str:
    """Update attachments on selected writable presets and summarize outcomes."""
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
    """Attach knowledge base IDs to selected writable presets."""
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
    """Apply read permissions and bound text output for a requested character range."""
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
    if (
      offset is not None
      and isinstance(value, str)
      and not value.startswith("Error:")
      and "not found:" not in value.lower()
      and not value.startswith("Multiple matching")
    ):
      page = self._page({**(identifiers or {}), "content": value}, offset, max_chars)
      return json.dumps(page, indent=2, ensure_ascii=False)
    return value

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
    List accessible Open WebUI knowledge bases with IDs and write access.

    :param page: API page, starting at 1
    :param offset: row offset within this page
    :param max_results: maximum rows within output byte limits
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      current = max(1, int(page))
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session, "GET", "/knowledge/", params={"page": current}
        )
      if err:
        return err
      items = (data or {}).get("items") or []
      rows = [
        {
          key: item[key]
          for key in (
            "id",
            "name",
            "write_access",
            "access_control",
            "access_grants",
            "user_id",
          )
          if key in item
        }
        for item in items
      ]
      total = (data or {}).get("total")
      more = current * 30 < total if isinstance(total, int) else len(items) == 30
      return self._list_page(
        rows, offset, max_results, page=current, more=more, total_count=total
      )

    return await self._read(
      "read list_knowledge_bases", str(page), run, __user__, __event_call__
    )

  async def save_knowledge_base(
    self,
    name: str = "",
    description: str = "",
    knowledge_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create a knowledge base when `knowledge_id` is empty, or update an existing base.

    :param name: knowledge base name for creation or replacement
    :param description: knowledge base description
    :param knowledge_id: existing knowledge base ID to update, or empty to create one
    """
    kid = (knowledge_id or "").strip()
    if not kid:
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
      clean_name = (name or "").strip()
      if not clean_name:
        return "Error: knowledge base name is empty."
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session,
          "POST",
          "/knowledge/create",
          expected=(200, 201),
          json={
            "name": clean_name,
            "description": (description or "").strip(),
            "access_grants": None,
          },
          timeout=self._short_timeout,
        )
      if err:
        return err
      attach = await self._attach_knowledge_to_presets([data.get("id")], __request__)
      return (
        f"Knowledge base created successfully.\nname={data.get('name', clean_name)}\n"
        f"knowledge_id={data.get('id')}\n"
        f"description={data.get('description', description)}\n{attach}"
      )

    refusal = await self._guard(
      "overwrite a knowledge base",
      f"{kid} -> {name}",
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as session:
      current, err = await self._request(session, "GET", f"/knowledge/{kid}")
      if err:
        return err
      payload = {
        "name": (name or "").strip() or current.get("name", ""),
        "description": (description or "").strip() or current.get("description", ""),
        "access_grants": current.get("access_grants"),
      }
      data, err = await self._request(
        session,
        "POST",
        f"/knowledge/{kid}/update",
        expected=(200, 201),
        json=payload,
        timeout=self._short_timeout,
      )
    if err:
      return err
    return (
      f"Knowledge base updated successfully.\nknowledge_id={kid}\n"
      f"name={data.get('name', payload['name'])}\n"
      f"description={data.get('description', payload['description'])}"
    )

  async def delete_knowledge_base(
    self,
    knowledge_id: str = Field(
      ..., description="ID of the Open WebUI knowledge base to delete."
    ),
    confirm: bool = Field(
      ..., description="Set true only when the user requested permanent deletion."
    ),
    allow_nonempty: bool = False,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete a knowledge base when `confirm` is true, requiring `allow_nonempty` when files exist.

    :param knowledge_id: knowledge base ID to delete
    :param confirm: true only when the user requested permanent deletion
    :param allow_nonempty: true to delete a base that still contains files
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
    async with await self._open_session(__request__) as session:
      meta, err = await self._request(session, "GET", f"/knowledge/{knowledge_id}")
      if err:
        return err
      has_content, err = await self._knowledge_has_content(session, knowledge_id)
      if err:
        return err
      if has_content and not allow_nonempty:
        return (
          "Deletion refused: knowledge base contains files. "
          "Use allow_nonempty=true only when explicitly intended."
        )
      _, err = await self._request(
        session,
        "DELETE",
        f"/knowledge/{knowledge_id}/delete",
        expected=(200, 204),
      )
    return (
      err
      or f"Knowledge base permanently deleted successfully.\n"
      f"knowledge_id={knowledge_id}\nname={meta.get('name', 'Unnamed')}"
    )

  async def list_knowledge_files(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    directory_id: str = "",
    path: str = "",
    tree: bool = False,
    max_depth: int = 20,
    max_nodes: int = 1000,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    page: int = 1,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List one knowledge directory page by ID or path, or render a bounded tree when `tree` is true.

    :param knowledge_id: Open WebUI knowledge base ID
    :param directory_id: directory ID to list, or empty for root
    :param path: optional directory path when `directory_id` is empty
    :param tree: true to return a recursive directory and file tree
    :param max_depth: maximum recursion depth when `tree` is true
    :param max_nodes: maximum tree nodes when `tree` is true
    :param page: API page, starting at 1
    :param offset: row offset within this API page
    :param max_results: maximum rows within output byte limits
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      if tree:
        depth_cap = max(1, min(max_depth, 50))
        node_cap = max(1, min(max_nodes, 5000))
        lines = []
        count = 0
        visited = set()
        async with await self._open_session(__request__) as session:
          target_dir = directory_id
          if path and not target_dir:
            parts, err = self._path_parts(path)
            if err:
              return err
            target_dir, err = await self._resolve_directory(
              session, knowledge_id, parts
            )
            if err:
              return err
          meta, err = await self._request(session, "GET", f"/knowledge/{knowledge_id}")
          if err:
            return err
          lines.append(
            f"{meta.get('name', 'Knowledge Base')} (knowledge_id={knowledge_id})"
          )

          async def walk(directory, depth):
            nonlocal count
            if count >= node_cap or depth > depth_cap:
              return
            key = directory or "ROOT"
            if key in visited:
              return
            visited.add(key)
            listing, walk_err = await self._directory_listing(
              session, knowledge_id, directory
            )
            if walk_err:
              lines.append("  " * depth + walk_err)
              return
            for entry in listing["items"]:
              if count >= node_cap:
                return
              count += 1
              item = self._file_item(entry)
              lines.append(
                "  " * depth
                + f"📄 {item.get('filename', 'Unnamed')} (file_id={item.get('id')})"
              )
            for entry in listing["directories"]:
              if count >= node_cap:
                return
              count += 1
              lines.append(
                "  " * depth
                + f"📁 {entry.get('name', 'Unnamed')} (directory_id={entry.get('id')})"
              )
              await walk(entry.get("id"), depth + 1)

          await walk(target_dir, 1)
        if count >= node_cap:
          lines.append(f"\nTree truncated after {node_cap} nodes.")
        return "\n".join(lines)

      current = max(1, int(page))
      async with await self._open_session(__request__) as session:
        target_dir = directory_id
        if path and not target_dir:
          parts, err = self._path_parts(path)
          if err:
            return err
          target_dir, err = await self._resolve_directory(session, knowledge_id, parts)
          if err:
            return err
        data, err = await self._request(
          session,
          "GET",
          f"/knowledge/{knowledge_id}/files",
          params={"directory_id": target_dir, "page": current},
        )
      if err:
        return err
      data = data or {}
      rows = []
      if current == 1:
        rows.extend(
          {
            "kind": "directory",
            "directory_id": item.get("id"),
            "name": item.get("name"),
            "parent_id": item.get("parent_id"),
          }
          for item in data.get("directories") or []
        )
      files = data.get("items") or []
      rows.extend(
        {
          "kind": "file",
          "file_id": self._file_item(item).get("id"),
          "filename": self._file_item(item).get("filename"),
        }
        for item in files
      )
      total = data.get("total")
      more = current * 30 < total if isinstance(total, int) else len(files) == 30
      breadcrumbs = [
        {key: item[key] for key in ("id", "name", "parent_id") if key in item}
        for item in data.get("breadcrumbs") or []
      ]
      return self._list_page(
        rows,
        offset,
        max_results,
        page=current,
        more=more,
        knowledge_id=knowledge_id,
        directory_id=target_dir,
        breadcrumbs=breadcrumbs,
        total_count=total,
      )

    target_label = directory_id or path or "ROOT"
    detail = (
      f"Knowledge base: {knowledge_id}"
      if tree and not (directory_id or path)
      else f"Knowledge base: {knowledge_id}\nDirectory: {target_label}\nPage: {page}"
    )
    return await self._read(
      "read list_knowledge_files",
      detail,
      run,
      __user__,
      __event_call__,
    )

  async def search_knowledge_files(
    self,
    query: str = Field(
      ..., description="Filename or text query across accessible knowledge bases."
    ),
    include_content: bool = False,
    exact_filename: str = "",
    read_match: bool = False,
    page: int = 1,
    max_content_items: int = 10,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Search accessible knowledge files, or read a unique match when `read_match` is true.

    :param query: filename or text query
    :param include_content: true to search file text and include previews
    :param exact_filename: optional exact filename filter
    :param read_match: true to read a single unambiguous first-page match
    :param page: result page, starting at 1
    :param max_content_items: maximum matching files with text previews
    """

    async def run():
      clean_query = (query or "").strip()
      if not clean_query:
        return "Error: search query is empty."
      if __request__ is None:
        return NO_CONTEXT
      if read_match:
        async with await self._open_session(__request__) as session:
          data, err = await self._request(
            session,
            "GET",
            "/knowledge/search/files",
            params={"query": clean_query, "include_content": "false", "page": 1},
          )
          if err:
            return err
          items = (data or {}).get("items", [])
          if exact_filename:
            exact = [
              item
              for item in items
              if self._file_item(item).get("filename", "").casefold()
              == exact_filename.strip().casefold()
            ]
            if exact:
              items = exact
          if not items:
            return "No matching knowledge file found."
          if len(items) > 1:
            return "Multiple matching files found:\n" + "\n".join(
              f"- {self._file_item(item).get('filename', 'Unnamed')} "
              f"(file_id={self._file_item(item).get('id')})"
              for item in items
            )
          chosen = self._file_item(items[0])
          body, err = await self._request(
            session, "GET", f"/files/{chosen.get('id')}/data/content"
          )
        return (
          err
          or f"filename={chosen.get('filename', 'Unnamed')}\n"
          f"file_id={chosen.get('id')}\n\n{(body or {}).get('content', '')}"
        )

      current = max(1, page)
      preview_cap = max(1, min(max_content_items, 50))
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session,
          "GET",
          "/knowledge/search/files",
          params={
            "query": clean_query,
            "include_content": str(include_content).lower(),
            "page": current,
          },
        )
        if err:
          return err
        items = (data or {}).get("items", [])
        if exact_filename:
          items = [
            item
            for item in items
            if self._file_item(item).get("filename", "").casefold()
            == exact_filename.strip().casefold()
          ]
        if not items:
          return (
            f"No matching knowledge files found.\nquery={clean_query}\npage={current}"
          )
        result = [f"Search results for: {clean_query}", f"page={current}", ""]
        for index, item in enumerate(items):
          file_data = self._file_item(item)
          filename = file_data.get("filename", file_data.get("name", "Unnamed"))
          file_id = file_data.get("id")
          result.append(f"- {filename} (file_id={file_id})")
          if include_content and file_id and index < preview_cap:
            content_data, content_error = await self._request(
              session, "GET", f"/files/{file_id}/data/content"
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

    detail = f"Query: {query}\nPage: {page}"
    if exact_filename:
      detail += f"\nExact filename: {exact_filename}"
    return await self._read(
      "read search_knowledge_files",
      detail,
      run,
      __user__,
      __event_call__,
    )

  async def read_knowledge_file(
    self,
    file_id: str = "",
    knowledge_id: str = "",
    path: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_chars: int = 10000,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Read a character range from a knowledge file by `file_id` or by `knowledge_id` and `path`.

    :param file_id: knowledge file ID to read directly
    :param knowledge_id: knowledge base ID when resolving `path`
    :param path: file path within `knowledge_id` when `file_id` is empty
    :param offset: character offset in returned text
    :param max_chars: maximum characters within output byte limits
    """
    clean_id = (file_id or "").strip()

    async def run():
      if clean_id:
        if __request__ is None:
          return NO_CONTEXT
        async with await self._open_session(__request__) as session:
          data, err = await self._request(
            session, "GET", f"/files/{clean_id}/data/content"
          )
        if err:
          return err
        content = (data or {}).get("content", "")
        return (
          content if content else "The file exists, but it holds no extracted text."
        )
      if not (knowledge_id or "").strip():
        return "Error: provide file_id or both knowledge_id and path."
      return await self._read_or_update_path(
        knowledge_id, path, None, False, __request__
      )

    if (
      not clean_id
      and (knowledge_id or "").strip()
      and self._permissions(__user__) != "Always ask"
    ):
      _, err = self._path_parts(path)
      if err:
        return err

    detail = (
      f"File: {clean_id}"
      if clean_id
      else f"Knowledge base: {knowledge_id}\nPath: {path}"
    )
    identifiers = {"file_id": clean_id} if clean_id else {"knowledge_id": knowledge_id}
    return await self._read(
      "read read_knowledge_file",
      detail,
      run,
      __user__,
      __event_call__,
      offset=offset,
      max_chars=max_chars,
      identifiers=identifiers,
    )

  async def save_knowledge_file(
    self,
    content: str = "",
    knowledge_id: str = "",
    file_id: str = "",
    path: str = "",
    filename: str = "",
    directory_id: str = "",
    new_filename: str = "",
    mode: Literal["upsert", "create", "update"] = "upsert",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create, update, upsert, or rename a knowledge file by `file_id`, `filename`, or `path`.

    :param content: full Markdown or text content to write
    :param knowledge_id: target knowledge base ID for path or directory writes
    :param file_id: existing knowledge file ID for direct updates or renames
    :param path: slash-separated file path inside `knowledge_id`
    :param filename: filename when creating inside `directory_id`
    :param directory_id: target directory ID when creating with `filename`
    :param new_filename: replacement filename when renaming an existing file
    :param mode: write behavior for `path` (`upsert`, `create`, or `update`)
    """
    clean_id = (file_id or "").strip()
    clean_rename = (new_filename or "").strip()

    if clean_rename and not content:
      target_label = clean_id or f"{knowledge_id}:{path}"
      refusal = await self._guard(
        "rename a knowledge file",
        f"{target_label} -> {clean_rename}",
        __user__,
        __event_call__,
      )
      if refusal:
        return refusal
      if "/" in clean_rename or "\\" in clean_rename:
        return "Error: filename must not contain directory separators."
      if not clean_id:
        parts, err = self._path_parts(path)
        if err:
          return err
        if __request__ is None:
          return NO_CONTEXT
        async with await self._open_session(__request__) as session:
          directory, err = await self._resolve_directory(
            session, knowledge_id, parts[:-1]
          )
          if err:
            return err
          item, err = await self._find_file_in_directory(
            session, knowledge_id, directory, parts[-1]
          )
          if err:
            return err
          clean_id = str(item.get("id") or "")
          _, err = await self._request(
            session,
            "POST",
            f"/files/{clean_id}/rename",
            expected=(200, 201),
            json={"filename": clean_rename},
          )
        return (
          err
          or f"Knowledge file renamed successfully.\n"
          f"file_id={clean_id}\nnew_filename={clean_rename}"
        )
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session,
          "POST",
          f"/files/{clean_id}/rename",
          expected=(200, 201),
          json={"filename": clean_rename},
        )
      return (
        err
        or f"Knowledge file renamed successfully.\n"
        f"file_id={clean_id}\nnew_filename={clean_rename}"
      )

    if clean_id:
      refusal = await self._guard(
        "overwrite a knowledge file",
        clean_id,
        __user__,
        __event_call__,
      )
      if refusal:
        return refusal
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session,
          "POST",
          f"/files/{clean_id}/data/content/update",
          expected=(200, 201),
          json={"content": content},
          timeout=self._long_timeout,
        )
      return (
        err or f"Knowledge file updated and reindexed successfully.\nfile_id={clean_id}"
      )

    if (filename or "").strip() and not (path or "").strip():
      clean_name = filename.strip()
      refusal = await self._guard(
        "create a knowledge file",
        clean_name,
        __user__,
        __event_call__,
        sensitive=False,
      )
      if refusal:
        return refusal
      if "/" in clean_name or "\\" in clean_name:
        return "Error: filename must not contain directory separators."
      if not clean_name.lower().endswith(".md"):
        clean_name += ".md"
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        data, err = await self._upload(
          session, knowledge_id, directory_id, clean_name, content
        )
      if err:
        return err
      return (
        f"Created and indexed knowledge file successfully.\n"
        f"filename={data.get('filename', clean_name)}\n"
        f"file_id={data.get('id')}\n"
        f"knowledge_id={knowledge_id}\n"
        f"directory_id={directory_id or 'ROOT'}"
      )

    if mode not in ("upsert", "create", "update"):
      return "Error: mode must be upsert, create, or update."
    action_label = (
      "create a knowledge file"
      if mode == "create"
      else "overwrite a knowledge file"
      if mode == "update"
      else "write a knowledge file"
    )
    refusal = await self._guard(
      action_label,
      f"{knowledge_id}:{path}",
      __user__,
      __event_call__,
      sensitive=(mode != "create"),
    )
    if refusal:
      return refusal
    if mode == "update":
      return await self._read_or_update_path(
        knowledge_id, path, content, True, __request__
      )
    parts, err = self._path_parts(path)
    if err:
      return err
    leaf = parts[-1]
    if not leaf.lower().endswith(".md"):
      leaf += ".md"
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as session:
      directory, err = await self._ensure_dirs(session, knowledge_id, parts[:-1])
      if err:
        return err
      if mode == "create":
        data, err = await self._upload(session, knowledge_id, directory, leaf, content)
        if err:
          return err
        return (
          f"Knowledge Markdown file created successfully.\n"
          f"path={'/'.join(parts[:-1] + [leaf])}\n"
          f"filename={data.get('filename', leaf)}\n"
          f"file_id={data.get('id')}\n"
          f"directory_id={directory or 'ROOT'}"
        )
      # Reject API errors or duplicate matches instead of uploading another copy.
      item, finderr = await self._find_file_in_directory(
        session, knowledge_id, directory, leaf
      )
      if finderr and finderr != f"File not found: {leaf}":
        return finderr
      if not finderr:
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
          f"Knowledge Markdown file updated successfully.\n"
          f"action=updated\n"
          f"path={'/'.join(parts[:-1] + [leaf])}\n"
          f"file_id={item.get('id')}"
        )
      data, err = await self._upload(session, knowledge_id, directory, leaf, content)
    if err:
      return err
    return (
      f"Knowledge Markdown file created successfully.\n"
      f"action=created\n"
      f"path={'/'.join(parts[:-1] + [leaf])}\n"
      f"file_id={data.get('id')}"
    )

  async def delete_knowledge_file(
    self,
    confirm: bool = Field(
      ..., description="Set true only when the user requested permanent deletion."
    ),
    file_id: str = "",
    knowledge_id: str = "",
    path: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Permanently delete a knowledge file by `file_id` or `path` when `confirm` is true.

    :param confirm: true only when the user requested permanent deletion
    :param file_id: knowledge file ID to delete directly
    :param knowledge_id: knowledge base ID when deleting via `path`
    :param path: exact file path when `file_id` is empty
    """
    clean_id = (file_id or "").strip()
    detail = clean_id if clean_id else f"{knowledge_id}:{path}"
    refusal = await self._guard(
      "delete a knowledge file",
      detail,
      __user__,
      __event_call__,
    )
    if refusal:
      return refusal
    if confirm is not True:
      return "Deletion cancelled: confirm must be true."
    if clean_id:
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session, "DELETE", f"/files/{clean_id}", expected=(200, 204)
        )
      return (
        err or f"Knowledge file permanently deleted successfully.\nfile_id={clean_id}"
      )
    parts, err = self._path_parts(path)
    if err:
      return err
    if __request__ is None:
      return NO_CONTEXT
    async with await self._open_session(__request__) as session:
      directory, err = await self._resolve_directory(session, knowledge_id, parts[:-1])
      if err:
        return err
      item, err = await self._find_file_in_directory(
        session, knowledge_id, directory, parts[-1]
      )
      if err:
        return err
      _, err = await self._request(
        session, "DELETE", f"/files/{item.get('id')}", expected=(200, 204)
      )
    return (
      err
      or f"Knowledge file permanently deleted successfully.\n"
      f"path={path}\nfile_id={item.get('id')}"
    )

  async def manage_knowledge_directory(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    action: Literal[
      "ensure_path", "create", "rename", "move", "delete"
    ] = "ensure_path",
    path: str = "",
    directory_id: str = "",
    name: str = "",
    parent_id: str = "",
    new_name: str = "",
    target_parent_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create, resolve, rename, move, or delete a directory inside a knowledge base.

    :param knowledge_id: Open WebUI knowledge base ID
    :param action: directory operation (`ensure_path`, `create`, `rename`, `move`, or `delete`)
    :param path: slash-separated directory path for `ensure_path`
    :param directory_id: target directory ID for `rename`, `move`, or `delete`
    :param name: directory name for `create`
    :param parent_id: optional parent directory ID for `create`
    :param new_name: replacement name for `rename`
    :param target_parent_id: destination parent directory ID for `move`, or empty for root
    """
    if action == "ensure_path":
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
            entry
            for entry in listing["directories"]
            if entry.get("name", "").casefold() == part.casefold()
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

    if action == "create":
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
      clean_name = (name or "").strip()
      if not clean_name:
        return "Error: directory name is empty."
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session,
          "POST",
          f"/knowledge/{knowledge_id}/dirs/create",
          expected=(200, 201),
          json={"name": clean_name, "parent_id": parent_id or None},
        )
      if err:
        return err
      return (
        f"Knowledge directory created successfully.\n"
        f"name={data.get('name', clean_name)}\n"
        f"directory_id={data.get('id')}\n"
        f"parent_id={data.get('parent_id')}"
      )

    if action == "rename":
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
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session,
          "POST",
          f"/knowledge/{knowledge_id}/dirs/{directory_id}/update",
          expected=(200, 201),
          json={"name": (new_name or "").strip()},
        )
      return (
        err
        or f"Knowledge directory renamed successfully.\n"
        f"directory_id={directory_id}\n"
        f"new_name={(data or {}).get('name', new_name)}"
      )

    if action == "move":
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
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session,
          "POST",
          f"/knowledge/{knowledge_id}/dirs/{directory_id}/update",
          expected=(200, 201),
          json={"parent_id": target_parent_id or None},
        )
      return (
        err
        or f"Knowledge directory moved successfully.\n"
        f"directory_id={directory_id}\nparent_id={(data or {}).get('parent_id')}"
      )

    if action == "delete":
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
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session,
          "DELETE",
          f"/knowledge/{knowledge_id}/dirs/{directory_id}/delete",
          expected=(200, 204),
          params={"move_files": "true"},
        )
      return (
        err or f"Knowledge directory deleted successfully.\ndirectory_id={directory_id}"
      )

    return "Error: invalid directory action."

  async def list_files(
    self,
    filename: str = "",
    page: int = 1,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List library files by page, or search filenames with wildcards when `filename` is set.

    :param filename: optional filename or wildcard pattern such as `*.md`
    :param page: API page when `filename` is empty, starting at 1
    :param offset: row offset
    :param max_results: maximum rows within output byte limits
    """
    has_query = bool((filename or "").strip())

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      if has_query:
        name = filename.strip()
        start = max(0, int(offset))
        size = min(100, max(1, int(max_results)))
        async with await self._open_session(__request__) as session:
          data, err = await self._request(
            session,
            "GET",
            "/files/search",
            expected=(200, 404),
            params={
              "filename": name,
              "content": "false",
              "skip": start,
              "limit": size,
            },
          )
        if err:
          return err
        items = data if isinstance(data, list) else (data or {}).get("items") or []
        rows = [
          {key: item[key] for key in ("id", "filename") if key in item}
          for item in items
        ]
        return self._list_page(
          rows, 0, size, base_offset=start, more=len(items) == size, total_count=None
        )

      current = max(1, int(page))
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session, "GET", "/files/", params={"page": current, "content": "false"}
        )
      if err:
        return err
      items = (data or {}).get("items") or []
      rows = [
        {key: item[key] for key in ("id", "filename") if key in item} for item in items
      ]
      for row, item in zip(rows, items):
        row["size"] = (item.get("meta") or {}).get("size") or 0
      total = (data or {}).get("total")
      more = current * 50 < total if isinstance(total, int) else len(items) == 50
      return self._list_page(
        rows, offset, max_results, page=current, more=more, total_count=total
      )

    detail = f"Filename: {filename}" if has_query else str(page)
    return await self._read("read list_files", detail, run, __user__, __event_call__)

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
    Read a character range from one library file's stored text.

    :param file_id: Open WebUI library file ID
    :param offset: character offset in stored text
    :param max_chars: maximum characters within output byte limits
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session, "GET", f"/files/{file_id}/data/content"
        )
      if err:
        return err
      content = (data or {}).get("content")
      if content is None:
        return "Error: the file has no stored text content."
      page = self._page(
        {"file_id": file_id, "content": str(content)}, offset, max_chars
      )
      return json.dumps(page, indent=2, ensure_ascii=False)

    return await self._read(
      "read read_file_content",
      f"File: {file_id}",
      run,
      __user__,
      __event_call__,
    )

  async def save_file(
    self,
    filename: str,
    content: str = "",
    file_id: str = "",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Upload a new library file when `file_id` is empty, or rename an existing file.

    :param filename: new filename including extension
    :param content: full text content when uploading a new file
    :param file_id: existing library file ID to rename, or empty to upload
    """
    clean_id = (file_id or "").strip()
    if clean_id:
      if __request__ is None:
        return NO_CONTEXT
      refusal = await self._guard(
        "rename a file", f"{clean_id} -> {filename}", __user__, __event_call__
      )
      if refusal:
        return refusal
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session, "POST", f"/files/{clean_id}/rename", json={"filename": filename}
        )
      return err or f"renamed file {clean_id} to {filename}"

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
    body.add_field(
      "file", content.encode("utf-8"), filename=name, content_type="text/markdown"
    )
    async with await self._open_session(__request__) as session:
      data, err = await self._request(
        session,
        "POST",
        "/files/",
        data=body,
        expected=(200, 201),
      )
    if err:
      return err
    return f"uploaded {name} (id={(data or {}).get('id')})"

  async def delete_file(
    self,
    file_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete one library file after write confirmation.

    :param file_id: library file ID to delete
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard("delete a file", str(file_id), __user__, __event_call__)
    if refusal:
      return refusal
    async with await self._open_session(__request__) as session:
      _, err = await self._request(session, "DELETE", f"/files/{file_id}")
    return err or f"deleted file {file_id}"

  @staticmethod
  def _format_skill_files(data) -> str:
    """Format one versioned skill-file manifest as text."""
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
    skill_id: str = "",
    version_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    page: int = 1,
    offset: int = 0,
    max_results: int = 20,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    List workspace skills by page, or list files in one skill snapshot when `skill_id` is set.

    :param skill_id: optional skill ID for snapshot file listing
    :param version_id: optional version ID when `skill_id` is set
    :param page: API page when `skill_id` is empty, starting at 1
    :param offset: row offset
    :param max_results: maximum rows within output byte limits
    """
    clean_skill = (skill_id or "").strip()

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      if clean_skill:
        params = {"version_id": version_id} if version_id else None
        async with await self._open_session(__request__) as session:
          data, err = await self._request(
            session, "GET", f"/skills/id/{clean_skill}/files", params=params
          )
        if err:
          return err
        data = data or {}
        rows = [
          {key: item[key] for key in ("path", "size", "encoding") if key in item}
          for item in data.get("files") or []
        ]
        return self._list_page(
          rows,
          offset,
          max_results,
          skill_id=clean_skill,
          version_id=data.get("version_id"),
        )

      current = max(1, int(page))
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session, "GET", "/skills/list", params={"page": current}
        )
      if err:
        return err
      items = (data or {}).get("items") or []
      rows = [
        {
          key: item[key]
          for key in (
            "id",
            "name",
            "is_active",
            "version_id",
            "write_access",
            "access_control",
            "access_grants",
            "user_id",
          )
          if key in item
        }
        for item in items
      ]
      total = (data or {}).get("total")
      more = current * 30 < total if isinstance(total, int) else len(items) == 30
      return self._list_page(
        rows, offset, max_results, page=current, more=more, total_count=total
      )

    detail = clean_skill if clean_skill else str(page)
    return await self._read("read list_skills", detail, run, __user__, __event_call__)

  async def show_skill(
    self,
    skill_id: str,
    path: str = "",
    version_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    offset: int = 0,
    max_chars: int = 10000,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Read one skill and its manifest, or page one supporting file when `path` is set.

    :param skill_id: skill ID to inspect
    :param path: optional relative path within the skill, such as `references/api.md`
    :param version_id: optional snapshot version ID
    :param offset: character offset in returned text
    :param max_chars: maximum characters within output byte limits
    """
    clean_path = (path or "").strip()
    if clean_path:

      async def run_file():
        if __request__ is None:
          return NO_CONTEXT
        params = {"version_id": version_id} if version_id else None
        async with await self._open_session(__request__) as session:
          manifest, err = await self._request(
            session, "GET", f"/skills/id/{skill_id}/files", params=params
          )
          if err:
            return err
          snapshot = (manifest or {}).get("version_id") or version_id
          item = next(
            (
              entry
              for entry in (manifest or {}).get("files") or []
              if entry.get("path") == clean_path
            ),
            None,
          )
          if not item:
            return f"Error: skill file not found: {clean_path}"
          if item.get("encoding"):
            return (
              f"path={clean_path}\nversion_id={snapshot}\nsize={item.get('size', 0)}\n"
              f"encoding={item.get('encoding')}\ncontent=<binary file>\n"
              "Open the file in the Open WebUI skill editor."
            )
          data, err = await self._request(
            session,
            "GET",
            f"/skills/id/{skill_id}/files/content",
            params={"path": clean_path, "version_id": snapshot},
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
        payload = {"content": content, "path": clean_path, "version_id": snapshot}
        page = self._page(payload, offset or 0, min(int(max_chars or 10000), 100000))
        next_offset = page["next_offset"]
        return (
          f"path={clean_path}\nversion_id={snapshot}\noffset={page['offset']}\n"
          f"next_offset={next_offset if next_offset is not None else 'none'}\n"
          f"content={page['content']}"
        )

      return await self._read(
        "read show_skill",
        f"{skill_id}:{clean_path}",
        run_file,
        __user__,
        __event_call__,
      )

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", f"/skills/id/{skill_id}")
        if err:
          return err
        if not data:
          return f"Error: skill not found: {skill_id}"
        files, err = await self._request(
          session,
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
      offset=offset,
      max_chars=max_chars,
      identifiers={"skill_id": skill_id},
    )

  async def save_skill(
    self,
    action: Literal[
      "create", "import_url", "update", "update_files", "toggle"
    ] = "create",
    name: str = "",
    content: str = "",
    description: str = "",
    skill_id: str = "",
    url: str = "",
    files: list[SkillFileInput] | None = None,
    operations: list[SkillFileOperationInput] | None = None,
    expected_version_id: str = "",
    commit_message: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create, import, update, patch files on, or toggle a workspace skill.

    :param action: skill operation (`create`, `import_url`, `update`, `update_files`, or `toggle`)
    :param name: skill name for `create` or `update`
    :param content: full `SKILL.md` instructions for `create` or `update`
    :param description: short skill description
    :param skill_id: skill ID for updates, toggles, or explicit creation IDs
    :param url: trusted HTTPS package URL for `import_url`
    :param files: optional UTF-8 supporting files for `create`
    :param operations: file operations (`put`, `move`, or `delete`) for `update_files`
    :param expected_version_id: expected snapshot version for `update_files`, or empty to read it
    :param commit_message: optional version message for `create` or `update_files`
    """
    if action == "create":
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
        rel_path = str(item.get("path") or "").strip()
        if not rel_path or "content" not in item:
          return "Error: each supporting file must have path and content."
        if rel_path == "SKILL.md":
          return "Error: pass SKILL.md in content, not files."
        if item.get("encoding"):
          return "Error: create_skill supporting files must be UTF-8 text."
        if not isinstance(item.get("content"), str):
          return f"Error: supporting file content must be text: {rel_path}"
        supporting.append({"path": rel_path, "content": item["content"]})
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
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session, "POST", "/skills/create", json=body, expected=(200, 201)
        )
        if err:
          return err
        identifier = (data or {}).get("id") or identifier
        attach = await self._attach_to_presets({"skillIds": [identifier]}, __request__)
      return (
        f"created skill {name} (id={identifier})\nfiles={len(skill_files)}\n{attach}"
      )

    if action == "import_url":
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
          pkg_files = package.get("files")
          if not isinstance(pkg_files, list) or not pkg_files:
            pkg_content = package.get("content")
            pkg_files = (
              [{"path": "SKILL.md", "content": pkg_content}]
              if isinstance(pkg_content, str)
              else []
            )
          if not pkg_files or any(not isinstance(item, dict) for item in pkg_files):
            lines.append("failed import: Open WebUI returned invalid skill files")
            continue
          identifier = str(package.get("id") or "")
          pkg_name = str(package.get("name") or identifier or "Imported skill")
          root = next(
            (
              item.get("content")
              for item in pkg_files
              if item.get("path") == "SKILL.md"
            ),
            "",
          )
          body = {
            "id": identifier,
            "name": pkg_name,
            "description": package.get("description") or "",
            "content": root,
            "files": pkg_files,
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
            lines.append(f"failed {pkg_name} (id={identifier}): {create_err}")
            continue
          created_id = (created or {}).get("id") or identifier
          imported.append(created_id)
          lines.append(f"imported {pkg_name} (id={created_id}, files={len(pkg_files)})")
      if imported:
        lines.append(await self._attach_to_presets({"skillIds": imported}, __request__))
      return "\n".join(lines)

    if action == "update":
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
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session, "POST", f"/skills/id/{skill_id}/update", json=body
        )
      return err or f"updated skill {skill_id}"

    if action == "update_files":
      if __request__ is None:
        return NO_CONTEXT
      refusal = await self._guard(
        "update skill files", str(skill_id), __user__, __event_call__
      )
      if refusal:
        return refusal
      if not isinstance(operations, list) or not operations:
        return "Error: provide at least one skill file operation."
      normalized = [
        op.model_dump(exclude_none=True) if isinstance(op, BaseModel) else op
        for op in operations
      ]
      for op in normalized:
        if not isinstance(op, dict):
          return "Error: each skill file operation must be an object."
        if op.get("encoding"):
          return "Error: save_skill writes UTF-8 text only."
        if op.get("op") not in {"put", "move", "delete"}:
          return "Error: skill file op must be put, move or delete."
        if not str(op.get("path") or "").strip():
          return "Error: each skill file operation needs a path."
        if op.get("op") == "put" and not isinstance(op.get("content"), str):
          return "Error: a put operation needs text content."
        if op.get("op") == "move" and not str(op.get("destination") or "").strip():
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
          "operations": normalized,
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

    if action == "toggle":
      if __request__ is None:
        return NO_CONTEXT
      refusal = await self._guard(
        "toggle a skill", str(skill_id), __user__, __event_call__
      )
      if refusal:
        return refusal
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session, "POST", f"/skills/id/{skill_id}/toggle"
        )
      if err:
        return err
      return f"toggled skill {skill_id} (active={(data or {}).get('is_active')})"

    return "Error: invalid skill action."

  async def delete_skill(
    self,
    skill_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete one workspace skill after write confirmation.

    :param skill_id: skill ID to delete
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "delete a skill", str(skill_id), __user__, __event_call__
    )
    if refusal:
      return refusal
    async with await self._open_session(__request__) as session:
      _, err = await self._request(session, "DELETE", f"/skills/id/{skill_id}/delete")
    return err or f"deleted skill {skill_id}"

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
    List Workspace Tools with IDs and access metadata.

    :param offset: row offset
    :param max_results: maximum rows within output byte limits
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", "/tools/")
      if err:
        return err
      items = data if isinstance(data, list) else (data or {}).get("items") or []
      rows = [
        {
          key: item[key]
          for key in (
            "id",
            "name",
            "write_access",
            "access_control",
            "access_grants",
            "user_id",
          )
          if key in item
        }
        for item in items
      ]
      return self._list_page(rows, offset, max_results)

    return await self._read(
      "read list_tools", str(offset), run, __user__, __event_call__
    )

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
    Read one Workspace Tool's metadata and paged Python source.

    :param tool_id: Workspace Tool ID to inspect
    :param offset: character offset in returned text
    :param max_chars: maximum characters within output byte limits
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", f"/tools/id/{tool_id}")
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
      offset=offset,
      max_chars=max_chars,
      identifiers={"tool_id": tool_id},
    )

  async def save_tool(
    self,
    action: Literal["create", "update", "toggle"] = "create",
    name: str = "",
    content: str = "",
    description: str = "",
    tool_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create, update, or toggle preset attachment for a Workspace Tool.

    :param action: tool operation (`create`, `update`, or `toggle`)
    :param name: tool name for `create` or `update`
    :param content: full Python source for `create` or `update`
    :param description: short description for `create` or `update`
    :param tool_id: tool ID for `update`, `toggle`, or explicit creation IDs
    """
    if action == "create":
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
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session, "POST", "/tools/create", json=body, expected=(200, 201)
        )
        if err:
          return err
        attach = await self._attach_to_presets({"toolIds": [identifier]}, __request__)
      return f"created tool {name} (id={identifier})\n{attach}"

    if action == "update":
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
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session, "POST", f"/tools/id/{tool_id}/update", json=body
        )
      return err or f"updated tool {tool_id}"

    if action == "toggle":
      if __request__ is None:
        return NO_CONTEXT
      refusal = await self._guard(
        "toggle a tool", str(tool_id), __user__, __event_call__
      )
      if refusal:
        return refusal
      present = 0
      total = 0
      async with await self._open_session(__request__) as session:
        page = 1
        while page <= 100:
          data, err = await self._request(
            session, "GET", "/models/list", params={"page": page}
          )
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

    return "Error: invalid tool action."

  async def delete_tool(
    self,
    tool_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete one Workspace Tool after write confirmation.

    :param tool_id: Workspace Tool ID to delete
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard("delete a tool", str(tool_id), __user__, __event_call__)
    if refusal:
      return refusal
    async with await self._open_session(__request__) as session:
      _, err = await self._request(session, "DELETE", f"/tools/id/{tool_id}/delete")
    return err or f"deleted tool {tool_id}"

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
    List workspace Functions with IDs, types, and active flags.

    :param offset: row offset
    :param max_results: maximum rows within output byte limits
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", "/functions/")
      if err:
        return err
      items = data if isinstance(data, list) else (data or {}).get("items") or []
      rows = [
        {
          key: item[key]
          for key in ("id", "name", "type", "is_active", "is_global")
          if key in item
        }
        for item in items
      ]
      return self._list_page(rows, offset, max_results)

    return await self._read(
      "read list_functions", str(offset), run, __user__, __event_call__
    )

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
    Read one Function's metadata, state, and paged Python source.

    :param function_id: Function ID to inspect
    :param offset: character offset in returned text
    :param max_chars: maximum characters within output byte limits
    """

    async def run():
      if __request__ is None:
        return NO_CONTEXT
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", f"/functions/id/{function_id}")
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
      offset=offset,
      max_chars=max_chars,
      identifiers={"function_id": function_id},
    )

  async def save_function(
    self,
    action: Literal["create", "update", "toggle"] = "create",
    name: str = "",
    content: str = "",
    description: str = "",
    function_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Create, update, or toggle one workspace Function.

    :param action: function operation (`create`, `update`, or `toggle`)
    :param name: function name for `create` or `update`
    :param content: full Python source for `create` or `update`
    :param description: short description for `create` or `update`
    :param function_id: function ID for `update`, `toggle`, or explicit creation IDs
    """
    if action == "create":
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
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session, "POST", "/functions/create", json=body, expected=(200, 201)
        )
        if err:
          return err
        attach = await self._attach_to_presets(
          {"functionIds": [identifier]}, __request__
        )
      return f"created function {name} (id={identifier})\n{attach}"

    if action == "update":
      if __request__ is None:
        return NO_CONTEXT
      refusal = await self._guard(
        "overwrite a function",
        f"{name} (id={function_id})",
        __user__,
        __event_call__,
      )
      if refusal:
        return refusal
      body = {
        "id": function_id,
        "name": name,
        "content": content,
        "meta": {"description": description, "manifest": {}},
      }
      async with await self._open_session(__request__) as session:
        _, err = await self._request(
          session, "POST", f"/functions/id/{function_id}/update", json=body
        )
      return err or f"updated function {function_id}"

    if action == "toggle":
      if __request__ is None:
        return NO_CONTEXT
      refusal = await self._guard(
        "toggle a function", str(function_id), __user__, __event_call__
      )
      if refusal:
        return refusal
      async with await self._open_session(__request__) as session:
        data, err = await self._request(
          session, "POST", f"/functions/id/{function_id}/toggle"
        )
      if err:
        return err
      return (
        f"toggled function {function_id} "
        f"(active={(data or {}).get('is_active')}, global={(data or {}).get('is_global')})"
      )

    return "Error: invalid function action."

  async def delete_function(
    self,
    function_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
    __event_emitter__: Callable | None = None,
  ) -> str:
    """
    Delete one workspace Function after write confirmation.

    :param function_id: Function ID to delete
    """
    if __request__ is None:
      return NO_CONTEXT
    refusal = await self._guard(
      "delete a function", str(function_id), __user__, __event_call__
    )
    if refusal:
      return refusal
    async with await self._open_session(__request__) as session:
      _, err = await self._request(
        session, "DELETE", f"/functions/id/{function_id}/delete"
      )
    return err or f"deleted function {function_id}"
