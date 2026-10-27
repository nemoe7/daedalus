"""
title: GitHub
author: nemo
description: GitHub access for Open WebUI. Read and write approval follows permission mode.
required_open_webui_version: 0.10.0
version: 3.0.13
licence: nemoe7 Noncommercial and Public-Interest License 1.2.0
"""

# fmt: off

import asyncio
import base64
import inspect
import json
import os
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

API = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"
USER_AGENT = "daedalus-openwebui-github"
JSON_ACCEPT = "application/vnd.github+json"


class GitHubError(Exception):
  """Report a GitHub failure with status, API message, method, and URL."""

  def __init__(self, status: int, message: str, method: str = "", url: str = ""):
    self.status = status
    self.message = message
    self.method = method
    self.url = url
    super().__init__(f"GitHub {status} on {method} {url}: {message}")


def _bounded(method):
  signature = inspect.signature(method)

  @wraps(method)
  async def call(self, *args, **kwargs):
    try:
      value = await method(self, *args, **kwargs)
    except Exception as error:
      value = {"error": str(error)}
      if self._output_size(value) <= self.valves.max_output_bytes:
        raise
    arguments = signature.bind(self, *args, **kwargs).arguments
    return self._output(
      value, method.__annotations__.get("return") in (str, "str"), arguments
    )

  return call


def _bounded_tools(cls):
  for name, method in list(vars(cls).items()):
    if not name.startswith("_") and inspect.iscoroutinefunction(method):
      setattr(cls, name, _bounded(method))
  return cls


