"""
title: Open WebUI Manager
author: nemo
description: The Open WebUI workspace manager: knowledge bases, skills, the file library, Workspace Tools and Functions. Reads and new items run freely. Every overwrite, toggle and delete passes a confirmation gate. The preset attach stays private. Stdlib only.
required_open_webui_version: 0.10.0
version: 1.0.1
licence: daedalus Noncommercial License 1.0.0
"""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any, Literal

import aiohttp
from pydantic import BaseModel, Field


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
  """Apply the add and remove lists to a current list, order kept.

  Knowledge entries are reference objects. The other lists hold plain ids.
  """
  gone = {str(item) for item in remove}
  merged = [item for item in (current or []) if _entry_key(item) not in gone]
  have = {_entry_key(item) for item in merged}
  for item in add:
    key = _entry_key(item)
    if key and key not in have:
      merged.append(item)
      have.add(key)
  return merged


class Tools:
  """The one Open WebUI workspace manager tool.

  The file wins over the 2 old manager tools: the 23 knowledge tools keep
  their names, and the skills, the file library, the Workspace Tools and the
  Functions join them. Reads and new items run freely. A mutation and a
  toggle pass the gate. The preset attach is private, so Open WebUI builds no
  model tool spec for it.
  """

  class Valves(BaseModel):
    OWUI_API_BASE: str = "http://127.0.0.1:8080/api/v1"
    PRESET_MODELS: str = ""
    permissions: Literal["Always ask", "Allow reads", "Always allow"] = "Always ask"
    timeout_seconds: int = 60
    INSTALL_FETCH_TIMEOUT: float = 12.0
    TRUSTED_DOMAINS: str = "github.com,huggingface.co,githubusercontent.com"
    SHOW_STATUS: bool = True

  class UserValves(BaseModel):
    mode: str = "default"
    timeout_seconds: int = 0

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
    """The permission level: the user valve wins, then the tool valve.

    Always ask holds every call, reads included. Allow reads lets a read run
    and holds the mutations and the toggles. Always allow holds nothing.
    """
    user_mode = self._user_valve(__user__, "mode")
    if user_mode and user_mode != "default":
      return str(user_mode)
    return str(self.valves.permissions or "Always ask")

  def _wait_seconds(self, __user__: dict | None = None) -> int:
    """The confirmation wait: the user value wins, then the tool value."""
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
    """Ask the user to confirm one write. Returns False when it cannot be asked.

    The confirmation travels over the socket of the tab that started the chat.
    A page refresh drops the dialog and the server would wait forever, because
    WEBSOCKET_EVENT_CALLER_TIMEOUT starts unset. The wait_for below is the only
    timeout that always exists, so a refresh costs one wait, then a deny.
    """
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
    except asyncio.TimeoutError:
      return False
    except Exception:
      return False
    return answer is True

  async def _guard(
    self, action, detail, __user__, __event_call__, sensitive: bool = True
  ):
    """Run the gate for one mutation. Returns a refusal text, or None to go on.

    sensitive holds a mutation and a toggle: every level but Always allow asks.
    A new item passes sensitive=False, so only Always ask holds it.
    """
    level = self._permissions(__user__)
    asked = level != "Always allow" if sensitive else level == "Always ask"
    if asked and not await self._ask(action, detail, __event_call__, __user__):
      return json.dumps(
        {
          "result": {
            "denied": True,
            "action": action,
            "reason": (
              f"Not confirmed within {self._wait_seconds(__user__)}s, so nothing "
              "was sent."
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
    self, session, method, path, *, timeout=None, expected=(200,), **kwargs
  ):
    """Unified HTTP request sending and response handling."""
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
        try:
          return await response.json(content_type=None), None
        except (ValueError, aiohttp.ContentTypeError):
          text = await response.text()
          return {"_text": text}, None
    except asyncio.TimeoutError:
      return None, "Error: Open WebUI API request timed out."
    except aiohttp.ClientError as exc:
      return None, f"Error: Open WebUI connection failed: {exc}"

  def _error(self, message):
    return message

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
  ) -> str:
    """
    List knowledge bases available to the current Open WebUI user,
    including their IDs and write access.
    """

    async def run(
      __request__=__request__, __user__=__user__, __event_call__=__event_call__
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      async with await self._open_session(__request__) as session:
        data, err = await self._request(session, "GET", "/knowledge/")
      if err:
        return err
      items = (data or {}).get("items", [])
      if not items:
        return "No knowledge bases found."
      return "\n".join(
        f"- {x.get('name', 'Unnamed')} (id={x.get('id')}, write_access={x.get('write_access')})"
        for x in items
      )

    return await self._read(
      "read list_knowledge_bases",
      str(locals().get("page", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def list_knowledge_files(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    directory_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    List files and directories inside an Open WebUI knowledge base.

    directory_id:
    - empty string = root directory
    - directory ID = show contents of that directory

    Returns directory IDs, file IDs and breadcrumbs so the model
    can navigate the knowledge base without creating duplicate folders.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param directory_id: the directory id to list, or the knowledge root when absent
    """

    async def run(
      knowledge_id=knowledge_id,
      directory_id=directory_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      async with await self._open_session(__request__) as session:
        data, err = await self._directory_listing(session, knowledge_id, directory_id)
      if err:
        return err
      out = []
      if data["breadcrumbs"]:
        out.append("PATH:")
        out += [
          f"- {x.get('name', 'Unnamed')} (directory_id={x.get('id')})"
          for x in data["breadcrumbs"]
        ]
      out.append("\nDIRECTORIES:")
      out += [
        f"- {x.get('name', 'Unnamed')} (directory_id={x.get('id')}, parent_id={x.get('parent_id')})"
        for x in data["directories"]
      ] or ["- none"]
      out.append("\nFILES:")
      out += [
        f"- {self._file_item(x).get('filename', 'Unnamed')} (file_id={self._file_item(x).get('id')})"
        for x in data["items"]
      ] or ["- none"]
      return "\n".join(out)

    return await self._read(
      "read list_knowledge_files",
      str(locals().get("page", "") or ""),
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
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Update the name and/or description of an Open WebUI knowledge base.

    Empty new_name keeps the existing name.
    Empty new_description keeps the existing description.

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
      return "Error: Open WebUI request context is unavailable."
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
    return f"Knowledge base updated successfully.\nknowledge_id={knowledge_id}\nname={data.get('name', payload['name'])}\ndescription={data.get('description', payload['description'])}"

  async def create_knowledge_directory(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    name: str = Field(..., description="Name of the directory to create."),
    parent_id: str = "",
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    Create a directory inside an Open WebUI knowledge base.
    Supports nested directories using parent_id.

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
      return "Error: Open WebUI request context is unavailable."
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
    return f"Knowledge directory created successfully.\nname={data.get('name', name)}\ndirectory_id={data.get('id')}\nparent_id={data.get('parent_id')}"

  async def ensure_knowledge_directory_path(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    path: str = Field(
      ...,
      description="Directory path to create or reuse, for example Servers/VPN/Notes.",
    ),
    __request__=None,
  ) -> str:
    """
    Ensure that a nested directory path exists inside a knowledge base.

    The tool reuses an existing directory and creates a missing one.
    Example: Servers/VPN/Notes

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param path: directory path to create or reuse, for example Servers/VPN/Notes
    """
    parts, err = self._path_parts(path)
    if err:
      return err
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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
    return f"Knowledge directory path ready.\npath={'/'.join(parts)}\ndirectory_id={current}\ncreated={', '.join(created) or 'none'}\nreused={', '.join(reused) or 'none'}"

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
  ) -> str:
    """
    Create a new Markdown file in an Open WebUI knowledge base
    and automatically index it for retrieval.

    directory_id:
    - empty string = create in knowledge base root
    - directory ID = create directly inside that directory

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
      return "Error: Open WebUI request context is unavailable."
    async with await self._open_session(__request__) as session:
      data, err = await self._upload(
        session, knowledge_id, directory_id, filename, content
      )
    if err:
      return err
    return f"Created and indexed knowledge file successfully.\nfilename={data.get('filename', filename)}\nfile_id={data.get('id')}\nknowledge_id={knowledge_id}\ndirectory_id={directory_id or 'ROOT'}"

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
  ) -> str:
    """
    Create a Markdown file at a nested path inside a knowledge base.

    The tool reuses an existing directory and creates a missing one.
    The file goes directly into the final directory.

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
      return "Error: Open WebUI request context is unavailable."
    async with await self._open_session(__request__) as session:
      directory, err = await self._ensure_dirs(session, knowledge_id, parts[:-1])
      if err:
        return err
      data, err = await self._upload(
        session, knowledge_id, directory, filename, content
      )
    if err:
      return err
    return f"Knowledge Markdown file created successfully.\npath={'/'.join(parts[:-1] + [filename])}\nfilename={data.get('filename', filename)}\nfile_id={data.get('id')}\ndirectory_id={directory or 'ROOT'}"

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
      return "Error: Open WebUI request context is unavailable."
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
        return f"Knowledge file updated successfully.\npath={path}\nfile_id={item.get('id')}\ndirectory_id={directory or 'ROOT'}"
      data, err = await self._request(
        session, "GET", f"/files/{item.get('id')}/data/content"
      )
    if err:
      return err
    return f"path={path}\nfilename={filename}\nfile_id={item.get('id')}\ndirectory_id={directory or 'ROOT'}\n\n{(data or {}).get('content', '')}"

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
  ) -> str:
    """
    Replace the content of an existing Open WebUI knowledge file
    and reindex it for retrieval.

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
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Read the extracted/indexed text content of an Open WebUI knowledge file.
    Use this before editing an existing document.

    :param file_id: ID of the Open WebUI knowledge file to read
    """

    async def run(
      file_id=file_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      async with await self._open_session(__request__) as s:
        data, e = await self._request(s, "GET", f"/files/{file_id}/data/content")
      if e:
        return e
      c = (data or {}).get("content", "")
      return c if c else "File exists, but no extracted text content was found."

    return await self._read(
      "read read_knowledge_file",
      str(locals().get("page", "") or ""),
      run,
      __user__,
      __event_call__,
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
  ) -> str:
    """
    Find an existing knowledge file by its exact path
    and replace its complete text content.

    Open WebUI reindexes the file automatically.
    Does not create missing directories or files.

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
  ) -> str:
    """
    Read an existing knowledge file by its exact path.
    Does not create or modify anything.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param path: exact file path, for example Projects/Servers/VPN/Notes/readme.md
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
      str(locals().get("page", "") or ""),
      run,
      __user__,
      __event_call__,
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
  ) -> str:
    """
    Create or update a Markdown file at an exact knowledge-base path.

    - The tool reuses an existing directory.
    - The tool creates a missing directory.
    - The tool updates and reindexes an existing file.
    - The tool creates and indexes a missing file.
    - Ambiguous directory or file matches are never guessed.

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
      return "Error: Open WebUI request context is unavailable."
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
        return f"Knowledge Markdown file updated successfully.\naction=updated\npath={'/'.join(parts[:-1] + [filename])}\nfile_id={item.get('id')}"
      data, err = await self._upload(s, knowledge_id, directory, filename, content)
    if err:
      return err
    return f"Knowledge Markdown file created successfully.\naction=created\npath={'/'.join(parts[:-1] + [filename])}\nfile_id={data.get('id')}"

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
      return "Error: Open WebUI request context is unavailable."
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
      return "Error: Open WebUI request context is unavailable."
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
      or f"Knowledge directory renamed successfully.\ndirectory_id={directory_id}\nnew_name={(d or {}).get('name', new_name)}"
    )

  async def move_knowledge_directory(
    self,
    knowledge_id: str = Field(..., description="ID of the Open WebUI knowledge base."),
    directory_id: str = Field(..., description="ID of the directory to move."),
    target_parent_id: str = Field(
      ...,
      description="ID of the target parent directory. Use an empty string to move the directory to the knowledge base root.",
    ),
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    Move a directory inside an Open WebUI knowledge base.

    Use an empty target_parent_id to move the directory to the root.

    :param knowledge_id: ID of the Open WebUI knowledge base
    :param directory_id: ID of the directory to move
    :param target_parent_id: ID of the target parent directory. Use an empty string to move the directory to the knowledge base root
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
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Permanently delete an Open WebUI knowledge file.

    This removes the file itself, its knowledge-base associations,
    and its indexed embeddings.

    Use only when the user explicitly asks to delete the file.

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
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Permanently delete an existing knowledge file by its exact path.

    Safety:
    - confirm must be true.
    - The exact directory path and filename must resolve.
    - ambiguous matches are never deleted.

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
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Delete a directory from an Open WebUI knowledge base.

    The tool moves the files inside the directory to its parent,
    and does not delete them.

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
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Search files across all Open WebUI knowledge bases available
    to the current user.

    By default searches metadata/filenames.
    Set include_content=true to return file content preview for found files.
    max_content_items limits how many files come back with their content (default: 10).

    Note: Open WebUI search API searches by filename, not by file content.
    Full-text search within file content is not supported by the current API.

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
        return "Error: Open WebUI request context is unavailable."
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
            # Some Open WebUI versions do not return the text in the search
            # response. Therefore, with include_content we read it with
            # a separate request.
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
      str(locals().get("page", "") or ""),
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
  ) -> str:
    """
    Find a file across all accessible Open WebUI knowledge bases
    and immediately return its full extracted/indexed text content.

    If the call names exact_filename, prefer an exact filename match.
    If several files still match, return their IDs instead of guessing.

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
        return "Error: Open WebUI request context is unavailable."
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
      str(locals().get("page", "") or ""),
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
  ) -> str:
    """
    Return the complete directory/file tree of an Open WebUI knowledge base.

    Includes directory IDs and file IDs.
    Traverses nested directories recursively.

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
        return "Error: Open WebUI request context is unavailable."
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
      str(locals().get("page", "") or ""),
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
  ) -> str:
    """
    Permanently delete an Open WebUI knowledge base.

    Safety rules:
    - confirm must be true.
    - The tool refuses to delete a knowledge base with files by default.
    - Set allow_nonempty=true only when the user explicitly wants
      a non-empty knowledge base deleted.

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
      return "Error: Open WebUI request context is unavailable."
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
      or f"Knowledge base permanently deleted successfully.\nknowledge_id={knowledge_id}\nname={meta.get('name', 'Unnamed')}"
    )

  async def _list_model_presets(
    self,
    query: str = "",
    page: int = 1,
    __request__=None,
  ) -> str:
    """
    List the workspace model presets of the current Open WebUI user.

    Shows the attachment counts of each preset: knowledge, tools,
    skills, filters and actions.
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
    params = {"page": max(1, page)}
    if query.strip():
      params["query"] = query.strip()
    async with await self._open_session(__request__) as s:
      data, err = await self._request(s, "GET", "/models/list", params=params)
    if err:
      return err
    items = (data or {}).get("items", [])
    if not items:
      return "No model presets found."
    lines = [f"total={(data or {}).get('total')}"]
    for item in items:
      meta = item.get("meta") or {}
      lines.append(
        f"- {item.get('name', 'Unnamed')} (id={item.get('id')}, "
        f"base_model_id={item.get('base_model_id') or 'none'}, "
        f"write_access={item.get('write_access')}, "
        f"knowledge={len(meta.get('knowledge') or [])}, "
        f"tools={len(meta.get('toolIds') or [])}, "
        f"skills={len(meta.get('skillIds') or [])}, "
        f"filters={len(meta.get('filterIds') or [])}, "
        f"actions={len(meta.get('actionIds') or [])})"
      )
    return "\n".join(lines)

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
    """The PRESET_MODELS valve as an id or name list. Empty serves each preset."""
    return _split_ids(self.valves.PRESET_MODELS)

  def _preset_selected(self, item) -> bool:
    """Tell whether PRESET_MODELS serves one preset. Empty serves each preset."""
    wanted = self._preset_filter()
    if not wanted:
      return True
    return str(item.get("id")) in wanted or str(item.get("name")) in wanted

  async def _attach_to_presets(self, add: dict, __request__, remove=None) -> str:
    """Add or remove ids in the meta list of each selected preset.

    One call per list: the caller names the meta key, such as skillIds or
    toolIds. The tool skips a preset without write access and names it. The report
    is `added to N[. skipped, no write access: ids][. failed: msg]`.
    """
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

  async def _update_model_preset(
    self,
    model_id: str = Field(
      ..., description="ID of the workspace model preset to change."
    ),
    add_knowledge_ids: str = "",
    add_tool_ids: str = "",
    add_skill_ids: str = "",
    add_filter_ids: str = "",
    add_action_ids: str = "",
    add_function_ids: str = "",
    remove_knowledge_ids: str = "",
    remove_tool_ids: str = "",
    remove_skill_ids: str = "",
    remove_filter_ids: str = "",
    remove_action_ids: str = "",
    remove_function_ids: str = "",
    __request__=None,
  ) -> str:
    """
    Attach or detach knowledge bases, tools, skills, filters and actions
    on one workspace model preset.

    Each id list is a comma or newline separated string of Open WebUI ids.
    The tool reads the record first and writes it back whole, so the other fields
    and the other meta keys stay.
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
    model = (model_id or "").strip()
    if not model:
      return "Error: model_id is empty. Use list_model_presets to read the ids."
    add, remove = self._preset_changes(
      add_knowledge_ids,
      add_tool_ids,
      add_skill_ids,
      add_filter_ids,
      add_action_ids,
      add_function_ids,
      remove_knowledge_ids,
      remove_tool_ids,
      remove_skill_ids,
      remove_filter_ids,
      remove_action_ids,
      remove_function_ids,
    )
    async with await self._open_session(__request__) as s:
      line, err = await self._write_preset(s, model, add, remove)
    return err or line

  async def _update_all_model_presets(
    self,
    add_knowledge_ids: str = "",
    add_tool_ids: str = "",
    add_skill_ids: str = "",
    add_filter_ids: str = "",
    add_action_ids: str = "",
    add_function_ids: str = "",
    remove_knowledge_ids: str = "",
    remove_tool_ids: str = "",
    remove_skill_ids: str = "",
    remove_filter_ids: str = "",
    remove_action_ids: str = "",
    remove_function_ids: str = "",
    only_writable: bool = True,
    max_models: int = 200,
    __request__=None,
  ) -> str:
    """
    Apply the same attachments to every workspace model preset.

    Reads each page of /models/list, then updates each preset with the
    model function. The tool skips a preset without write access and names it in
    the result, unless only_writable is false. max_models caps the run.
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
    add, remove = self._preset_changes(
      add_knowledge_ids,
      add_tool_ids,
      add_skill_ids,
      add_filter_ids,
      add_action_ids,
      add_function_ids,
      remove_knowledge_ids,
      remove_tool_ids,
      remove_skill_ids,
      remove_filter_ids,
      remove_action_ids,
      remove_function_ids,
    )
    if not any(add.values()) and not any(remove.values()):
      return "Error: no ids given. Pass at least one add or remove list."
    max_models = max(1, min(max_models, 1000))
    lines = []
    seen = 0
    skipped = []
    total = None
    page = 1
    async with await self._open_session(__request__) as s:
      while page <= 100:
        data, err = await self._request(s, "GET", "/models/list", params={"page": page})
        if err:
          lines.append(err)
          break
        if page == 1:
          total = (data or {}).get("total")
        items = (data or {}).get("items") or []
        if not items:
          break
        for item in items:
          if seen >= max_models:
            break
          seen += 1
          model_id = item.get("id")
          if only_writable and not item.get("write_access"):
            skipped.append(str(model_id))
            continue
          line, err = await self._write_preset(s, model_id, add, remove)
          lines.append(err or line)
        if seen >= max_models:
          break
        if total is not None and seen >= int(total):
          break
        page += 1
    result = [f"presets seen={seen}, cap={max_models}"]
    result += lines
    if skipped:
      result.append(f"skipped, no write access: {', '.join(skipped)}")
    return "\n".join(result)

  async def _read(
    self,
    action: str,
    detail: str,
    fn: Callable,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ):
    """Run one read behind the read gate.

    Always ask holds the read at the same confirmation as a mutation. Allow
    reads and Always allow let it run.
    """
    if self._permissions(__user__) == "Always ask" and not await self._ask(
      action, detail, __event_call__, __user__
    ):
      return json.dumps(
        {
          "result": {
            "denied": True,
            "action": action,
            "reason": "Not confirmed, so nothing was read.",
          }
        }
      )
    return await fn()

  # ---------------------------------------------------------------- the files

  async def list_files(
    self,
    page: int = 1,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    List the files of the Open WebUI file library, with their ids and sizes.

    :param page: the page of results, from 1
    """

    async def run(
      page=page,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      async with await self._open_session(__request__) as s:
        data, err = await self._request(
          s, "GET", "/files/", params={"page": max(1, page)}
        )
      if err:
        return err
      items = (data or {}).get("items") or []
      if not items:
        return "No files found."
      lines = [f"total={(data or {}).get('total')}"]
      for item in items:
        meta = item.get("meta") or {}
        lines.append(
          f"- {item.get('filename') or 'Unnamed'} (id={item.get('id')}, "
          f"size={meta.get('size') or 0})"
        )
      return "\n".join(lines)

    return await self._read(
      "read list_files",
      str(locals().get("page", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def search_files(
    self,
    filename: str,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    Search the file library by name. Wildcards such as *.md work.

    :param filename: the filename or part of it to search for
    """

    async def run(
      filename=filename,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      name = (filename or "").strip()
      if not name:
        return "Error: filename is empty."
      async with await self._open_session(__request__) as s:
        data, err = await self._request(
          s, "GET", "/files/search", params={"filename": name}
        )
      if err:
        return err
      items = data if isinstance(data, list) else (data or {}).get("items") or []
      if not items:
        return "No files found."
      return "\n".join(
        f"- {item.get('filename') or 'Unnamed'} (id={item.get('id')})" for item in items
      )

    return await self._read(
      "read search_files",
      str(locals().get("page", "") or ""),
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
  ) -> str:
    """
    Read the stored text content of one file of the library. A read runs free.

    :param file_id: the id of the Open WebUI file to read
    """

    async def run(
      file_id=file_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      async with await self._open_session(__request__) as s:
        data, err = await self._request(s, "GET", f"/files/{file_id}/data/content")
      if err:
        return err
      content = (data or {}).get("content")
      if content is None:
        return "Error: the file has no stored text content."
      return str(content)

    return await self._read(
      "read read_file_content",
      str(locals().get("page", "") or ""),
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
      return "Error: Open WebUI request context is unavailable."
    name = (filename or "").strip() or "untitled.txt"
    boundary = "daedalus-openwebui-manager"
    body = (
      f"--{boundary}\r\n"
      f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
      "Content-Type: text/markdown\r\n\r\n"
      f"{content}\r\n"
      f"--{boundary}--\r\n"
    ).encode()
    async with await self._open_session(__request__) as s:
      data, err = await self._request(
        s,
        "POST",
        "/files/",
        data=body,
        headers={
          "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
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
  ) -> str:
    """
    Rename one file. The rename is a mutation, so it passes the gate.

    :param file_id: the id of the file to rename
    :param name: the new filename, with its extension
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Delete one file of the library. The delete passes the gate.

    :param file_id: the id of the file to delete
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
    refusal = await self._guard("delete a file", str(file_id), __user__, __event_call__)
    if refusal:
      return refusal
    async with await self._open_session(__request__) as s:
      _, err = await self._request(s, "DELETE", f"/files/{file_id}")
    return err or f"deleted file {file_id}"

  # ---------------------------------------------------------------- the skills

  async def list_skills(
    self,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """List the skills of the workspace, with their ids and their state."""

    async def run(
      __request__=__request__, __user__=__user__, __event_call__=__event_call__
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      async with await self._open_session(__request__) as s:
        data, err = await self._request(s, "GET", "/skills/")
      if err:
        return err
      items = (data or {}).get("items") or []
      if not items:
        return "No skills found."
      return "\n".join(
        f"- {item.get('name') or 'Unnamed'} (id={item.get('id')}, "
        f"active={item.get('is_active')})"
        for item in items
      )

    return await self._read(
      "read list_skills",
      str(locals().get("page", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def show_skill(
    self,
    skill_id: str,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    Show one skill: its name, its state and its full source.

    :param skill_id: the id of the skill to read
    """

    async def run(
      skill_id=skill_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      async with await self._open_session(__request__) as s:
        data, err = await self._request(s, "GET", f"/skills/id/{skill_id}")
      if err:
        return err
      if not data:
        return f"Error: skill not found: {skill_id}"
      return (
        f"name={data.get('name')} (id={data.get('id')}, "
        f"active={data.get('is_active')}, description={data.get('description') or ''})\n"
        f"\n{data.get('content') or ''}"
      )

    return await self._read(
      "read show_skill",
      str(locals().get("page", "") or ""),
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
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    Create one skill. A new item runs free, and it joins the preset models.

    :param name: the name of the new skill
    :param content: the full skill text
    :param description: a short description, or empty
    :param skill_id: an id to use, or empty to let Open WebUI build one
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
      return "Error: Open WebUI request context is unavailable."
    identifier = (skill_id or "").strip() or re.sub(
      r"[^a-z0-9]+", "-", (name or "").strip().lower()
    ).strip("-")
    if not identifier:
      return "Error: name is empty, so no skill id can be built."
    body = {
      "id": identifier,
      "name": name,
      "description": description,
      "content": content,
      "meta": {},
      "is_active": True,
    }
    async with await self._open_session(__request__) as s:
      _data, err = await self._request(
        s, "POST", "/skills/create", json=body, expected=(200, 201)
      )
      if err:
        return err
      attach = await self._attach_to_presets({"skillIds": [identifier]}, __request__)
    return f"created skill {name} (id={identifier})\n{attach}"

  async def install_skill(
    self,
    url: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
  ) -> str:
    """
    Install one skill from a URL. The host must be a trusted domain.

    :param url: the URL of the skill file to load
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
      return "Error: Open WebUI request context is unavailable."
    target = (url or "").strip()
    if not target.startswith("https://"):
      return "Error: the URL must start with https://."
    host = target.split("/")[2].lower() if "//" in target else ""
    trusted = [d.strip().lower() for d in _split_ids(self.valves.TRUSTED_DOMAINS)]
    if not any(host == d or host.endswith("." + d) for d in trusted):
      return f"Error: {host} is not a trusted domain."
    try:
      async with (
        aiohttp.ClientSession() as s,
        s.get(
          target,
          timeout=aiohttp.ClientTimeout(
            total=float(self.valves.INSTALL_FETCH_TIMEOUT or 12)
          ),
        ) as response,
      ):
        if response.status != 200:
          return f"Error: the request failed with status {response.status}."
        text = await response.text()
    except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
      return f"Error: the request failed: {exc}"
    name = ""
    for line in text.split("\n"):
      if line.startswith("# "):
        name = line[2:].strip()
        break
    name = name or target.rstrip("/").split("/")[-1].replace(".md", "")
    created = await self.create_skill(
      name, text, f"Installed from {target}", "", __request__
    )
    return created

  async def update_skill(
    self,
    skill_id: str,
    name: str,
    content: str,
    description: str = "",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
  ) -> str:
    """
    Replace one skill. The overwrite passes the gate.

    :param skill_id: the id of the skill to change
    :param name: the new name
    :param content: the new full skill text
    :param description: the new description, or empty
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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

  async def toggle_skill(
    self,
    skill_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
    __request__=None,
  ) -> str:
    """
    Switch one skill on or off. The toggle passes the gate.

    :param skill_id: the id of the skill to turn on or off
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Delete one skill. The delete passes the gate.

    :param skill_id: the id of the skill to delete
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """List the Workspace Tools, with their ids."""

    async def run(
      __request__=__request__, __user__=__user__, __event_call__=__event_call__
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      async with await self._open_session(__request__) as s:
        data, err = await self._request(s, "GET", "/tools/")
      if err:
        return err
      items = data if isinstance(data, list) else (data or {}).get("items") or []
      if not items:
        return "No tools found."
      return "\n".join(
        f"- {item.get('name') or 'Unnamed'} (id={item.get('id')})" for item in items
      )

    return await self._read(
      "read list_tools",
      str(locals().get("page", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def show_tool(
    self,
    tool_id: str,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    Show one Workspace Tool: its name, its spec and its source.

    :param tool_id: the id of the tool to read
    """

    async def run(
      tool_id=tool_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
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
      str(locals().get("page", "") or ""),
      run,
      __user__,
      __event_call__,
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
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Replace one Workspace Tool. The overwrite passes the gate.

    :param tool_id: the id of the tool to change
    :param name: the new name
    :param content: the new full Python source
    :param description: the new description, or empty
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Toggle one Workspace Tool on the preset models.

    Open WebUI v0.11.4 has no per-tool on and off switch, so this toggles the
    tool id in the toolIds list of each preset the caller can write: a tool in
    every preset leaves them, and a tool in no preset joins them.

    :param tool_id: the id of the tool to turn on or off
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Delete one Workspace Tool. The delete passes the gate.

    :param tool_id: the id of the tool to delete
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """List the Functions, with their ids and their types."""

    async def run(
      __request__=__request__, __user__=__user__, __event_call__=__event_call__
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
      async with await self._open_session(__request__) as s:
        data, err = await self._request(s, "GET", "/functions/")
      if err:
        return err
      items = data if isinstance(data, list) else (data or {}).get("items") or []
      if not items:
        return "No functions found."
      return "\n".join(
        f"- {item.get('name') or 'Unnamed'} (id={item.get('id')}, "
        f"type={item.get('type')}, active={item.get('is_active')}, "
        f"global={item.get('is_global')})"
        for item in items
      )

    return await self._read(
      "read list_functions",
      str(locals().get("page", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def show_function(
    self,
    function_id: str,
    __request__=None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> str:
    """
    Show one Function: its name, its state and its source.

    :param function_id: the id of the function to read
    """

    async def run(
      function_id=function_id,
      __request__=__request__,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      if __request__ is None:
        return "Error: Open WebUI request context is unavailable."
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
      str(locals().get("page", "") or ""),
      run,
      __user__,
      __event_call__,
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
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Replace one Function. The overwrite passes the gate.

    :param function_id: the id of the function to change
    :param name: the new name
    :param content: the new full Python source
    :param description: the new description, or empty
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Switch one Function on or off. The toggle passes the gate.

    :param function_id: the id of the function to turn on or off
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
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
  ) -> str:
    """
    Delete one Function. The delete passes the gate.

    :param function_id: the id of the function to delete
    """
    if __request__ is None:
      return "Error: Open WebUI request context is unavailable."
    refusal = await self._guard(
      "delete a function", str(function_id), __user__, __event_call__
    )
    if refusal:
      return refusal
    async with await self._open_session(__request__) as s:
      _, err = await self._request(s, "DELETE", f"/functions/id/{function_id}/delete")
    return err or f"deleted function {function_id}"