@_bounded_tools
class Tools:
  class Valves(BaseModel):
    max_output_bytes: int = Field(
      default=32768, ge=1024, description="Maximum UTF-8 bytes in one tool result."
    )
    github_token: str = ""
    permissions: Literal["Always ask", "Allow reads", "Always allow"] = "Always ask"
    timeout_seconds: int = 60
    http_timeout_seconds: int = 30
    api_version: str = "2022-11-28"
    max_files_per_commit: int = 30
    max_tree_entries: int = 2000

  class UserValves(BaseModel):
    mode: Literal["default", "Always ask", "Allow reads", "Always allow"] = "default"
    timeout_seconds: int = 0

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
      "message": "Use a smaller page or text range. Do not repeat a write to recover output.",
      "identifiers": {},
    }
    pending = [arguments or {}]
    if isinstance(value, (dict, list)):
      pending.append(value)
    keys = {
      "id",
      "sha",
      "tree_sha",
      "node_id",
      "html_url",
      "url",
      "file_id",
      "directory_id",
      "thread_id",
      "message_id",
      "document_id",
      "calendar_id",
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
      row = pending.pop(0)
      if isinstance(row, list):
        pending.extend(item for item in row[:100] if isinstance(item, (dict, list)))
        continue
      for key, item in row.items():
        if (
          key in keys
          and key not in result["identifiers"]
          and isinstance(item, (str, int))
          and not isinstance(item, bool)
          and item != ""
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
      raise ValueError(
        "Output metadata leaves no room for text. Use a smaller result."
      )
    return page(lower)

  def _list_page(
    self,
    payload: dict,
    field: str,
    items: list,
    offset: int,
    max_results: int,
    *,
    cap: int = 200,
    more: bool = False,
  ) -> dict:
    start = min(max(0, int(offset)), len(items))
    upper = min(max(1, int(max_results)), cap, len(items) - start)

    def page(count):
      end = start + count
      return {
        **payload,
        field: items[start:end],
        "count": count,
        "total_count": len(items),
        "offset": start,
        "next_offset": end if end < len(items) else None,
        "truncated": more or end < len(items),
      }

    lower = 0
    while lower < upper:
      middle = (lower + upper + 1) // 2
      if self._output_size({"result": page(middle)}) <= self.valves.max_output_bytes:
        lower = middle
      else:
        upper = middle - 1
    if lower == 0 and start < len(items):
      return page(1)
    return page(lower)

  def __init__(self):
    self.valves = self.Valves()

  # ---------------------------------------------------------------- gate

  @staticmethod
  def _user_valve(__user__: dict | None, name: str) -> Any:
    """Read one user valve from a model or dict."""
    valves = (__user__ or {}).get("valves") or {}
    value = getattr(valves, name, None)
    if value is None and isinstance(valves, dict):
      value = valves.get(name)
    return value

  def _permissions(self, __user__: dict | None = None) -> str:
    """Resolve permission mode, requiring confirmation for unknown values."""
    user_mode = self._user_valve(__user__, "mode")
    mode = (
      self.valves.permissions
      if user_mode is None or user_mode == "default"
      else user_mode
    )
    return (
      mode
      if isinstance(mode, str)
      and mode in ("Always ask", "Allow reads", "Always allow")
      else "Always ask"
    )

  def _wait_seconds(self, __user__: dict | None = None) -> int:
    """Return nonzero user timeout, tool timeout, or 60 seconds, minimum 1."""
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
    """Request confirmation within configured wait and accept only `True`."""
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

  async def _read(
    self,
    action: str,
    detail: str,
    fn: Callable,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> Any:
    """Run read after confirmation in `Always ask` mode, or directly otherwise."""
    if self._permissions(__user__) == "Always ask" and not await self._ask(
      action, detail, __event_call__, __user__
    ):
      return {
        "result": {
          "denied": True,
          "action": action,
          "reason": "You did not confirm, so I read nothing.",
        }
      }
    return await fn()

  async def _write(
    self,
    action: str,
    detail: str,
    fn: Callable,
    *,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> Any:
    """Run write only with `Always allow` permission or explicit confirmation."""
    if self._permissions(__user__) != "Always allow" and not await self._ask(
      action, detail, __event_call__, __user__
    ):
      return {
        "result": {
          "denied": True,
          "action": action,
          "reason": (
            "You did not confirm, so I sent nothing. A missing dialog, a closed tab, "
            f"or an answer later than {self._wait_seconds(__user__)}s all read as no."
          ),
        }
      }
    return await fn()

  # ---------------------------------------------------------------- http

  def _token(self) -> str:
    token = (self.valves.github_token or os.environ.get("GITHUB_TOKEN", "")).strip()
    if not token:
      raise GitHubError(
        401, "No GitHub token: set it in tool valves or GITHUB_TOKEN.", "", ""
      )
    return token

  def _headers(self, accept: str = JSON_ACCEPT) -> dict:
    return {
      "Authorization": f"Bearer {self._token()}",
      "Accept": accept,
      "X-GitHub-Api-Version": self.valves.api_version,
      "User-Agent": USER_AGENT,
    }

  def _http(
    self,
    method: str,
    url: str,
    params: dict | None = None,
    payload: dict | None = None,
    accept: str = JSON_ACCEPT,
    raw: bool = False,
  ) -> Any:
    """Make a blocking HTTP request for callers running in a worker thread."""
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    if clean:
      url = f"{url}?{urlencode(clean, doseq=True)}"
    data = None
    if payload is not None:
      data = json.dumps({k: v for k, v in payload.items() if v is not None}).encode()
    request = Request(url, data=data, method=method, headers=self._headers(accept))
    try:
      with urlopen(request, timeout=self.valves.http_timeout_seconds) as response:
        body = response.read().decode("utf-8", "replace")
    except HTTPError as error:
      body = error.read().decode("utf-8", "replace")
      try:
        message = json.loads(body).get("message", body)
      except Exception:
        message = body.strip() or error.reason
      raise GitHubError(error.code, str(message), method, url) from None
    except URLError as error:
      raise GitHubError(0, str(error.reason), method, url) from None
    if raw:
      return body
    if not body.strip():
      return {}
    try:
      return json.loads(body)
    except json.JSONDecodeError:
      return {"content": body}

  async def _request(
    self,
    method: str,
    url: str,
    params: dict | None = None,
    payload: dict | None = None,
    accept: str = JSON_ACCEPT,
    raw: bool = False,
  ) -> Any:
    return await asyncio.to_thread(
      self._http, method, url, params, payload, accept, raw
    )

  async def _graphql(self, query: str, variables: dict) -> dict:
    data = await self._request(
      "POST", GRAPHQL_URL, payload={"query": query, "variables": variables}
    )
    if data.get("errors"):
      raise GitHubError(
        422,
        ". ".join(e.get("message", "") for e in data["errors"]),
        "POST",
        GRAPHQL_URL,
      )
    return data.get("data") or {}

  @staticmethod
  def _ok(payload: Any) -> dict:
    return {"result": payload}

  @staticmethod
  def _repository_summary(item: dict) -> dict:
    description = item.get("description") or ""
    return {
      "id": item.get("id"),
      "node_id": item.get("node_id"),
      "full_name": item.get("full_name"),
      "html_url": item.get("html_url"),
      "description": description[:160],
      "description_truncated": len(description) > 160,
      "private": item.get("private"),
      "visibility": item.get("visibility"),
      "default_branch": item.get("default_branch"),
      "permissions": item.get("permissions"),
      "archived": item.get("archived"),
      "disabled": item.get("disabled"),
    }

  @staticmethod
  def _issue_summary(item: dict) -> dict:
    title = item.get("title") or ""
    return {
      "id": item.get("id"),
      "node_id": item.get("node_id"),
      "number": item.get("number"),
      "repository_url": item.get("repository_url"),
      "html_url": item.get("html_url"),
      "title": title[:200],
      "title_truncated": len(title) > 200,
      "state": item.get("state"),
      "state_reason": item.get("state_reason"),
      "author": (item.get("user") or {}).get("login"),
      "author_association": item.get("author_association"),
      "locked": item.get("locked"),
      "labels": [
        label.get("name") if isinstance(label, dict) else label
        for label in item.get("labels") or []
      ],
      "created_at": item.get("created_at"),
      "updated_at": item.get("updated_at"),
      "is_pull_request": "pull_request" in item,
      "draft": item.get("draft"),
    }

  @staticmethod
  def _commit_summary(item: dict) -> dict:
    commit = item.get("commit") or {}
    author = commit.get("author") or {}
    subject = (commit.get("message") or "").partition("\n")[0]
    return {
      "sha": item.get("sha"),
      "html_url": item.get("html_url"),
      "message": subject[:200],
      "message_truncated": len(subject) > 200
      or "\n" in (commit.get("message") or ""),
      "author": author.get("name"),
      "date": author.get("date"),
    }

  @staticmethod
  def _tree_entry(item: dict) -> dict:
    return {
      key: item[key]
      for key in ("path", "type", "sha", "size")
      if key in item and item[key] is not None
    }

  # ------------------------------------------------------------- pointers

  @staticmethod
  def _seg(value: str) -> str:
    return quote(str(value), safe="")

  @classmethod
  def _repo(cls, repository_full_name: str) -> str:
    full = (repository_full_name or "").strip().strip("/")
    if not full or full.count("/") != 1:
      raise GitHubError(422, "repository_full_name must be 'owner/repo'.", "", "")
    owner, name = full.split("/")
    return f"/repos/{cls._seg(owner)}/{cls._seg(name)}"

  @classmethod
  def _repo_of(
    cls,
    repository_full_name: str | None = None,
    repository_id: int | None = None,
    repository_url: str | None = None,
  ) -> str:
    if repository_full_name:
      return cls._repo(repository_full_name)
    if repository_id:
      return f"/repositories/{int(repository_id)}"
    if repository_url:
      parts = [p for p in urlparse(repository_url).path.split("/") if p]
      if parts and parts[0] == "repos":
        parts = parts[1:]
      if len(parts) >= 2:
        return cls._repo(f"{parts[0]}/{parts[1]}")
      raise GitHubError(422, f"Cannot parse repo from {repository_url}.", "", "")
    raise GitHubError(
      422, "Give one repo selector: full name, ID, or URL.", "", ""
    )

  @staticmethod
  def _path(value: str) -> str:
    return quote(str(value).lstrip("/"), safe="/")

  @staticmethod
  def _issue_shape(issue: dict) -> dict:
    return {
      "issue": issue,
      "url": issue.get("html_url"),
      "title": issue.get("title"),
    }

  async def _pr_node_id(self, repository_full_name: str, pr_number: int) -> str:
    owner, name = repository_full_name.strip().strip("/").split("/")
    data = await self._graphql(
      """
      query($owner: String!, $name: String!, $number: Int!) {
        repository(owner: $owner, name: $name) {
          pullRequest(number: $number) { id }
        }
      }
      """,
      {"owner": owner, "name": name, "number": int(pr_number)},
    )
    node = ((data.get("repository") or {}).get("pullRequest") or {}).get("id")
    if not node:
      raise GitHubError(
        404,
        f"PR #{pr_number} not found in {repository_full_name}.",
        "POST",
        GRAPHQL_URL,
      )
    return node

  # ═══════════════════════════════ issues & labels ══════════════════════

  async def fetch_issue(
    self,
    issue_number: int,
    repository_full_name: str | None = None,
    repository_id: int | None = None,
    repository_url: str | None = None,
    include_comments: bool = False,
    comments_only: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read a GitHub issue, its comments, or both.

    :param issue_number: issue number
    :param repository_full_name: owner/repo
    :param repository_id: numeric repo ID
    :param repository_url: repo HTML or API URL
    :param include_comments: include first 100 issue comments with issue data
    :param comments_only: return only first 100 issue comments
    """

    async def run():
      repo = self._repo_of(repository_full_name, repository_id, repository_url)
      number = int(issue_number)
      if comments_only:
        comments = await self._request(
          "GET", f"{API}{repo}/issues/{number}/comments", params={"per_page": 100}
        )
        return self._ok({"comments": comments})
      issue = await self._request("GET", f"{API}{repo}/issues/{number}")
      payload = self._issue_shape(issue)
      if include_comments:
        payload["comments"] = await self._request(
          "GET", f"{API}{repo}/issues/{number}/comments", params={"per_page": 100}
        )
      return self._ok(payload)

    return await self._read(
      "read fetch_issue",
      str({
        "issue_number": issue_number,
        "repository_full_name": repository_full_name,
        "repository_id": repository_id,
        "repository_url": repository_url,
        "include_comments": include_comments,
        "comments_only": comments_only,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def list_recent_issues(
    self,
    top_k: int = 20,
    offset: int = 0,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List recent GitHub issues for the signed-in user, newest update first.

    :param top_k: maximum issues to return, up to 100
    :param offset: item offset within the fetched recent issues
    """

    async def run():
      limit = min(max(1, int(top_k)), 100)
      issues = await self._request(
        "GET",
        f"{API}/issues",
        params={
          "filter": "all",
          "state": "all",
          "sort": "updated",
          "direction": "desc",
          "per_page": limit,
        },
      )
      rows = [self._issue_summary(item) for item in issues]
      return self._ok(self._list_page({}, "issues", rows, offset, limit, cap=100))

    return await self._read(
      "read list_recent_issues",
      str({"top_k": top_k, "offset": offset}),
      run,
      __user__,
      __event_call__,
    )

  async def create_issue(
    self,
    repository_full_name: str,
    title: str,
    body: str | None = None,
    assignees: list[str] | None = None,
    labels: list[str] | None = None,
    milestone: int | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create a GitHub issue.

    :param repository_full_name: owner/repo
    :param title: issue title
    :param body: Markdown body
    :param assignees: usernames to assign
    :param labels: label names
    :param milestone: milestone number
    """
    repo = self._repo(repository_full_name)

    async def run():
      issue = await self._request(
        "POST",
        f"{API}{repo}/issues",
        payload={
          "title": title,
          "body": body,
          "assignees": assignees,
          "labels": labels,
          "milestone": milestone,
        },
      )
      return self._ok(self._issue_shape(issue))

    return await self._write(
      f"open issue '{title}' in {repository_full_name}",
      body or title,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def update_issue(
    self,
    repository_full_name: str,
    issue_number: int,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    state_reason: str | None = None,
    assignees: list[str] | None = None,
    labels: list[str] | None = None,
    milestone: int | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Edit a GitHub issue, replacing labels or assignees when supplied.

    :param repository_full_name: owner/repo
    :param issue_number: issue number
    :param title: new title
    :param body: new Markdown body
    :param state: open or closed
    :param state_reason: completed, not_planned, duplicate, or reopened
    :param assignees: replacement usernames
    :param labels: replacement label names
    :param milestone: milestone number, 0 clears
    """
    repo = self._repo(repository_full_name)

    async def run():
      issue = await self._request(
        "PATCH",
        f"{API}{repo}/issues/{int(issue_number)}",
        payload={
          "title": title,
          "body": body,
          "state": state,
          "state_reason": state_reason,
          "assignees": assignees,
          "labels": labels,
          "milestone": milestone,
        },
      )
      return self._ok(self._issue_shape(issue))

    return await self._write(
      f"update issue #{issue_number} in {repository_full_name}",
      f"title={title!r} state={state!r} labels={labels!r}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def write_issue_comment(
    self,
    repo_full_name: str,
    comment: str,
    issue_number: int | None = None,
    comment_id: int | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create a top-level issue or PR comment, or edit an existing comment.

    :param repo_full_name: owner/repo
    :param comment: Markdown comment body
    :param issue_number: issue or PR number when adding a comment
    :param comment_id: existing comment ID when editing a comment
    """
    repo = self._repo(repo_full_name)
    if (issue_number is None) == (comment_id is None):
      raise GitHubError(
        422, "Give exactly one of issue_number or comment_id.", "POST", ""
      )

    async def run():
      if comment_id is not None:
        data = await self._request(
          "PATCH",
          f"{API}{repo}/issues/comments/{int(comment_id)}",
          payload={"body": comment},
        )
        return self._ok(data)
      data = await self._request(
        "POST",
        f"{API}{repo}/issues/{int(issue_number)}/comments",
        payload={"body": comment},
      )
      return self._ok(data)

    action = (
      f"edit comment {comment_id} in {repo_full_name}"
      if comment_id is not None
      else f"comment on #{issue_number} in {repo_full_name}"
    )
    return await self._write(
      action,
      comment,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def edit_assignees(
    self,
    repository_full_name: str,
    issue_number: int,
    assignees: list[str],
    action: Literal["add", "remove"] = "add",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Add or remove assignees on a GitHub issue or PR.

    :param repository_full_name: owner/repo
    :param issue_number: issue or PR number
    :param assignees: usernames to add or remove
    :param action: add or remove
    """
    repo = self._repo(repository_full_name)
    if action not in ("add", "remove"):
      raise GitHubError(422, "action must be 'add' or 'remove'.", "POST", "")

    async def run():
      method = "POST" if action == "add" else "DELETE"
      issue = await self._request(
        method,
        f"{API}{repo}/issues/{int(issue_number)}/assignees",
        payload={"assignees": assignees},
      )
      return self._ok(self._issue_shape(issue))

    return await self._write(
      f"{action} assignees {assignees} on #{issue_number} in {repository_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def edit_labels(
    self,
    repository_full_name: str,
    issue_number: int,
    labels: list[str] | str,
    action: Literal["add", "remove"] = "add",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Add or remove labels on a GitHub issue or PR.

    :param repository_full_name: owner/repo
    :param issue_number: issue or PR number
    :param labels: label name or list of label names
    :param action: add or remove
    """
    repo = self._repo(repository_full_name)
    names = [labels] if isinstance(labels, str) else list(labels)
    if not names:
      raise GitHubError(422, "Give at least 1 label.", "POST", "")
    if action not in ("add", "remove"):
      raise GitHubError(422, "action must be 'add' or 'remove'.", "POST", "")

    async def run():
      if action == "add":
        data = await self._request(
          "POST",
          f"{API}{repo}/issues/{int(issue_number)}/labels",
          payload={"labels": names},
        )
        return self._ok({"success": True, "labels": data})
      last: Any = {}
      for label in names:
        last = await self._request(
          "DELETE",
          f"{API}{repo}/issues/{int(issue_number)}/labels/{self._path(label)}",
        )
      return self._ok({"success": True, "removed": names, "labels": last})

    return await self._write(
      f"{action} labels {names} on #{issue_number} in {repository_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # ═════════════════════════ PRs & reviews ════════════════════

  async def fetch_pr(
    self,
    repo_full_name: str,
    pr_number: int,
    include_diff: bool = True,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read a GitHub PR with optional unified diff.

    :param repo_full_name: owner/repo
    :param pr_number: PR number
    :param include_diff: include unified diff when true, or metadata only when false
    """

    async def run():
      repo = self._repo(repo_full_name)
      pull = await self._request("GET", f"{API}{repo}/pulls/{int(pr_number)}")
      if not include_diff:
        return self._ok(pull)
      diff = await self._request(
        "GET",
        f"{API}{repo}/pulls/{int(pr_number)}",
        accept="application/vnd.github.diff",
        raw=True,
      )
      return self._ok(
        {
          "pull_request": pull,
          "url": pull.get("html_url"),
          "title": pull.get("title"),
          "display_url": pull.get("html_url"),
          "display_title": f"#{pr_number} {pull.get('title')}",
          "diff": diff,
        }
      )

    return await self._read(
      "read fetch_pr",
      str({
        "repo_full_name": repo_full_name,
        "pr_number": pr_number,
        "include_diff": include_diff,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def fetch_pr_comments(
    self,
    repo_full_name: str,
    pr_number: int,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read PR discussion comments, inline review comments, and reviews.

    :param repo_full_name: owner/repo
    :param pr_number: PR number
    """

    async def run():
      repo = self._repo(repo_full_name)
      number = int(pr_number)
      issue_comments = await self._request(
        "GET", f"{API}{repo}/issues/{number}/comments", params={"per_page": 100}
      )
      review_comments = await self._request(
        "GET", f"{API}{repo}/pulls/{number}/comments", params={"per_page": 100}
      )
      reviews = await self._request(
        "GET", f"{API}{repo}/pulls/{number}/reviews", params={"per_page": 100}
      )
      comments = (
        [{"type": "issue_comment", **c} for c in issue_comments]
        + [{"type": "review_comment", **c} for c in review_comments]
        + [{"type": "review", **r} for r in reviews]
      )
      return self._ok({"comments": comments})

    return await self._read(
      "read fetch_pr_comments",
      str({"repo_full_name": repo_full_name, "pr_number": pr_number}),
      run,
      __user__,
      __event_call__,
    )

  async def fetch_pr_diff(
    self,
    repo_full_name: str,
    pr_number: int,
    mode: Literal["diff", "patch", "files", "filenames", "file"] = "diff",
    path: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read a PR unified diff, patch, changed files, filenames, or one file patch.

    :param repo_full_name: owner/repo
    :param pr_number: PR number
    :param mode: diff, patch, files, filenames, or file
    :param path: changed file path when reading one file patch
    """
    if mode not in ("diff", "patch", "files", "filenames", "file"):
      raise GitHubError(
        422,
        "mode must be 'diff', 'patch', 'files', 'filenames', or 'file'.",
        "GET",
        "",
      )
    if mode == "file" and not path:
      raise GitHubError(422, "Give path when mode is 'file'.", "GET", "")

    async def run():
      repo = self._repo(repo_full_name)
      number = int(pr_number)
      if path or mode == "file":
        for page in (1, 2, 3):
          files = await self._request(
            "GET",
            f"{API}{repo}/pulls/{number}/files",
            params={"per_page": 100, "page": page},
          )
          for entry in files:
            if entry.get("filename") == path:
              return self._ok(
                {
                  "patch": {
                    "filename": entry.get("filename"),
                    "patch": entry.get("patch"),
                    "url": entry.get("blob_url"),
                    "title": entry.get("filename"),
                  }
                }
              )
          if len(files) < 100:
            break
        return self._ok({"patch": None})
      if mode in ("diff", "patch"):
        media = (
          "application/vnd.github.patch"
          if mode == "patch"
          else "application/vnd.github.diff"
        )
        diff = await self._request(
          "GET", f"{API}{repo}/pulls/{number}", accept=media, raw=True
        )
        return self._ok({"diff": diff})
      files = await self._request(
        "GET", f"{API}{repo}/pulls/{number}/files", params={"per_page": 100}
      )
      if mode == "filenames":
        return self._ok({"filenames": [f.get("filename") for f in files]})
      patches = [
        {
          "filename": f.get("filename"),
          "patch": f.get("patch"),
          "url": f.get("blob_url"),
          "title": f.get("filename"),
          "status": f.get("status"),
          "additions": f.get("additions"),
          "deletions": f.get("deletions"),
        }
        for f in files
      ]
      return self._ok({"patches": patches})

    return await self._read(
      "read fetch_pr_diff",
      str({
        "repo_full_name": repo_full_name,
        "pr_number": pr_number,
        "mode": mode,
        "path": path,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def create_pull_request(
    self,
    repository_full_name: str,
    title: str | None = None,
    body: str | None = None,
    head_branch: str | None = None,
    base_branch: str | None = None,
    draft: bool = False,
    head: str | None = None,
    base: str | None = None,
    issue: int | None = None,
    head_repo: str | None = None,
    maintainer_can_modify: bool | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Open a GitHub PR with a title or existing issue number.

    :param repository_full_name: owner/repo
    :param title: PR title
    :param body: Markdown body
    :param head_branch: branch holding changes
    :param base_branch: target base branch
    :param draft: open as draft
    :param head: head as owner:branch for fork PRs
    :param base: base ref without base_branch
    :param issue: issue number to convert into a PR
    :param head_repo: fork owner or full name paired with head_branch
    :param maintainer_can_modify: allow maintainer edits
    """
    repo = self._repo(repository_full_name)
    head_ref = head or head_branch
    if head_ref and head_repo and ":" not in head_ref:
      head_ref = f"{head_repo}:{head_ref}"

    async def run():
      pull = await self._request(
        "POST",
        f"{API}{repo}/pulls",
        payload={
          "title": title,
          "body": body,
          "head": head_ref,
          "base": base or base_branch,
          "draft": draft,
          "issue": issue,
          "maintainer_can_modify": maintainer_can_modify,
        },
      )
      return self._ok(pull)

    return await self._write(
      f"open PR in {repository_full_name}",
      f"head={head_ref} base={base or base_branch} title={title!r}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def update_pull_request(
    self,
    repository_full_name: str,
    pr_number: int,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    base_branch: str | None = None,
    maintainer_can_modify: bool | None = None,
    draft: bool | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Edit a GitHub PR or toggle its draft status.

    :param repository_full_name: owner/repo
    :param pr_number: PR number
    :param title: new title
    :param body: new Markdown body
    :param state: open or closed
    :param base_branch: new base branch
    :param maintainer_can_modify: allow maintainer edits
    :param draft: true converts to draft, false marks ready for review
    """
    repo = self._repo(repository_full_name)
    has_rest = any(
      item is not None
      for item in (title, body, state, base_branch, maintainer_can_modify)
    )

    async def run():
      pull = None
      if has_rest or draft is None:
        pull = await self._request(
          "PATCH",
          f"{API}{repo}/pulls/{int(pr_number)}",
          payload={
            "title": title,
            "body": body,
            "state": state,
            "base": base_branch,
            "maintainer_can_modify": maintainer_can_modify,
          },
        )
      if draft is None:
        return self._ok(pull)
      node = await self._pr_node_id(repository_full_name, pr_number)
      if draft:
        data = await self._graphql(
          """
          mutation($id: ID!) {
            convertPullRequestToDraft(input: {pullRequestId: $id}) {
              pullRequest { id isDraft url title }
            }
          }
          """,
          {"id": node},
        )
        draft_pull = (
          (data.get("convertPullRequestToDraft") or {}).get("pullRequest") or {}
        )
      else:
        data = await self._graphql(
          """
          mutation($id: ID!) {
            markPullRequestReadyForReview(input: {pullRequestId: $id}) {
              pullRequest { id isDraft url title }
            }
          }
          """,
          {"id": node},
        )
        draft_pull = (
          (data.get("markPullRequestReadyForReview") or {}).get("pullRequest") or {}
        )
      if pull is None:
        return self._ok(draft_pull)
      return self._ok({**pull, "draft_state": draft_pull})

    return await self._write(
      f"update PR #{pr_number} in {repository_full_name}",
      f"title={title!r} state={state!r} base={base_branch!r} draft={draft!r}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def merge_pull_request(
    self,
    repository_full_name: str,
    pr_number: int,
    merge_method: str | None = None,
    commit_title: str | None = None,
    commit_message: str | None = None,
    expected_head_sha: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Merge a GitHub PR.

    :param repository_full_name: owner/repo
    :param pr_number: PR number
    :param merge_method: merge, squash, or rebase
    :param commit_title: commit title
    :param commit_message: commit body
    :param expected_head_sha: required current head SHA before merging
    """
    repo = self._repo(repository_full_name)

    async def run():
      data = await self._request(
        "PUT",
        f"{API}{repo}/pulls/{int(pr_number)}/merge",
        payload={
          "merge_method": merge_method,
          "commit_title": commit_title,
          "commit_message": commit_message,
          "sha": expected_head_sha,
        },
      )
      return self._ok(data)

    return await self._write(
      f"merge PR #{pr_number} in {repository_full_name}",
      f"method={merge_method or 'merge'} expected_head={expected_head_sha or 'any'}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def create_pr_with_files(
    self,
    repository_full_name: str,
    files: list[dict],
    branch: str,
    title: str | None = None,
    body: str | None = None,
    base: str | None = None,
    commit_message: str | None = None,
    draft: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Commit file changes to a GitHub branch and open a PR when new.

    :param repository_full_name: owner/repo
    :param files: changes as {path, content} or {path, delete: true}
    :param branch: branch to create or move
    :param title: PR title, defaults to branch name
    :param body: PR body
    :param base: base branch, defaults to repo default branch
    :param commit_message: commit message, defaults to title
    :param draft: open PR as draft
    """
    repo = self._repo(repository_full_name)
    if not files:
      raise GitHubError(422, "Give at least 1 file.", "POST", "")
    if len(files) > int(self.valves.max_files_per_commit):
      raise GitHubError(
        422, f"At most {self.valves.max_files_per_commit} files per call.", "POST", ""
      )
    paths = [str(item.get("path") or "") for item in files]
    if not all(paths):
      raise GitHubError(422, "Every file needs a path.", "POST", "")

    async def run():
      target = base
      if not target:
        info = await self._request("GET", f"{API}{repo}")
        target = info.get("default_branch") or "main"
      parent_sha = None
      updated = False
      try:
        ref = await self._request(
          "GET", f"{API}{repo}/git/ref/heads/{self._path(branch)}"
        )
        parent_sha = (ref.get("object") or {}).get("sha")
        updated = True
      except GitHubError as error:
        if error.status != 404:
          raise
      if not parent_sha:
        base_ref = await self._request(
          "GET", f"{API}{repo}/git/ref/heads/{self._path(target)}"
        )
        parent_sha = (base_ref.get("object") or {}).get("sha")
      parent = await self._request("GET", f"{API}{repo}/git/commits/{parent_sha}")
      entries = []
      for item in files:
        if item.get("delete"):
          entries.append(
            {"path": item.get("path"), "mode": "100644", "type": "blob", "sha": None}
          )
          continue
        blob = await self._request(
          "POST",
          f"{API}{repo}/git/blobs",
          payload={
            "content": base64.b64encode(
              str(item.get("content") or "").encode()
            ).decode(),
            "encoding": "base64",
          },
        )
        entries.append(
          {
            "path": item.get("path"),
            "mode": "100644",
            "type": "blob",
            "sha": blob.get("sha"),
          }
        )
      tree = await self._request(
        "POST",
        f"{API}{repo}/git/trees",
        payload={"base_tree": (parent.get("tree") or {}).get("sha"), "tree": entries},
      )
      commit = await self._request(
        "POST",
        f"{API}{repo}/git/commits",
        payload={
          "message": commit_message or title or "Update files",
          "tree": tree.get("sha"),
          "parents": [parent_sha],
        },
      )
      if updated:
        await self._request(
          "PATCH",
          f"{API}{repo}/git/refs/heads/{self._path(branch)}",
          payload={"sha": commit.get("sha")},
        )
        pulls = await self._request(
          "GET",
          f"{API}{repo}/pulls",
          params={
            "head": f"{repository_full_name.split('/')[0]}:{branch}",
            "state": "open",
          },
        )
        pull = pulls[0] if pulls else None
      else:
        await self._request(
          "POST",
          f"{API}{repo}/git/refs",
          payload={"ref": f"refs/heads/{branch}", "sha": commit.get("sha")},
        )
        pull = await self._request(
          "POST",
          f"{API}{repo}/pulls",
          payload={
            "title": title or branch,
            "body": body,
            "head": branch,
            "base": target,
            "draft": draft,
          },
        )
      return self._ok(
        {
          "pull_request": pull,
          "branch": branch,
          "base": target,
          "commit_sha": commit.get("sha"),
          "tree_sha": tree.get("sha"),
          "updated": updated,
          "files": paths,
        }
      )

    return await self._write(
      f"write {len(paths)} file(s) to {branch} in {repository_full_name}",
      ", ".join(paths[:10]),
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def list_pull_request_reviews(
    self,
    repo_full_name: str,
    pr_number: int,
    target: Literal["reviews", "threads", "both"] = "reviews",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List submitted PR reviews, inline review threads, or both.

    :param repo_full_name: owner/repo
    :param pr_number: PR number
    :param target: reviews, threads, or both
    """
    if target not in ("reviews", "threads", "both"):
      raise GitHubError(
        422, "target must be 'reviews', 'threads', or 'both'.", "GET", ""
      )

    async def run():
      self._repo(repo_full_name)
      owner, name = repo_full_name.strip().strip("/").split("/")
      result: dict[str, Any] = {}
      if target in ("reviews", "both"):
        data = await self._graphql(
          """
          query($owner: String!, $name: String!, $number: Int!) {
            repository(owner: $owner, name: $name) {
              pullRequest(number: $number) {
                reviews(first: 100) {
                  nodes { id state body submittedAt url author { login } }
                }
              }
            }
          }
          """,
          {"owner": owner, "name": name, "number": int(pr_number)},
        )
        result["reviews"] = (
          ((data.get("repository") or {}).get("pullRequest") or {}).get("reviews")
          or {}
        ).get("nodes") or []
      if target in ("threads", "both"):
        data = await self._graphql(
          """
          query($owner: String!, $name: String!, $number: Int!) {
            repository(owner: $owner, name: $name) {
              pullRequest(number: $number) {
                reviewThreads(first: 100) {
                  nodes {
                    id isResolved isOutdated path line
                    comments(first: 100) {
                      nodes {
                        id databaseId body createdAt path line
                        author { login }
                      }
                    }
                  }
                }
              }
            }
          }
          """,
          {"owner": owner, "name": name, "number": int(pr_number)},
        )
        result["review_threads"] = (
          (
            (data.get("repository") or {}).get("pullRequest") or {}
          ).get("reviewThreads")
          or {}
        ).get("nodes") or []
      return self._ok(result)

    return await self._read(
      "read list_pull_request_reviews",
      str({
        "repo_full_name": repo_full_name,
        "pr_number": pr_number,
        "target": target,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def add_review_to_pr(
    self,
    repo_full_name: str,
    pr_number: int,
    action: str,
    review: str | None = None,
    file_comments: list[dict] | None = None,
    commit_id: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Submit a GitHub PR review.

    :param repo_full_name: owner/repo
    :param pr_number: PR number
    :param action: COMMENT, APPROVE, or REQUEST_CHANGES
    :param review: review body
    :param file_comments: inline comments with path, body, and line or position
    :param commit_id: commit SHA the review applies to
    """
    repo = self._repo(repo_full_name)

    async def run():
      data = await self._request(
        "POST",
        f"{API}{repo}/pulls/{int(pr_number)}/reviews",
        payload={
          "body": review,
          "event": action,
          "comments": file_comments,
          "commit_id": commit_id,
        },
      )
      return self._ok({"success": True, "review_id": data.get("id"), "review": data})

    return await self._write(
      f"submit {action} review on PR #{pr_number} in {repo_full_name}",
      review or action,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def dismiss_pull_request_review(
    self,
    review_id: str,
    message: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Dismiss a GitHub PR review by GraphQL node ID.

    :param review_id: review node ID from list_pull_request_reviews
    :param message: dismissal message
    """

    async def run():
      data = await self._graphql(
        """
        mutation($id: ID!, $message: String!) {
          dismissPullRequestReview(input: {pullRequestReviewId: $id, message: $message}) {
            pullRequestReview { id state body }
          }
        }
        """,
        {"id": review_id, "message": message},
      )
      review = (
        (data.get("dismissPullRequestReview") or {}).get("pullRequestReview") or {}
      )
      return self._ok({"review": review})

    return await self._write(
      f"dismiss review {review_id}",
      message,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def request_pull_request_reviewers(
    self,
    repository_full_name: str,
    pr_number: int,
    reviewers: list[str] | None = None,
    team_reviewers: list[str] | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Request user or team reviewers on a GitHub PR.

    :param repository_full_name: owner/repo
    :param pr_number: PR number
    :param reviewers: usernames
    :param team_reviewers: team slugs
    """
    repo = self._repo(repository_full_name)

    async def run():
      data = await self._request(
        "POST",
        f"{API}{repo}/pulls/{int(pr_number)}/requested_reviewers",
        payload={"reviewers": reviewers, "team_reviewers": team_reviewers},
      )
      return self._ok(data)

    return await self._write(
      f"request reviewers {reviewers or team_reviewers} on PR #{pr_number}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def write_review_comment(
    self,
    repo_full_name: str,
    comment: str,
    pr_number: int | None = None,
    reply_to_comment_id: int | None = None,
    comment_id: int | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Reply to an inline PR review comment or edit an existing review comment.

    :param repo_full_name: owner/repo
    :param comment: Markdown comment body
    :param pr_number: PR number when replying to a review comment
    :param reply_to_comment_id: top-level review comment ID when replying
    :param comment_id: review comment ID when editing
    """
    repo = self._repo(repo_full_name)
    replying = reply_to_comment_id is not None and pr_number is not None
    editing = comment_id is not None
    if replying == editing:
      raise GitHubError(
        422,
        "Give either comment_id to edit or pr_number with reply_to_comment_id to reply.",
        "POST",
        "",
      )

    async def run():
      if editing:
        data = await self._request(
          "PATCH",
          f"{API}{repo}/pulls/comments/{int(comment_id)}",
          payload={"body": comment},
        )
        return self._ok(data)
      data = await self._request(
        "POST",
        f"{API}{repo}/pulls/{int(pr_number)}/comments/{int(reply_to_comment_id)}/replies",
        payload={"body": comment},
      )
      return self._ok(data)

    action = (
      f"edit review comment {comment_id} in {repo_full_name}"
      if editing
      else f"reply to review comment {reply_to_comment_id} on PR #{pr_number} in {repo_full_name}"
    )
    return await self._write(
      action,
      comment,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def set_review_thread(
    self,
    thread_id: str,
    resolved: bool = True,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Resolve or reopen a GitHub PR review thread by GraphQL node ID.

    :param thread_id: thread node ID from list_pull_request_reviews(target='threads')
    :param resolved: true resolves thread, false reopens thread
    """

    async def run():
      if resolved:
        data = await self._graphql(
          """
          mutation($id: ID!) {
            resolveReviewThread(input: {threadId: $id}) {
              thread { id isResolved }
            }
          }
          """,
          {"id": thread_id},
        )
        thread = (data.get("resolveReviewThread") or {}).get("thread") or {}
      else:
        data = await self._graphql(
          """
          mutation($id: ID!) {
            unresolveReviewThread(input: {threadId: $id}) {
              thread { id isResolved }
            }
          }
          """,
          {"id": thread_id},
        )
        thread = (data.get("unresolveReviewThread") or {}).get("thread") or {}
      return self._ok({"review_thread": thread})

    verb = "resolve" if resolved else "reopen"
    return await self._write(
      f"{verb} review thread {thread_id}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # ═════════════════════════ files, trees, commits ══════════════════════

  async def fetch_github_file(
    self,
    repository_full_name: str,
    path: str = "",
    blob_sha: str | None = None,
    ref: str | None = None,
    encoding: str = "utf-8",
    start_line: int | None = None,
    end_line: int | None = None,
    offset: int = 0,
    max_chars: int = 10000,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read a GitHub file or directory by path, or a git blob by SHA.

    :param repository_full_name: owner/repo
    :param path: file or directory path inside repo without blob_sha
    :param blob_sha: git blob SHA for paged decoded blob reads
    :param ref: branch, tag, or commit when reading by path
    :param encoding: utf-8 or base64 when reading by path
    :param start_line: 1-based first line when reading by path
    :param end_line: 1-based inclusive last line when reading by path
    :param offset: character offset when reading by blob_sha
    :param max_chars: maximum characters when reading by blob_sha
    """
    if not blob_sha and not path:
      raise GitHubError(422, "Give path or blob_sha.", "GET", "")

    async def run():
      repo = self._repo(repository_full_name)
      if blob_sha:
        data = await self._request(
          "GET", f"{API}{repo}/git/blobs/{self._seg(blob_sha)}"
        )
        content = data.get("content") or ""
        if data.get("encoding") == "base64":
          content = base64.b64decode(content).decode("utf-8", "replace")
        return self._ok(
          self._page(
            {"content": content, "sha": data.get("sha"), "size": data.get("size")},
            offset,
            max_chars,
          )
        )
      data = await self._request(
        "GET", f"{API}{repo}/contents/{self._path(path)}", params={"ref": ref}
      )
      if isinstance(data, list):
        return self._ok(
          {
            "content": json.dumps(data, indent=2)[:400000],
            "encoding": "json",
            "sha": None,
            "directory": True,
            "display_url": (
              data[0].get("html_url").rsplit("/", 1)[0] if data else None
            ),
            "display_title": f"{repository_full_name}/{path}",
          }
        )
      blob = data.get("content") or ""
      text = (
        base64.b64decode(blob).decode("utf-8", "replace")
        if data.get("encoding") == "base64"
        else blob
      )
      if start_line or end_line:
        lines = text.splitlines()
        first = max(1, int(start_line or 1))
        last = min(len(lines), int(end_line or len(lines)))
        text = "\n".join(lines[first - 1 : last])
      if encoding == "base64":
        text = (
          base64.b64encode(text.encode()).decode()
          if data.get("encoding") != "base64" or start_line or end_line
          else blob
        )
      return self._ok(
        {
          "content": text,
          "encoding": encoding,
          "sha": data.get("sha"),
          "display_url": data.get("html_url"),
          "display_title": data.get("name") or path,
          "path": data.get("path"),
          "size": data.get("size"),
        }
      )

    return await self._read(
      "read fetch_github_file",
      str({
        "repository_full_name": repository_full_name,
        "path": path,
        "blob_sha": blob_sha,
        "ref": ref,
        "encoding": encoding,
        "start_line": start_line,
        "end_line": end_line,
        "offset": offset,
        "max_chars": max_chars,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def fetch_github_url(
    self,
    url: str,
    offset: int = 0,
    max_chars: int = 10000,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read `github.com` or `api.github.com` URLs in paged text ranges.

    :param url: github.com or api.github.com URL
    :param offset: character offset in returned text
    :param max_chars: maximum characters within output byte limit
    """

    async def run():
      def paged(payload):
        title = payload.get("title")
        if isinstance(title, str) and len(title) > 200:
          payload = {**payload, "title": title[:200], "title_truncated": True}
        return self._ok(self._page(payload, offset, max_chars))

      parsed = urlparse(url)
      if parsed.netloc in ("api.github.com",):
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        data = await self._request("GET", f"https://api.github.com{path}", raw=True)
        return paged(
          {"content": data, "url": url, "title": None, "modified_date": None}
        )
      parts = [p for p in parsed.path.split("/") if p]
      if parsed.netloc != "github.com" or len(parts) < 2:
        raise GitHubError(
          422,
          f"fetch_github_url reads github.com or api.github.com URLs only, not {url}. "
          "Use the builtin fetch_url tool for that URL.",
          "GET",
          url,
        )
      owner, repo_name = parts[0], parts[1]
      rest = parts[2:]
      repo = self._repo(f"{owner}/{repo_name}")
      title = f"{owner}/{repo_name}"
      if not rest:
        data = await self._request("GET", f"{API}{repo}")
        return paged(
          {
            "content": json.dumps(data, indent=2),
            "title": title,
            "url": url,
            "modified_date": data.get("updated_at"),
          }
        )
      segment, tail = rest[0], rest[1:]
      if segment in ("tree", "blob") and tail:
        ref, path = tail[0], "/".join(tail[1:])
        if segment == "tree":
          data = await self._request(
            "GET", f"{API}{repo}/contents/{self._path(path)}", params={"ref": ref}
          )
          return paged(
            {
              "content": json.dumps(data, indent=2),
              "title": f"{title}/{path}",
              "url": url,
              "modified_date": None,
            }
          )
        file_data = await self._request(
          "GET", f"{API}{repo}/contents/{self._path(path)}", params={"ref": ref}
        )
        content = file_data.get("content") or ""
        if file_data.get("encoding") == "base64":
          content = base64.b64decode(content).decode("utf-8", "replace")
        return paged(
          {
            "content": content,
            "title": f"{title}/{path}",
            "url": url,
            "modified_date": None,
          }
        )
      if segment == "issues" and tail:
        data = await self._request("GET", f"{API}{repo}/issues/{int(tail[0])}")
        return paged(
          {
            "content": f"#{data.get('number')} {data.get('title')}\n\n{data.get('body') or ''}",
            "title": data.get("title"),
            "url": url,
            "modified_date": data.get("updated_at"),
          }
        )
      if segment == "pull" and tail:
        pull = await self._request("GET", f"{API}{repo}/pulls/{int(tail[0])}")
        diff = await self._request(
          "GET",
          f"{API}{repo}/pulls/{int(tail[0])}",
          accept="application/vnd.github.diff",
          raw=True,
        )
        return paged(
          {
            "content": diff,
            "title": f"#{pull.get('number')} {pull.get('title')}",
            "url": url,
            "modified_date": pull.get("updated_at"),
          }
        )
      if segment == "commit" and tail:
        commit = await self._request(
          "GET", f"{API}{repo}/commits/{self._seg(tail[0])}"
        )
        lines = ((commit.get("commit") or {}).get("message") or "").splitlines()
        return paged(
          {
            "content": json.dumps(commit, indent=2),
            "title": lines[0] if lines else "",
            "url": url,
            "modified_date": (
              (commit.get("commit") or {}).get("committer") or {}
            ).get("date"),
          }
        )
      if segment == "branches":
        data = await self._request(
          "GET", f"{API}{repo}/branches", params={"per_page": 100}
        )
        return paged(
          {
            "content": json.dumps(data, indent=2),
            "title": f"{title} branches",
            "url": url,
            "modified_date": None,
          }
        )
      if segment == "actions" and tail[:1] == ["runs"]:
        run_id = tail[1] if len(tail) > 1 else None
        if run_id:
          data = await self._request("GET", f"{API}{repo}/actions/runs/{int(run_id)}")
        else:
          data = await self._request(
            "GET", f"{API}{repo}/actions/runs", params={"per_page": 20}
          )
        return paged(
          {
            "content": json.dumps(data, indent=2),
            "title": f"{title} actions",
            "url": url,
            "modified_date": None,
          }
        )
      if segment == "search":
        query = parse_qs(parsed.query).get("q", [""])[0]
        return paged(
          {
            "content": json.dumps(await self._search_code(query, 20), indent=2),
            "title": "search",
            "url": url,
            "modified_date": None,
          }
        )
      raise GitHubError(
        422,
        f"fetch_github_url does not support path {parsed.path}.",
        "GET",
        url,
      )

    return await self._read(
      "read fetch_github_url",
      str({"url": url, "offset": offset, "max_chars": max_chars}),
      run,
      __user__,
      __event_call__,
    )

  async def write_github_file(
    self,
    repository_full_name: str,
    path: str,
    message: str,
    action: Literal["create", "update", "delete"] = "create",
    content: str = "",
    sha: str | None = None,
    branch: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create, update, or delete one GitHub file in a single commit.

    :param repository_full_name: owner/repo
    :param path: file path in repo
    :param message: commit message
    :param action: create, update, or delete
    :param content: file text for create or update
    :param sha: current blob SHA required for update or delete
    :param branch: target branch
    """
    repo = self._repo(repository_full_name)
    if action not in ("create", "update", "delete"):
      raise GitHubError(
        422, "action must be 'create', 'update', or 'delete'.", "POST", ""
      )
    if action in ("update", "delete") and not sha:
      raise GitHubError(
        422, f"sha is required when action is '{action}'.", "POST", ""
      )

    async def run():
      if action == "delete":
        data = await self._request(
          "DELETE",
          f"{API}{repo}/contents/{self._path(path)}",
          payload={"message": message, "sha": sha, "branch": branch},
        )
        return self._ok({"commit_sha": (data.get("commit") or {}).get("sha")})
      data = await self._request(
        "PUT",
        f"{API}{repo}/contents/{self._path(path)}",
        payload={
          "message": message,
          "content": base64.b64encode(content.encode()).decode(),
          "sha": sha if action == "update" else None,
          "branch": branch,
        },
      )
      return self._ok(
        {
          "commit_sha": (data.get("commit") or {}).get("sha"),
          "content_sha": (data.get("content") or {}).get("sha"),
        }
      )

    return await self._write(
      f"{action} {path} in {repository_full_name}",
      message,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def write_branch(
    self,
    repository_full_name: str,
    branch_name: str,
    action: Literal["create", "move"] = "create",
    sha: str | None = None,
    base_ref: str | None = None,
    force: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create a GitHub branch or move an existing branch to another commit.

    :param repository_full_name: owner/repo
    :param branch_name: branch name
    :param action: create or move
    :param sha: target commit SHA
    :param base_ref: source branch name when creating without sha
    :param force: allow non-fast-forward move when action is move
    """
    repo = self._repo(repository_full_name)
    if action not in ("create", "move"):
      raise GitHubError(422, "action must be 'create' or 'move'.", "POST", "")
    if action == "move" and not sha:
      raise GitHubError(422, "sha is required when action is 'move'.", "PATCH", "")
    if action == "create" and not sha and not base_ref:
      raise GitHubError(
        422, "Give sha or base_ref when action is 'create'.", "POST", ""
      )

    async def run():
      if action == "move":
        await self._request(
          "PATCH",
          f"{API}{repo}/git/refs/heads/{self._path(branch_name)}",
          payload={"sha": sha, "force": force},
        )
        return self._ok({"success": True, "branch": branch_name, "sha": sha})
      target = sha
      if not target:
        ref = await self._request(
          "GET", f"{API}{repo}/git/ref/heads/{self._path(base_ref or '')}"
        )
        target = (ref.get("object") or {}).get("sha")
      data = await self._request(
        "POST",
        f"{API}{repo}/git/refs",
        payload={"ref": f"refs/heads/{branch_name}", "sha": target},
      )
      return self._ok(
        {
          "branch": data.get("ref", "").removeprefix("refs/heads/"),
          "sha": target,
          "ref": data,
        }
      )

    detail = (
      f"to {sha} ({'force' if force else 'fast-forward'})"
      if action == "move"
      else f"from {sha or base_ref}"
    )
    return await self._write(
      f"{action} branch {branch_name} in {repository_full_name}",
      detail,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def list_tree(
    self,
    repository_full_name: str,
    ref: str | None = None,
    tree_sha: str | None = None,
    recursive: bool = True,
    path: str | None = None,
    path_prefix: str | None = None,
    entry_type: Literal["blob", "tree"] | None = None,
    offset: int = 0,
    max_results: int = 200,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List a GitHub tree for a branch, tag, or commit, recursive by default.

    :param repository_full_name: owner/repo
    :param ref: branch or tag name, defaults to repo default branch
    :param tree_sha: tree SHA instead of ref
    :param recursive: traverse nested directories
    :param path: relative path prefix alias for path_prefix
    :param path_prefix: filter entries starting with this prefix
    :param entry_type: optional git entry kind (blob or tree)
    :param offset: entry offset within the filtered tree
    :param max_results: maximum entries per page within output byte limits
    """

    async def run():
      repo = self._repo(repository_full_name)
      target = tree_sha or ref
      if not target:
        info = await self._request("GET", f"{API}{repo}")
        target = info.get("default_branch") or "main"
      data = await self._request(
        "GET",
        f"{API}{repo}/git/trees/{self._path(target)}",
        params={"recursive": "1" if recursive else None},
      )
      entries = data.get("tree") or []
      prefix = ((path_prefix if path_prefix is not None else path) or "").lstrip("/")
      if prefix:
        entries = [
          item
          for item in entries
          if (item.get("path") or "").startswith(prefix)
        ]
      if entry_type:
        entries = [item for item in entries if item.get("type") == entry_type]
      limit = min(max(1, int(self.valves.max_tree_entries)), 5000)
      rows = [self._tree_entry(item) for item in entries[:limit]]
      api_truncated = bool(data.get("truncated")) or len(entries) > limit
      paged = self._list_page(
        {"tree_sha": data.get("sha") or tree_sha, "api_truncated": api_truncated},
        "tree",
        rows,
        offset,
        max_results,
        cap=limit,
      )
      if api_truncated:
        paged["truncated"] = True
      return self._ok(paged)

    return await self._read(
      "read list_tree",
      str({
        "repository_full_name": repository_full_name,
        "ref": ref,
        "tree_sha": tree_sha,
        "recursive": recursive,
        "path": path,
        "path_prefix": path_prefix,
        "entry_type": entry_type,
        "offset": offset,
        "max_results": max_results,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def list_commits(
    self,
    repository_full_name: str,
    path: str | None = None,
    sha: str | None = None,
    since: str | None = None,
    until: str | None = None,
    per_page: int = 30,
    page: int = 1,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List short GitHub commit summaries, newest first, with optional path filter.

    :param repository_full_name: owner/repo
    :param path: filter commits touching this path
    :param sha: branch, tag, or commit to walk from
    :param since: ISO 8601 start timestamp
    :param until: ISO 8601 end timestamp
    :param per_page: commits per page
    :param page: page number
    """

    async def run():
      repo = self._repo(repository_full_name)
      limit = min(max(1, int(per_page)), 100)
      data = await self._request(
        "GET",
        f"{API}{repo}/commits",
        params={
          "path": path,
          "sha": sha,
          "since": since,
          "until": until,
          "per_page": limit,
          "page": int(page),
        },
      )
      return self._ok(
        {
          "commits": [self._commit_summary(item) for item in data],
          "page": int(page),
          "per_page": limit,
          "next_page": int(page) + 1 if len(data) == limit else None,
        }
      )

    return await self._read(
      "read list_commits",
      str({
        "repository_full_name": repository_full_name,
        "path": path,
        "sha": sha,
        "since": since,
        "until": until,
        "per_page": per_page,
        "page": page,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def fetch_commit(
    self,
    repository_full_name: str,
    commit_sha: str,
    include_status: bool = False,
    status_only: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read one GitHub commit, its combined status, or both.

    :param repository_full_name: owner/repo
    :param commit_sha: commit SHA or ref
    :param include_status: attach combined commit status to commit payload
    :param status_only: return only combined commit status
    """

    async def run():
      repo = self._repo(repository_full_name)
      if status_only:
        status = await self._request(
          "GET", f"{API}{repo}/commits/{self._seg(commit_sha)}/status"
        )
        return self._ok(
          {
            "statuses": status.get("statuses") or [],
            "state": status.get("state"),
            "total_count": status.get("total_count"),
          }
        )
      data = await self._request(
        "GET", f"{API}{repo}/commits/{self._seg(commit_sha)}"
      )
      if include_status:
        status = await self._request(
          "GET", f"{API}{repo}/commits/{self._seg(commit_sha)}/status"
        )
        data = {
          **data,
          "combined_status": {
            "statuses": status.get("statuses") or [],
            "state": status.get("state"),
            "total_count": status.get("total_count"),
          },
        }
      return self._ok(data)

    return await self._read(
      "read fetch_commit",
      str({
        "repository_full_name": repository_full_name,
        "commit_sha": commit_sha,
        "include_status": include_status,
        "status_only": status_only,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def compare_commits(
    self,
    repo_full_name: str,
    base: str,
    head: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Compare two GitHub refs and return commit and file changes.

    :param repo_full_name: owner/repo
    :param base: base ref
    :param head: head ref
    """

    async def run():
      repo = self._repo(repo_full_name)
      data = await self._request(
        "GET", f"{API}{repo}/compare/{self._path(base)}...{self._path(head)}"
      )
      return self._ok(
        {**data, "repository_full_name": repo_full_name, "base": base, "head": head}
      )

    return await self._read(
      "read compare_commits",
      str({"repo_full_name": repo_full_name, "base": base, "head": head}),
      run,
      __user__,
      __event_call__,
    )

  # ═══════════════ repos, refs, releases, packages, account ═════════════

  async def get_repo(
    self,
    repository_full_name: str | None = None,
    repository_id: int | None = None,
    repository_url: str | None = None,
    collaborator: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read GitHub repo metadata or one collaborator's permission level.

    :param repository_full_name: owner/repo
    :param repository_id: numeric repo ID
    :param repository_url: repo HTML or API URL
    :param collaborator: username to check for repo permission level
    """

    async def run():
      path = self._repo_of(repository_full_name, repository_id, repository_url)
      if collaborator:
        data = await self._request(
          "GET", f"{API}{path}/collaborators/{self._seg(collaborator)}/permission"
        )
        return self._ok(
          {"permission": data.get("permission"), "user": data.get("user")}
        )
      data = await self._request("GET", f"{API}{path}")
      return self._ok({**data, "repository_full_name": data.get("full_name")})

    return await self._read(
      "read get_repo",
      str({
        "repository_full_name": repository_full_name,
        "repository_id": repository_id,
        "repository_url": repository_url,
        "collaborator": collaborator,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def list_repositories(
    self,
    page_size: int = 20,
    page_offset: int = 0,
    owner: str | None = None,
    include_search_index_status: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List GitHub repos for the signed-in user or one owner.

    :param page_size: repos per page
    :param page_offset: items to skip
    :param owner: optional org or user login
    :param include_search_index_status: unused compatibility flag
    """

    async def run():
      size = min(max(1, int(page_size)), 100)
      offset = max(0, int(page_offset))
      page = offset // size + 1
      path = f"{API}/users/{self._seg(owner)}/repos" if owner else f"{API}/user/repos"
      data = await self._request(
        "GET",
        path,
        params={
          "per_page": size,
          "page": page,
          "sort": "updated",
          "affiliation": "owner,collaborator,organization_member",
        },
      )
      items = data[offset % size :]
      return self._ok(
        {
          "repositories": [self._repository_summary(item) for item in items],
          "page_offset": offset,
          "page_size": size,
          "next_offset": offset + len(items) if len(data) == size else None,
        }
      )

    return await self._read(
      "read list_repositories",
      str({"page_size": page_size, "page_offset": page_offset, "owner": owner}),
      run,
      __user__,
      __event_call__,
    )

  async def list_repo_refs(
    self,
    repo_full_name: str,
    ref_type: Literal["tags", "branches"] = "tags",
    query: str = "",
    per_page: int = 30,
    page: int = 1,
    cursor: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List or filter tags or branches in one GitHub repo.

    :param repo_full_name: owner/repo
    :param ref_type: tags or branches
    :param query: optional case-insensitive substring filter
    :param per_page: items per page
    :param page: page number without cursor
    :param cursor: page cursor from previous branches call
    """
    if ref_type not in ("tags", "branches"):
      raise GitHubError(422, "ref_type must be 'tags' or 'branches'.", "GET", "")

    async def run():
      repo = self._repo(repo_full_name)
      size = min(max(1, int(per_page)), 100)
      current = int(cursor) if cursor and str(cursor).isdigit() else max(1, int(page))
      needle = (query or "").lower()
      if ref_type == "branches":
        data = await self._request(
          "GET", f"{API}{repo}/branches", params={"per_page": size, "page": current}
        )
        branches = [
          {"branch": item.get("name"), "sha": (item.get("commit") or {}).get("sha")}
          for item in data
          if needle in (item.get("name") or "").lower()
        ]
        token = str(current + 1) if len(data) == size else None
        return self._ok({"branches": branches, "cursor": token})
      data = await self._request(
        "GET", f"{API}{repo}/tags", params={"per_page": size, "page": current}
      )
      if needle:
        data = [
          item for item in data if needle in (item.get("name") or "").lower()
        ]
      return self._ok({"tags": data})

    return await self._read(
      "read list_repo_refs",
      str({
        "repo_full_name": repo_full_name,
        "ref_type": ref_type,
        "query": query,
        "per_page": per_page,
        "page": page,
        "cursor": cursor,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def list_github_releases(
    self,
    repo_full_name: str,
    release_id: int | None = None,
    tag: str | None = None,
    per_page: int = 30,
    page: int = 1,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List repo releases or read one release by ID or tag.

    :param repo_full_name: owner/repo
    :param release_id: release ID, takes precedence over tag
    :param tag: release tag name
    :param per_page: releases per page
    :param page: page number
    """

    async def run():
      repo = self._repo(repo_full_name)
      if release_id is not None:
        data = await self._request("GET", f"{API}{repo}/releases/{int(release_id)}")
        return self._ok({"release": data})
      if tag:
        data = await self._request(
          "GET", f"{API}{repo}/releases/tags/{self._seg(tag)}"
        )
        return self._ok({"release": data})
      data = await self._request(
        "GET",
        f"{API}{repo}/releases",
        params={"per_page": min(max(1, int(per_page)), 100), "page": int(page)},
      )
      return self._ok({"releases": data})

    return await self._read(
      "read list_github_releases",
      str({
        "repo_full_name": repo_full_name,
        "release_id": release_id,
        "tag": tag,
        "per_page": per_page,
        "page": page,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def list_github_packages(
    self,
    package_type: str | None = None,
    owner: str | None = None,
    owner_type: str = "org",
    package_name: str | None = None,
    per_page: int = 30,
    page: int = 1,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List GitHub packages for signed-in user, user, or org, or read one package.

    :param package_type: npm, maven, rubygems, docker, nuget, container, or generic
    :param owner: user or org login, omit for signed-in user
    :param owner_type: org or user
    :param package_name: package name to read with versions
    :param per_page: versions per page when reading one package
    :param page: page number when reading one package
    """

    async def run():
      scope = (owner_type or "org").strip().lower()
      if scope not in ("org", "user"):
        raise GitHubError(422, "owner_type must be 'org' or 'user'.", "GET", "")
      if owner and not package_type:
        raise GitHubError(422, "Give package_type with owner.", "GET", "")
      if package_name and not package_type:
        raise GitHubError(422, "Give package_type with package_name.", "GET", "")
      base = f"/{scope}s/{self._seg(owner)}" if owner else "/user"
      if package_name:
        path = (
          f"{API}{base}/packages/{self._seg(package_type or '')}/{self._seg(package_name)}"
        )
        info = await self._request("GET", path)
        versions = await self._request(
          "GET",
          f"{path}/versions",
          params={"per_page": min(max(1, int(per_page)), 100), "page": int(page)},
        )
        return self._ok({"package": info, "versions": versions})
      data = await self._request(
        "GET", f"{API}{base}/packages", params={"package_type": package_type}
      )
      return self._ok({"packages": data})

    return await self._read(
      "read list_github_packages",
      str({
        "package_type": package_type,
        "owner": owner,
        "owner_type": owner_type,
        "package_name": package_name,
        "per_page": per_page,
        "page": page,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def inspect_github_account(
    self,
    target: Literal["profile", "orgs", "installations", "actions_minutes"] = "profile",
    owner: str | None = None,
    owner_type: str = "org",
    manageable_only: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read signed-in GitHub profile, orgs, installations, or Actions billing minutes.

    :param target: profile, orgs, installations, or actions_minutes
    :param owner: org or user login when target is actions_minutes
    :param owner_type: org or user when target is actions_minutes
    :param manageable_only: filter installations to manageable entries
    """
    if target not in ("profile", "orgs", "installations", "actions_minutes"):
      raise GitHubError(
        422,
        "target must be 'profile', 'orgs', 'installations', or 'actions_minutes'.",
        "GET",
        "",
      )
    if target == "actions_minutes" and not owner:
      raise GitHubError(
        422, "owner is required when target is 'actions_minutes'.", "GET", ""
      )

    async def run():
      if target == "profile":
        data = await self._request("GET", f"{API}/user")
        return self._ok(
          {
            "id": str(data.get("id")) if data.get("id") is not None else None,
            "name": data.get("name"),
            "email": data.get("email"),
            "nickname": data.get("login"),
            "picture": data.get("avatar_url"),
            "user": data,
          }
        )
      if target == "orgs":
        data = await self._request("GET", f"{API}/user/orgs")
        return self._ok(
          {"orgs": [item.get("login") for item in data], "organizations": data}
        )
      if target == "installations":
        data = await self._request("GET", f"{API}/user/installations")
        installations = (data or {}).get("installations") or []
        if manageable_only:
          installations = [
            item
            for item in installations
            if (item.get("permissions") or {}).get("administration") == "write"
            or (item.get("repository_selection") or "") == "all"
          ]
        return self._ok(
          {
            "installations": installations,
            "allow_all_repositories_for_testing": None,
          }
        )
      scope = (owner_type or "org").strip().lower()
      if scope not in ("org", "user"):
        raise GitHubError(422, "owner_type must be 'org' or 'user'.", "GET", "")
      base = "orgs" if scope == "org" else "users"
      data = await self._request(
        "GET", f"{API}/{base}/{self._seg(owner or '')}/settings/billing/actions"
      )
      return self._ok({"minutes": data})

    return await self._read(
      "read inspect_github_account",
      str({
        "target": target,
        "owner": owner,
        "owner_type": owner_type,
        "manageable_only": manageable_only,
      }),
      run,
      __user__,
      __event_call__,
    )

  # ═════════════════════════════════ search ═════════════════════════════

  async def _search_code(
    self,
    query: str,
    topn: int,
    repo: str | list[str] | None = None,
    org: str | None = None,
  ) -> list:
    terms = [query]
    if repo:
      for name in repo if isinstance(repo, list) else [repo]:
        terms.append(f"repo:{name}")
    if org:
      terms.append(f"org:{org}")
    data = await self._request(
      "GET",
      f"{API}/search/code",
      params={"q": " ".join(terms), "per_page": min(max(1, int(topn)), 100)},
    )
    return [
      {**item, "display_url": item.get("html_url")}
      for item in (data.get("items") or [])
    ]

  async def _search_issues(
    self,
    query: str,
    qualifier: str,
    topn: int,
    repository_full_name: str | list[str] | None,
    repository_id: int | list[int] | None,
    repository_url: str | list[str] | None,
    org: str | None,
    sort: str | None,
    order: str | None,
    state: str | None,
    page: int = 1,
  ) -> dict:
    terms = [query]
    if qualifier:
      terms.append(f"is:{qualifier}")
    for value in (repository_full_name, repository_url):
      if value:
        for item in value if isinstance(value, list) else [value]:
          terms.append(f"repo:{str(item).removeprefix('https://github.com/')}")
    if org:
      terms.append(f"org:{org}")
    if state and state != "all":
      terms.append(f"state:{state}")
    if repository_id:
      for item in (
        repository_id if isinstance(repository_id, list) else [repository_id]
      ):
        terms.append(f"repo:{item}")
    sorts = {
      "best-match": None,
      "created": "created",
      "updated": "updated",
      "comments": "comments",
      "reactions": "reactions",
      "interactions": "interactions",
    }
    size = min(max(1, int(topn)), 100)
    current = max(1, int(page))
    data = await self._request(
      "GET",
      f"{API}/search/issues",
      params={
        "q": " ".join(terms),
        "per_page": size,
        "page": current,
        "sort": sorts.get(sort or "best-match"),
        "order": order,
      },
    )
    items = [self._issue_summary(item) for item in (data.get("items") or [])]
    return self._ok(
      {
        "issues": items,
        "total_count": data.get("total_count"),
        "page": current,
        "per_page": size,
        "next_page": (
          current + 1
          if len(items) == size
          and current * size < min(data.get("total_count", 1000), 1000)
          else None
        ),
        "search_limited": data.get("total_count", 0) > 1000,
        "incomplete_results": data.get("incomplete_results", False),
      }
    )

  async def search_github(
    self,
    query: str,
    scope: Literal[
      "issues", "prs", "repositories", "code", "commits", "branches"
    ] = "issues",
    repository_full_name: str | list[str] | None = None,
    repository_id: int | list[int] | None = None,
    repository_url: str | list[str] | None = None,
    org: str | None = None,
    topn: int = 20,
    per_page: int | None = None,
    page: int = 1,
    sort: str | None = None,
    order: str | None = None,
    state: str | None = None,
    cursor: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Search GitHub issues, PRs, repos, code, commits, or branches.

    :param query: search terms and optional GitHub qualifiers
    :param scope: issues, prs, repositories, code, commits, or branches
    :param repository_full_name: one owner/repo or a list
    :param repository_id: one numeric repo ID or a list
    :param repository_url: one repo URL or a list
    :param org: restrict search to one org
    :param topn: results per page without per_page
    :param per_page: explicit page size up to 100
    :param page: page number starting at 1
    :param sort: sort field for issues, PRs, or commits
    :param order: desc or asc
    :param state: open, closed, or all for issues and PRs
    :param cursor: page cursor for branches scope
    """
    if scope not in (
      "issues",
      "prs",
      "repositories",
      "code",
      "commits",
      "branches",
    ):
      raise GitHubError(
        422,
        "scope must be 'issues', 'prs', 'repositories', 'code', 'commits', or 'branches'.",
        "GET",
        "",
      )

    async def run():
      size = min(max(1, int(per_page or topn or 20)), 100)
      current = max(1, int(page))
      if scope in ("issues", "prs"):
        return await self._search_issues(
          query,
          "pr" if scope == "prs" else "",
          size,
          repository_full_name,
          repository_id,
          repository_url,
          org,
          sort,
          order,
          state,
          page=current,
        )
      if scope == "repositories":
        terms = [query] + ([f"org:{org}"] if org else [])
        data = await self._request(
          "GET",
          f"{API}/search/repositories",
          params={"q": " ".join(terms), "per_page": size, "page": current},
        )
        rows = data.get("items") or []
        return self._ok(
          {
            "repositories": [self._repository_summary(item) for item in rows],
            "page": current,
            "per_page": size,
            "next_page": (
              current + 1
              if len(rows) == size
              and current * size < min(data.get("total_count", 1000), 1000)
              else None
            ),
            "search_limited": data.get("total_count", 0) > 1000,
            "incomplete_results": data.get("incomplete_results", False),
            "total_count": data.get("total_count"),
          }
        )
      if scope == "code":
        return self._ok(
          {"results": await self._search_code(query, size, repository_full_name, org)}
        )
      if scope == "commits":
        terms = [query]
        for value in (repository_full_name, repository_url):
          if value:
            for item in value if isinstance(value, list) else [value]:
              terms.append(f"repo:{str(item).removeprefix('https://github.com/')}")
        if repository_id:
          for item in (
            repository_id if isinstance(repository_id, list) else [repository_id]
          ):
            repo_meta = await self._request(
              "GET", f"{API}/repositories/{int(item)}"
            )
            terms.append(f"repo:{repo_meta['full_name']}")
        if org:
          terms.append(f"org:{org}")
        sorts = {
          "best-match": None,
          "author-date": "author-date",
          "committer-date": "committer-date",
        }
        data = await self._request(
          "GET",
          f"{API}/search/commits",
          params={
            "q": " ".join(terms),
            "per_page": size,
            "sort": sorts.get(sort or "best-match"),
            "order": order,
          },
        )
        items = [self._commit_summary(item) for item in (data.get("items") or [])]
        return self._ok({"commits": items, "total_count": data.get("total_count")})
      repo_name = (
        repository_full_name[0]
        if isinstance(repository_full_name, list)
        else repository_full_name
      )
      if not repo_name:
        raise GitHubError(
          422,
          "repository_full_name is required when scope is 'branches'.",
          "GET",
          "",
        )
      repo = self._repo(repo_name)
      branch_page = (
        int(cursor) if cursor and str(cursor).isdigit() else current
      )
      data = await self._request(
        "GET", f"{API}{repo}/branches", params={"per_page": size, "page": branch_page}
      )
      needle = (query or "").lower()
      branches = [
        {"branch": item.get("name"), "sha": (item.get("commit") or {}).get("sha")}
        for item in data
        if needle in (item.get("name") or "").lower()
      ]
      token = str(branch_page + 1) if len(data) == size else None
      return self._ok({"branches": branches, "cursor": token})

    return await self._read(
      "read search_github",
      str({
        "query": query,
        "scope": scope,
        "repository_full_name": repository_full_name,
        "repository_id": repository_id,
        "org": org,
      }),
      run,
      __user__,
      __event_call__,
    )

  # ═══════════════════════════ security alerts ══════════════════════════

  async def list_security_alerts(
    self,
    repo_full_name: str,
    alert_type: Literal[
      "code_scanning", "secret_scanning", "dependabot"
    ] = "code_scanning",
    alert_number: int | None = None,
    state: str | None = "open",
    severity: str | None = None,
    tool_name: str | None = None,
    ref: str | None = None,
    resolution: str | None = None,
    secret_type: str | None = None,
    validity: str | None = None,
    ecosystem: str | None = None,
    package: str | None = None,
    manifest: str | None = None,
    scope: str | None = None,
    sort: str = "created",
    direction: str = "desc",
    per_page: int = 30,
    page: int = 1,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List code-scanning, secret-scanning, or Dependabot alerts, or read one alert.

    :param repo_full_name: owner/repo
    :param alert_type: code_scanning, secret_scanning, or dependabot
    :param alert_number: alert number to read single alert detail
    :param state: alert state filter such as open, dismissed, resolved, or fixed
    :param severity: severity filter for code_scanning or dependabot
    :param tool_name: scanner name for code_scanning
    :param ref: branch, tag, or commit for code_scanning
    :param resolution: resolution filter for secret_scanning
    :param secret_type: secret type slug for secret_scanning
    :param validity: active, inactive, or unknown for secret_scanning
    :param ecosystem: package ecosystem for dependabot
    :param package: package name for dependabot
    :param manifest: manifest path for dependabot
    :param scope: development or runtime for dependabot
    :param sort: created or updated
    :param direction: asc or desc
    :param per_page: alerts per page
    :param page: page number
    """
    if alert_type not in ("code_scanning", "secret_scanning", "dependabot"):
      raise GitHubError(
        422,
        "alert_type must be 'code_scanning', 'secret_scanning', or 'dependabot'.",
        "GET",
        "",
      )

    async def run():
      repo = self._repo(repo_full_name)
      limit = min(max(1, int(per_page)), 100)
      if alert_type == "code_scanning":
        if alert_number is not None:
          number = int(alert_number)
          alert = await self._request(
            "GET", f"{API}{repo}/code-scanning/alerts/{number}"
          )
          instances = await self._request(
            "GET", f"{API}{repo}/code-scanning/alerts/{number}/instances"
          )
          return self._ok({"alert": alert, "instances": instances})
        alerts = await self._request(
          "GET",
          f"{API}{repo}/code-scanning/alerts",
          params={
            "state": state,
            "tool_name": tool_name,
            "ref": ref,
            "severity": severity,
            "sort": sort,
            "direction": direction,
            "per_page": limit,
            "page": int(page),
          },
        )
        return self._ok({"alerts": alerts})
      if alert_type == "secret_scanning":
        if alert_number is not None:
          number = int(alert_number)
          alert = await self._request(
            "GET", f"{API}{repo}/secret-scanning/alerts/{number}"
          )
          locations = await self._request(
            "GET", f"{API}{repo}/secret-scanning/alerts/{number}/locations"
          )
          return self._ok({"alert": alert, "locations": locations})
        alerts = await self._request(
          "GET",
          f"{API}{repo}/secret-scanning/alerts",
          params={
            "state": state,
            "resolution": resolution,
            "secret_type": secret_type,
            "validity": validity,
            "sort": sort,
            "direction": direction,
            "per_page": limit,
            "page": int(page),
          },
        )
        return self._ok({"alerts": alerts})
      if alert_number is not None:
        alert = await self._request(
          "GET", f"{API}{repo}/dependabot/alerts/{int(alert_number)}"
        )
        return self._ok({"alert": alert})
      alerts = await self._request(
        "GET",
        f"{API}{repo}/dependabot/alerts",
        params={
          "state": state,
          "severity": severity,
          "ecosystem": ecosystem,
          "package": package,
          "manifest": manifest,
          "scope": scope,
          "sort": sort,
          "direction": direction,
          "per_page": limit,
          "page": int(page),
        },
      )
      return self._ok({"alerts": alerts})

    return await self._read(
      "read list_security_alerts",
      str({
        "repo_full_name": repo_full_name,
        "alert_type": alert_type,
        "alert_number": alert_number,
        "state": state,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def update_security_alert(
    self,
    repo_full_name: str,
    alert_number: int,
    alert_type: Literal[
      "code_scanning", "secret_scanning", "dependabot"
    ] = "code_scanning",
    state: str | None = None,
    reason: str | None = None,
    resolution: str | None = None,
    comment: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Dismiss, resolve, or reopen a GitHub security alert.

    :param repo_full_name: owner/repo
    :param alert_number: alert number
    :param alert_type: code_scanning, secret_scanning, or dependabot
    :param state: dismissed or open, or resolved or open for secret_scanning
    :param reason: dismissal reason for code_scanning or dependabot
    :param resolution: resolution reason for secret_scanning
    :param comment: dismissal or resolution comment
    """
    repo = self._repo(repo_full_name)
    if alert_type not in ("code_scanning", "secret_scanning", "dependabot"):
      raise GitHubError(
        422,
        "alert_type must be 'code_scanning', 'secret_scanning', or 'dependabot'.",
        "PATCH",
        "",
      )
    number = int(alert_number)
    target_state = state or (
      "resolved" if alert_type == "secret_scanning" else "dismissed"
    )

    async def run():
      if alert_type == "secret_scanning":
        data = await self._request(
          "PATCH",
          f"{API}{repo}/secret-scanning/alerts/{number}",
          payload={
            "state": target_state,
            "resolution": resolution or reason,
            "resolution_comment": comment,
          },
        )
        return self._ok({"alert": data})
      route = "code-scanning" if alert_type == "code_scanning" else "dependabot"
      data = await self._request(
        "PATCH",
        f"{API}{repo}/{route}/alerts/{number}",
        payload={
          "state": target_state,
          "dismissed_reason": reason,
          "dismissed_comment": comment,
        },
      )
      return self._ok({"alert": data})

    return await self._write(
      f"set {alert_type} alert {number} to {target_state} in {repo_full_name}",
      f"reason={reason or resolution or 'none'}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # ═══════════════════════════ checks & actions ═════════════════════════

  async def list_github_check_runs(
    self,
    repo_full_name: str,
    ref: str,
    check_name: str | None = None,
    check_run_id: int | None = None,
    status: str | None = None,
    filter: str | None = None,
    per_page: int = 30,
    page: int = 1,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List check runs for a GitHub ref, or read one check run with annotations.

    :param repo_full_name: owner/repo
    :param ref: commit SHA, branch, or tag
    :param check_name: filter by check name
    :param check_run_id: check run ID to read single run and annotations
    :param status: queued, in_progress, or completed
    :param filter: latest or all
    :param per_page: runs per page
    :param page: page number
    """

    async def run():
      repo = self._repo(repo_full_name)
      if check_run_id is not None:
        number = int(check_run_id)
        check_run = await self._request("GET", f"{API}{repo}/check-runs/{number}")
        annotations = await self._request(
          "GET", f"{API}{repo}/check-runs/{number}/annotations"
        )
        return self._ok({"check_run": check_run, "annotations": annotations})
      data = await self._request(
        "GET",
        f"{API}{repo}/commits/{self._seg(ref)}/check-runs",
        params={
          "check_name": check_name,
          "status": status,
          "filter": filter,
          "per_page": min(max(1, int(per_page)), 100),
          "page": int(page),
        },
      )
      return self._ok(
        {
          "check_runs": data.get("check_runs") or [],
          "total_count": data.get("total_count"),
        }
      )

    return await self._read(
      "read list_github_check_runs",
      str({
        "repo_full_name": repo_full_name,
        "ref": ref,
        "check_name": check_name,
        "check_run_id": check_run_id,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def inspect_workflow_run(
    self,
    repo_full_name: str,
    target: Literal[
      "workflows",
      "commit_runs",
      "jobs",
      "job_steps",
      "job_logs",
      "artifacts",
      "artifact_download",
    ] = "workflows",
    commit_sha: str | None = None,
    run_id: int | None = None,
    job_id: int | None = None,
    artifact_id: int | None = None,
    name: str | None = None,
    file_name: str | None = None,
    per_page: int = 30,
    page: int = 1,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Inspect repo Actions workflows, commit runs, jobs, steps, logs, or artifacts.

    :param repo_full_name: owner/repo
    :param target: workflows, commit_runs, jobs, job_steps, job_logs, artifacts, or artifact_download
    :param commit_sha: commit SHA for commit_runs
    :param run_id: workflow run ID for jobs or artifacts
    :param job_id: workflow job ID for job_steps or job_logs
    :param artifact_id: artifact ID for artifact_download
    :param name: optional artifact name filter for artifacts
    :param file_name: reported archive filename for artifact_download
    :param per_page: workflows per page
    :param page: page number for workflows
    """
    valid = (
      "workflows",
      "commit_runs",
      "jobs",
      "job_steps",
      "job_logs",
      "artifacts",
      "artifact_download",
    )
    if target not in valid:
      raise GitHubError(422, f"target must be one of {valid}.", "GET", "")
    if target == "commit_runs" and not commit_sha:
      raise GitHubError(
        422, "commit_sha is required when target is 'commit_runs'.", "GET", ""
      )
    if target in ("jobs", "artifacts") and run_id is None:
      raise GitHubError(
        422, f"run_id is required when target is '{target}'.", "GET", ""
      )
    if target in ("job_steps", "job_logs") and job_id is None:
      raise GitHubError(
        422, f"job_id is required when target is '{target}'.", "GET", ""
      )
    if target == "artifact_download" and artifact_id is None:
      raise GitHubError(
        422, "artifact_id is required when target is 'artifact_download'.", "GET", ""
      )

    async def run():
      repo = self._repo(repo_full_name)
      if target == "workflows":
        data = await self._request(
          "GET",
          f"{API}{repo}/actions/workflows",
          params={
            "per_page": min(max(1, int(per_page)), 100),
            "page": int(page),
          },
        )
        return self._ok(
          {
            "workflows": data.get("workflows") or [],
            "total_count": data.get("total_count"),
          }
        )
      if target == "commit_runs":
        data = await self._request(
          "GET",
          f"{API}{repo}/actions/runs",
          params={
            "head_sha": commit_sha,
            "event": "pull_request",
            "per_page": 100,
          },
        )
        return self._ok({"workflow_runs": data.get("workflow_runs") or []})
      if target == "jobs":
        data = await self._request(
          "GET",
          f"{API}{repo}/actions/runs/{int(run_id or 0)}/jobs",
          params={"filter": "latest", "per_page": 100},
        )
        return self._ok({"jobs": data.get("jobs") or []})
      if target == "job_steps":
        data = await self._request(
          "GET", f"{API}{repo}/actions/jobs/{int(job_id or 0)}"
        )
        return self._ok({"steps": data.get("steps") or [], "job": data})
      if target == "job_logs":
        text = await self._request(
          "GET",
          f"{API}{repo}/actions/jobs/{int(job_id or 0)}/logs",
          accept="application/vnd.github+json",
          raw=True,
        )
        if len(text) > 400000:
          text = "[log truncated to the last 400000 characters]\n" + text[-400000:]
        return self._ok({"content": text})
      if target == "artifacts":
        data = await self._request(
          "GET",
          f"{API}{repo}/actions/runs/{int(run_id or 0)}/artifacts",
          params={"per_page": 100},
        )
        artifacts = data.get("artifacts") or []
        if name:
          artifacts = [a for a in artifacts if a.get("name") == name]
        return self._ok({"artifacts": artifacts})
      data = await self._request(
        "GET", f"{API}{repo}/actions/artifacts/{int(artifact_id or 0)}"
      )
      archive = file_name or f"{data.get('name') or 'artifact'}.zip"
      return self._ok(
        {
          "file_uri": {
            "download_url": data.get("archive_download_url"),
            "file_id": str(data.get("id")),
            "mime_type": "application/zip",
            "file_name": archive,
          },
          "artifact_id": int(artifact_id or 0),
          "file_name": archive,
          "mime_type": "application/zip",
          "size_in_bytes": data.get("size_in_bytes"),
          "expired": data.get("expired"),
          "artifact": data,
        }
      )

    return await self._read(
      "read inspect_workflow_run",
      str({
        "repo_full_name": repo_full_name,
        "target": target,
        "commit_sha": commit_sha,
        "run_id": run_id,
        "job_id": job_id,
        "artifact_id": artifact_id,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def rerun_failed_workflow_run_jobs(
    self,
    repo_full_name: str,
    run_id: int,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Rerun failed jobs in a GitHub workflow run.

    :param repo_full_name: owner/repo
    :param run_id: workflow run ID
    """
    repo = self._repo(repo_full_name)

    async def run():
      await self._request(
        "POST", f"{API}{repo}/actions/runs/{int(run_id)}/rerun-failed-jobs"
      )
      return self._ok({"success": True})

    return await self._write(
      f"rerun failed jobs of run {run_id} in {repo_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # ═════════════════════════════════ gists ═══════════════════════════════

  async def read_gist(
    self,
    gist_id: str | None = None,
    username: str | None = None,
    per_page: int = 30,
    page: int = 1,
    since: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read one GitHub gist by ID, or list gists for a user or signed-in account.

    :param gist_id: gist ID when reading one gist with files
    :param username: user login when listing another user's gists
    :param per_page: gists per page when listing
    :param page: page number when listing
    :param since: ISO 8601 timestamp filter when listing
    """

    async def run():
      if gist_id:
        data = await self._request("GET", f"{API}/gists/{self._seg(gist_id)}")
        return self._ok({"gist": data})
      url = (
        f"{API}/users/{self._seg(username)}/gists" if username else f"{API}/gists"
      )
      data = await self._request(
        "GET",
        url,
        params={
          "per_page": min(max(1, int(per_page)), 100),
          "page": int(page),
          "since": since,
        },
      )
      return self._ok({"gists": data})

    return await self._read(
      "read read_gist",
      str({
        "gist_id": gist_id,
        "username": username,
        "per_page": per_page,
        "page": page,
        "since": since,
      }),
      run,
      __user__,
      __event_call__,
    )

  async def write_gist(
    self,
    action: Literal["create", "update", "delete"] = "create",
    gist_id: str | None = None,
    files: dict | None = None,
    description: str | None = None,
    public: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create, update, or delete a GitHub gist.

    :param action: create, update, or delete
    :param gist_id: gist ID required for update or delete
    :param files: mapping of filename to {content: ...}
    :param description: gist description
    :param public: true creates a public gist when action is create
    """
    if action not in ("create", "update", "delete"):
      raise GitHubError(
        422, "action must be 'create', 'update', or 'delete'.", "POST", ""
      )
    if action == "create" and not files:
      raise GitHubError(422, "Give at least 1 file.", "POST", "")
    if action in ("update", "delete") and not gist_id:
      raise GitHubError(
        422, f"gist_id is required when action is '{action}'.", "POST", ""
      )

    async def run():
      if action == "create":
        data = await self._request(
          "POST",
          f"{API}/gists",
          payload={
            "files": files,
            "description": description,
            "public": bool(public),
          },
        )
        return self._ok({"gist": data})
      if action == "update":
        data = await self._request(
          "PATCH",
          f"{API}/gists/{self._seg(gist_id or '')}",
          payload={"files": files, "description": description},
        )
        return self._ok({"gist": data})
      await self._request("DELETE", f"{API}/gists/{self._seg(gist_id or '')}")
      return self._ok({"deleted": True, "gist_id": gist_id})

    summary = (
      f"create {'public' if public else 'secret'} gist with {len(files or {})} file(s)"
      if action == "create"
      else f"{action} gist {gist_id}"
    )
    detail = ", ".join(list(files or {})[:10]) or (gist_id or "")
    return await self._write(
      summary,
      detail,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )
