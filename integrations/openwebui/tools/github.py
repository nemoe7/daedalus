"""
title: GitHub
author: nemo
description: GitHub access for Open WebUI. Reads run freely. Every write passes a confirmation gate and a timeout, and the tool sends nothing when it cannot show the gate. Stdlib only.
required_open_webui_version: 0.10.0
version: 3.0.1
licence: daedalus Noncommercial License 1.0.0
"""

import asyncio
import base64
import json
import os
from collections.abc import Callable
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel

API = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"
USER_AGENT = "daedalus-openwebui-github"
JSON_ACCEPT = "application/vnd.github+json"


class GitHubError(Exception):
  """A GitHub call failed. The message keeps the status and the API text."""

  def __init__(self, status: int, message: str, method: str = "", url: str = ""):
    self.status = status
    self.message = message
    self.method = method
    self.url = url
    super().__init__(f"GitHub {status} on {method} {url}: {message}")


class Tools:
  class Valves(BaseModel):
    github_token: str = ""
    permissions: Literal["Always ask", "Allow reads", "Always allow"] = "Always ask"
    timeout_seconds: int = 60
    http_timeout_seconds: int = 30
    api_version: str = "2022-11-28"
    max_files_per_commit: int = 30
    max_tree_entries: int = 2000

  class UserValves(BaseModel):
    mode: str = "default"
    timeout_seconds: int = 0

  def __init__(self):
    self.valves = self.Valves()

  # ---------------------------------------------------------------- gate

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

    Always ask holds every call, reads included. Allow reads frees the reads
    and holds the gated calls. Always allow holds nothing.
    """
    user_mode = self._user_valve(__user__, "mode")
    if user_mode and user_mode != "default":
      return str(user_mode)
    return str(self.valves.permissions or "Always ask")

  def _wait_seconds(self, __user__: dict | None = None) -> int:
    """The confirmation wait: the user value wins, then the tool value. Zero keeps the tool value."""
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

    The confirmation travels over the socket of the tab that started the chat. A
    page refresh drops the dialog and the server would wait forever, because
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

  async def _read(
    self,
    action: str,
    detail: str,
    fn: Callable,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> Any:
    """Run one read behind the read gate.

    Always ask holds the read at the same confirmation as a write. Allow reads
    and Always allow let it run.
    """
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
    """Run one write behind the gate. deny => never asked, timeout => never sent."""
    if self._permissions(__user__) != "Always allow" and not await self._ask(
      action, detail, __event_call__, __user__
    ):
      return {
        "result": {
          "denied": True,
          "action": action,
          "reason": (
            "You did not confirm, so I sent nothing. A missing dialog, a closed tab "
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
        401, "No GitHub token: set it in the tool valves or GITHUB_TOKEN.", "", ""
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
    """The one HTTP call. Blocking on purpose: every caller awaits it in a thread."""
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
        "; ".join(e.get("message", "") for e in data["errors"]),
        "POST",
        GRAPHQL_URL,
      )
    return data.get("data") or {}

  @staticmethod
  def _ok(payload: Any) -> dict:
    return {"result": payload}

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
      raise GitHubError(422, f"Cannot read a repository from {repository_url}.", "", "")
    raise GitHubError(
      422, "Give one repository selector: full name, id or url.", "", ""
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

  # ═══════════════════════════════ issues, comments, reactions ═══════════

  async def fetch_issue(
    self,
    issue_number: int,
    repository_full_name: str | None = None,
    repository_id: int | None = None,
    repository_url: str | None = None,
  ) -> dict:
    """Read one issue. Exactly one repository selector.

    :param issue_number: the issue number
    :param repository_full_name: owner/repo
    :param repository_id: numeric repository id
    :param repository_url: repository html or api url
    """
    repo = self._repo_of(repository_full_name, repository_id, repository_url)
    issue = await self._request("GET", f"{API}{repo}/issues/{int(issue_number)}")
    return self._ok(self._issue_shape(issue))

  async def fetch_issue_comments(self, repo_full_name: str, issue_number: int) -> dict:
    """Read an issue's comments, first page.

    :param repo_full_name: owner/repo
    :param issue_number: the issue number
    """
    repo = self._repo(repo_full_name)
    comments = await self._request(
      "GET",
      f"{API}{repo}/issues/{int(issue_number)}/comments",
      params={"per_page": 100},
    )
    return self._ok({"comments": comments})

  async def list_recent_issues(
    self,
    top_k: int = 20,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List the issues of the signed-in user, most recently updated first.

    :param top_k: how many to return
    """

    async def run(top_k=top_k, __user__=__user__, __event_call__=__event_call__):
      issues = await self._request(
        "GET",
        f"{API}/issues",
        params={
          "filter": "all",
          "state": "all",
          "sort": "updated",
          "direction": "desc",
          "per_page": min(max(1, int(top_k)), 100),
        },
      )
      return self._ok({"issues": issues})

    return await self._read(
      "read list_recent_issues",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def add_comment_to_issue(
    self,
    repo_full_name: str,
    pr_number: int,
    comment: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create a top-level comment on an issue or pull request conversation.

    :param repo_full_name: owner/repo
    :param pr_number: the issue or pull request number
    :param comment: the markdown body
    """
    repo = self._repo(repo_full_name)

    async def run():
      data = await self._request(
        "POST",
        f"{API}{repo}/issues/{int(pr_number)}/comments",
        payload={"body": comment},
      )
      return self._ok(data)

    return await self._write(
      f"comment on #{pr_number} in {repo_full_name}",
      comment,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def update_issue_comment(
    self,
    repo_full_name: str,
    comment_id: int,
    comment: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Edit an issue comment.

    :param repo_full_name: owner/repo
    :param comment_id: the comment id
    :param comment: the new markdown body
    """
    repo = self._repo(repo_full_name)

    async def run():
      data = await self._request(
        "PATCH",
        f"{API}{repo}/issues/comments/{int(comment_id)}",
        payload={"body": comment},
      )
      return self._ok(data)

    return await self._write(
      f"edit comment {comment_id} in {repo_full_name}",
      comment,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
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
    """Create an issue.

    :param repository_full_name: owner/repo
    :param title: the issue title
    :param body: the markdown body
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
      f"open the issue '{title}' in {repository_full_name}",
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
    """Edit an issue. Labels replace the current set, unlike add_issue_labels.

    :param repository_full_name: owner/repo
    :param issue_number: the issue number
    :param title: new title
    :param body: new markdown body
    :param state: open or closed
    :param state_reason: completed, not_planned, duplicate or reopened
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

  async def add_issue_assignees(
    self,
    repository_full_name: str,
    issue_number: int,
    assignees: list[str],
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Add up to 10 assignees to an issue.

    :param repository_full_name: owner/repo
    :param issue_number: the issue number
    :param assignees: usernames to add
    """
    repo = self._repo(repository_full_name)

    async def run():
      issue = await self._request(
        "POST",
        f"{API}{repo}/issues/{int(issue_number)}/assignees",
        payload={"assignees": assignees},
      )
      return self._ok(self._issue_shape(issue))

    return await self._write(
      f"add {assignees} to issue #{issue_number} in {repository_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def remove_issue_assignees(
    self,
    repository_full_name: str,
    issue_number: int,
    assignees: list[str],
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Remove assignees from an issue.

    :param repository_full_name: owner/repo
    :param issue_number: the issue number
    :param assignees: usernames to remove
    """
    repo = self._repo(repository_full_name)

    async def run():
      issue = await self._request(
        "DELETE",
        f"{API}{repo}/issues/{int(issue_number)}/assignees",
        payload={"assignees": assignees},
      )
      return self._ok(self._issue_shape(issue))

    return await self._write(
      f"remove {assignees} from issue #{issue_number} in {repository_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def add_issue_labels(
    self,
    repository_full_name: str,
    issue_number: int,
    labels: list[str],
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Add labels to an issue, keeping the ones already there.

    :param repository_full_name: owner/repo
    :param issue_number: the issue number
    :param labels: label names to add
    """
    repo = self._repo(repository_full_name)

    async def run():
      data = await self._request(
        "POST",
        f"{API}{repo}/issues/{int(issue_number)}/labels",
        payload={"labels": labels},
      )
      issue = await self._request("GET", f"{API}{repo}/issues/{int(issue_number)}")
      return self._ok(self._issue_shape(issue) | {"labels": data})

    return await self._write(
      f"add labels {labels} to issue #{issue_number} in {repository_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def remove_issue_label(
    self,
    repository_full_name: str,
    issue_number: int,
    label: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Remove one label from an issue.

    :param repository_full_name: owner/repo
    :param issue_number: the issue number
    :param label: the label name
    """
    repo = self._repo(repository_full_name)

    async def run():
      issue = await self._request(
        "DELETE", f"{API}{repo}/issues/{int(issue_number)}/labels/{self._path(label)}"
      )
      return self._ok(self._issue_shape(issue))

    return await self._write(
      f"remove the label '{label}' from issue #{issue_number} in {repository_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # ══════════════════════════════ pull request reviews ══════════════════

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

  async def list_pull_request_reviews(
    self,
    repo_full_name: str,
    pr_number: int,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List the submitted reviews of a pull request.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    """

    async def run(
      repo_full_name=repo_full_name,
      pr_number=pr_number,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      owner, name = repo_full_name.strip().strip("/").split("/")
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
      reviews = (
        ((data.get("repository") or {}).get("pullRequest") or {}).get("reviews") or {}
      ).get("nodes") or []
      return self._ok({"reviews": reviews})

    return await self._read(
      "read list_pull_request_reviews",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def list_pull_request_review_threads(
    self,
    repo_full_name: str,
    pr_number: int,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List the inline review threads of a pull request, with their resolution state.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    """

    async def run(
      repo_full_name=repo_full_name,
      pr_number=pr_number,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      owner, name = repo_full_name.strip().strip("/").split("/")
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
      threads = (
        ((data.get("repository") or {}).get("pullRequest") or {}).get("reviewThreads")
        or {}
      ).get("nodes") or []
      return self._ok({"review_threads": threads})

    return await self._read(
      "read list_pull_request_review_threads",
      str(locals().get("name", "") or locals().get("path", "") or ""),
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
    """Submit a pull request review. COMMENT and REQUEST_CHANGES need a review body.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    :param action: COMMENT, APPROVE or REQUEST_CHANGES
    :param review: the review body
    :param file_comments: inline comments, each with path, body and line or position
    :param commit_id: the commit the review applies to
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
      f"submit a {action} review on PR #{pr_number} in {repo_full_name}",
      review or action,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def reply_to_review_comment(
    self,
    repo_full_name: str,
    pr_number: int,
    comment_id: int,
    comment: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Reply to a top-level inline review comment.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    :param comment_id: the top-level review comment id
    :param comment: the markdown reply
    """
    repo = self._repo(repo_full_name)

    async def run():
      data = await self._request(
        "POST",
        f"{API}{repo}/pulls/{int(pr_number)}/comments/{int(comment_id)}/replies",
        payload={"body": comment},
      )
      return self._ok(data)

    return await self._write(
      f"reply to review comment {comment_id} on PR #{pr_number} in {repo_full_name}",
      comment,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def update_review_comment(
    self,
    repo_full_name: str,
    comment_id: int,
    comment: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Edit an inline review comment.

    :param repo_full_name: owner/repo
    :param comment_id: the review comment id
    :param comment: the new markdown body
    """
    repo = self._repo(repo_full_name)

    async def run():
      data = await self._request(
        "PATCH",
        f"{API}{repo}/pulls/comments/{int(comment_id)}",
        payload={"body": comment},
      )
      return self._ok(data)

    return await self._write(
      f"edit review comment {comment_id} in {repo_full_name}",
      comment,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def resolve_review_thread(
    self,
    thread_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Resolve a review thread by its GraphQL node id.

    :param thread_id: the thread node id, from list_pull_request_review_threads
    """

    async def run():
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
      return self._ok({"review_thread": thread})

    return await self._write(
      f"resolve review thread {thread_id}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def unresolve_review_thread(
    self,
    thread_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Reopen a resolved review thread by its GraphQL node id.

    :param thread_id: the thread node id, from list_pull_request_review_threads
    """

    async def run():
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

    return await self._write(
      f"reopen review thread {thread_id}",
      "",
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
    """Dismiss a pull request review by its GraphQL node id.

    :param review_id: the review node id, from list_pull_request_reviews
    :param message: the dismissal message
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
      review = (data.get("dismissPullRequestReview") or {}).get(
        "pullRequestReview"
      ) or {}
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
    """Request reviewers on a pull request.

    :param repository_full_name: owner/repo
    :param pr_number: the pull request number
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

  # ═════════════════════════ pull request info and diff ═════════════════

  async def get_pr_info(
    self,
    repository_full_name: str,
    pr_number: int,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read pull request metadata, without the diff.

    :param repository_full_name: owner/repo
    :param pr_number: the pull request number
    """

    async def run(
      repository_full_name=repository_full_name,
      pr_number=pr_number,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      repo = self._repo(repository_full_name)
      pull = await self._request("GET", f"{API}{repo}/pulls/{int(pr_number)}")
      return self._ok(pull)

    return await self._read(
      "read get_pr_info",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def fetch_pr(self, repo_full_name: str, pr_number: int) -> dict:
    """Read a pull request with its title, url and unified diff.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    """
    repo = self._repo(repo_full_name)
    pull = await self._request("GET", f"{API}{repo}/pulls/{int(pr_number)}")
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

  async def fetch_pr_comments(self, repo_full_name: str, pr_number: int) -> dict:
    """Read a pull request discussion: issue comments, review comments and reviews.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    """
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
      [{"kind": "issue_comment", **c} for c in issue_comments]
      + [{"kind": "review_comment", **c} for c in review_comments]
      + [{"kind": "review", **r} for r in reviews]
    )
    return self._ok({"comments": comments})

  async def get_pr_diff(
    self,
    repo_full_name: str,
    pr_number: int,
    format: str = "diff",
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Read only the diff or the patch of a pull request.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    :param format: diff or patch
    """

    async def run(
      repo_full_name=repo_full_name,
      pr_number=pr_number,
      format=format,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      repo = self._repo(repo_full_name)
      media = (
        "application/vnd.github.patch"
        if format == "patch"
        else "application/vnd.github.diff"
      )
      diff = await self._request(
        "GET", f"{API}{repo}/pulls/{int(pr_number)}", accept=media, raw=True
      )
      return self._ok({"diff": diff})

    return await self._read(
      "read get_pr_diff",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def fetch_pr_patch(self, repo_full_name: str, pr_number: int) -> dict:
    """Read the per-file patches of a pull request, first page.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    """
    repo = self._repo(repo_full_name)
    files = await self._request(
      "GET", f"{API}{repo}/pulls/{int(pr_number)}/files", params={"per_page": 100}
    )
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

  async def list_pr_changed_filenames(
    self,
    repo_full_name: str,
    pr_number: int,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List the changed filenames of a pull request, first page.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    """

    async def run(
      repo_full_name=repo_full_name,
      pr_number=pr_number,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      repo = self._repo(repo_full_name)
      files = await self._request(
        "GET", f"{API}{repo}/pulls/{int(pr_number)}/files", params={"per_page": 100}
      )
      return self._ok({"filenames": [f.get("filename") for f in files]})

    return await self._read(
      "read list_pr_changed_filenames",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def fetch_pr_file_patch(
    self, repo_full_name: str, pr_number: int, path: str
  ) -> dict:
    """Read the patch of one changed file. The path comes from list_pr_changed_filenames.

    :param repo_full_name: owner/repo
    :param pr_number: the pull request number
    :param path: the file path as it appears in the pull request
    """
    repo = self._repo(repo_full_name)
    for page in (1, 2, 3):
      files = await self._request(
        "GET",
        f"{API}{repo}/pulls/{int(pr_number)}/files",
        params={"per_page": 100, "page": page},
      )
      for f in files:
        if f.get("filename") == path:
          return self._ok(
            {
              "patch": {
                "filename": f.get("filename"),
                "patch": f.get("patch"),
                "url": f.get("blob_url"),
                "title": f.get("filename"),
              }
            }
          )
      if len(files) < 100:
        break
    return self._ok({"patch": None})

  # ═════════════════════════ pull request mutations ═════════════════════

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
    """Open a pull request. The call needs a title unless it names an issue.

    :param repository_full_name: owner/repo
    :param title: the pull request title
    :param body: the markdown body
    :param head_branch: the branch that holds the change
    :param base_branch: the branch to merge into
    :param draft: open as draft
    :param head: head as owner:branch, when the branch lives in a fork
    :param base: base ref, when base_branch is not used
    :param issue: an issue number to turn into a pull request
    :param head_repo: the fork full name, paired with head_branch
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
      f"open a pull request in {repository_full_name}",
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
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Edit a pull request.

    :param repository_full_name: owner/repo
    :param pr_number: the pull request number
    :param title: new title
    :param body: new markdown body
    :param state: open or closed
    :param base_branch: new base branch
    :param maintainer_can_modify: allow maintainer edits
    """
    repo = self._repo(repository_full_name)

    async def run():
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
      return self._ok(pull)

    return await self._write(
      f"update PR #{pr_number} in {repository_full_name}",
      f"title={title!r} state={state!r} base={base_branch!r}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def convert_pull_request_to_draft(
    self,
    repository_full_name: str,
    pr_number: int,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Turn a pull request into a draft.

    :param repository_full_name: owner/repo
    :param pr_number: the pull request number
    """

    async def run():
      node = await self._pr_node_id(repository_full_name, pr_number)
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
      return self._ok(
        (data.get("convertPullRequestToDraft") or {}).get("pullRequest") or {}
      )

    return await self._write(
      f"convert PR #{pr_number} to draft in {repository_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def mark_pull_request_ready_for_review(
    self,
    repository_full_name: str,
    pr_number: int,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Mark a draft pull request ready for review.

    :param repository_full_name: owner/repo
    :param pr_number: the pull request number
    """

    async def run():
      node = await self._pr_node_id(repository_full_name, pr_number)
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
      return self._ok(
        (data.get("markPullRequestReadyForReview") or {}).get("pullRequest") or {}
      )

    return await self._write(
      f"mark PR #{pr_number} ready for review in {repository_full_name}",
      "",
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
    """Merge a pull request.

    :param repository_full_name: owner/repo
    :param pr_number: the pull request number
    :param merge_method: merge, squash or rebase
    :param commit_title: the commit title
    :param commit_message: the commit body
    :param expected_head_sha: refuse the merge unless head still points here
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

  async def label_pr(
    self,
    repository_full_name: str,
    pr_number: int,
    label: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Add one label to a pull request.

    :param repository_full_name: owner/repo
    :param pr_number: the pull request number
    :param label: the label name
    """
    repo = self._repo(repository_full_name)

    async def run():
      await self._request(
        "POST",
        f"{API}{repo}/issues/{int(pr_number)}/labels",
        payload={"labels": [label]},
      )
      return self._ok({"success": True})

    return await self._write(
      f"label PR #{pr_number} '{label}' in {repository_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # ═════════════════════════ files and git data ═════════════════════════

  async def fetch_file(
    self,
    repository_full_name: str,
    path: str,
    ref: str | None = None,
    encoding: str = "utf-8",
    start_line: int | None = None,
    end_line: int | None = None,
  ) -> dict:
    """Read a file, base64 decoded. A directory returns its listing as JSON text.

    :param repository_full_name: owner/repo
    :param path: the path inside the repository
    :param ref: the branch, tag or commit. The default branch when absent
    :param encoding: utf-8 (decoded) or base64 (raw)
    :param start_line: keep from this line, 1-based
    :param end_line: keep to this line, inclusive
    """
    repo = self._repo(repository_full_name)
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
          "display_url": data[0].get("html_url").rsplit("/", 1)[0] if data else None,
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
        if data.get("encoding") != "base64"
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

  async def fetch_blob(self, repository_full_name: str, blob_sha: str) -> dict:
    """Read a blob by SHA, base64 decoded.

    :param repository_full_name: owner/repo
    :param blob_sha: the blob SHA
    """
    repo = self._repo(repository_full_name)
    data = await self._request("GET", f"{API}{repo}/git/blobs/{self._seg(blob_sha)}")
    content = data.get("content") or ""
    if data.get("encoding") == "base64":
      content = base64.b64decode(content).decode("utf-8", "replace")
    return self._ok(
      {"content": content, "sha": data.get("sha"), "size": data.get("size")}
    )

  async def fetch(self, url: str) -> dict:
    """Read one GitHub page or API url: repository, directory, file, issue, PR, commit, branch, run.

    :param url: a github.com or api.github.com URL
    """
    parsed = urlparse(url)
    if parsed.netloc in ("api.github.com",):
      path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
      data = await self._request("GET", f"https://api.github.com{path}", raw=True)
      return self._ok(
        {"content": data, "url": url, "title": None, "modified_date": None}
      )
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.netloc != "github.com" or len(parts) < 2:
      raise GitHubError(
        422,
        f"fetch reads github.com or api.github.com URLs only, not {url}.",
        "GET",
        url,
      )
    owner, repo_name = parts[0], parts[1]
    rest = parts[2:]
    repo = self._repo(f"{owner}/{repo_name}")
    title = f"{owner}/{repo_name}"
    if not rest:
      data = await self._request("GET", f"{API}{repo}")
      return self._ok(
        {
          "content": json.dumps(data, indent=2),
          "title": title,
          "url": url,
          "modified_date": data.get("updated_at"),
        }
      )
    kind, tail = rest[0], rest[1:]
    if kind in ("tree", "blob") and tail:
      ref, path = tail[0], "/".join(tail[1:])
      if kind == "tree":
        data = await self._request(
          "GET", f"{API}{repo}/contents/{self._path(path)}", params={"ref": ref}
        )
        return self._ok(
          {
            "content": json.dumps(data, indent=2)[:400000],
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
      return self._ok(
        {
          "content": content,
          "title": f"{title}/{path}",
          "url": url,
          "modified_date": None,
        }
      )
    if kind == "issues" and tail:
      data = await self._request("GET", f"{API}{repo}/issues/{int(tail[0])}")
      return self._ok(
        {
          "content": f"#{data.get('number')} {data.get('title')}\n\n{data.get('body') or ''}",
          "title": data.get("title"),
          "url": url,
          "modified_date": data.get("updated_at"),
        }
      )
    if kind == "pull" and tail:
      pull = await self._request("GET", f"{API}{repo}/pulls/{int(tail[0])}")
      diff = await self._request(
        "GET",
        f"{API}{repo}/pulls/{int(tail[0])}",
        accept="application/vnd.github.diff",
        raw=True,
      )
      return self._ok(
        {
          "content": diff,
          "title": f"#{pull.get('number')} {pull.get('title')}",
          "url": url,
          "modified_date": pull.get("updated_at"),
        }
      )
    if kind == "commit" and tail:
      commit = await self._request("GET", f"{API}{repo}/commits/{self._seg(tail[0])}")
      lines = ((commit.get("commit") or {}).get("message") or "").splitlines()
      return self._ok(
        {
          "content": json.dumps(commit, indent=2),
          "title": lines[0] if lines else "",
          "url": url,
          "modified_date": ((commit.get("commit") or {}).get("committer") or {}).get(
            "date"
          ),
        }
      )
    if kind == "branches":
      data = await self._request(
        "GET", f"{API}{repo}/branches", params={"per_page": 100}
      )
      return self._ok(
        {
          "content": json.dumps(data, indent=2),
          "title": f"{title} branches",
          "url": url,
          "modified_date": None,
        }
      )
    if kind == "actions" and tail[:1] == ["runs"]:
      run_id = tail[1] if len(tail) > 1 else None
      if run_id:
        data = await self._request("GET", f"{API}{repo}/actions/runs/{int(run_id)}")
      else:
        data = await self._request(
          "GET", f"{API}{repo}/actions/runs", params={"per_page": 20}
        )
      return self._ok(
        {
          "content": json.dumps(data, indent=2),
          "title": f"{title} actions",
          "url": url,
          "modified_date": None,
        }
      )
    if kind == "search":
      query = parse_qs(parsed.query).get("q", [""])[0]
      return self._ok(
        {
          "content": json.dumps(await self._search_code(query, 20), indent=2),
          "title": "search",
          "url": url,
          "modified_date": None,
        }
      )
    raise GitHubError(
      422, f"fetch does not read this github.com path yet: {parsed.path}.", "GET", url
    )

  async def create_branch(
    self,
    repository_full_name: str,
    branch_name: str,
    sha: str | None = None,
    base_ref: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create a branch. Give exactly one of sha or base_ref.

    :param repository_full_name: owner/repo
    :param branch_name: the new branch name
    :param sha: the commit to point at
    :param base_ref: a branch or tag to start from
    """
    repo = self._repo(repository_full_name)

    async def run():
      target = sha
      if not target:
        if not base_ref:
          raise GitHubError(422, "Give exactly one of sha or base_ref.", "POST", "")
        ref = await self._request(
          "GET", f"{API}{repo}/git/ref/heads/{self._path(base_ref)}"
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

    return await self._write(
      f"create the branch {branch_name} in {repository_full_name}",
      f"from {sha or base_ref}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def update_ref(
    self,
    repository_full_name: str,
    branch_name: str,
    sha: str,
    force: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Move a branch to another commit.

    :param repository_full_name: owner/repo
    :param branch_name: the branch name
    :param sha: the commit to point at
    :param force: allow a non fast-forward move
    """
    repo = self._repo(repository_full_name)

    async def run():
      await self._request(
        "PATCH",
        f"{API}{repo}/git/refs/heads/{self._path(branch_name)}",
        payload={"sha": sha, "force": force},
      )
      return self._ok({"success": True})

    return await self._write(
      f"move the branch {branch_name} to {sha} in {repository_full_name}",
      "force" if force else "fast-forward only",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def create_file(
    self,
    repository_full_name: str,
    path: str,
    content: str,
    message: str,
    branch: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create a file with one commit.

    :param repository_full_name: owner/repo
    :param path: the new file path
    :param content: the file text
    :param message: the commit message
    :param branch: the target branch
    """
    repo = self._repo(repository_full_name)

    async def run():
      data = await self._request(
        "PUT",
        f"{API}{repo}/contents/{self._path(path)}",
        payload={
          "message": message,
          "content": base64.b64encode(content.encode()).decode(),
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
      f"create {path} in {repository_full_name}",
      message,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def update_file(
    self,
    repository_full_name: str,
    path: str,
    content: str,
    message: str,
    sha: str,
    branch: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Replace a file with one commit. The sha comes from fetch_file.

    :param repository_full_name: owner/repo
    :param path: the file path
    :param content: the new file text
    :param message: the commit message
    :param sha: the current blob sha
    :param branch: the target branch
    """
    repo = self._repo(repository_full_name)

    async def run():
      data = await self._request(
        "PUT",
        f"{API}{repo}/contents/{self._path(path)}",
        payload={
          "message": message,
          "content": base64.b64encode(content.encode()).decode(),
          "sha": sha,
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
      f"update {path} in {repository_full_name}",
      message,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def delete_file(
    self,
    repository_full_name: str,
    path: str,
    message: str,
    sha: str,
    branch: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Delete a file with one commit. The sha comes from fetch_file.

    :param repository_full_name: owner/repo
    :param path: the file path
    :param message: the commit message
    :param sha: the current blob sha
    :param branch: the target branch
    """
    repo = self._repo(repository_full_name)

    async def run():
      data = await self._request(
        "DELETE",
        f"{API}{repo}/contents/{self._path(path)}",
        payload={"message": message, "sha": sha, "branch": branch},
      )
      return self._ok({"commit_sha": (data.get("commit") or {}).get("sha")})

    return await self._write(
      f"delete {path} in {repository_full_name}",
      message,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
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
    """List commits, newest first, with an optional path filter.

    :param repository_full_name: owner/repo
    :param path: only the commits that touch this path
    :param sha: the branch, tag or commit to walk from
    :param since: an ISO 8601 timestamp, for example 2026-01-01T00:00:00Z
    :param until: an ISO 8601 timestamp
    :param per_page: commits per page
    :param page: the page number
    """

    async def run(
      repository_full_name=repository_full_name,
      path=path,
      sha=sha,
      since=since,
      until=until,
      per_page=per_page,
      page=page,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      repo = self._repo(repository_full_name)
      data = await self._request(
        "GET",
        f"{API}{repo}/commits",
        params={
          "path": path,
          "sha": sha,
          "since": since,
          "until": until,
          "per_page": min(max(1, int(per_page)), 100),
          "page": int(page),
        },
      )
      return self._ok({"commits": data})

    return await self._read(
      "read list_commits",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def list_tree(
    self,
    repository_full_name: str,
    ref: str | None = None,
    tree_sha: str | None = None,
    recursive: bool = True,
    path_prefix: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List the tree of a branch, tag or commit, recursive by default.

    :param repository_full_name: owner/repo
    :param ref: the branch or tag name, the default branch when absent
    :param tree_sha: the tree SHA, in place of ref
    :param recursive: read every level
    :param path_prefix: keep the entries under this path
    """

    async def run(
      repository_full_name=repository_full_name,
      ref=ref,
      tree_sha=tree_sha,
      recursive=recursive,
      path_prefix=path_prefix,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
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
      if path_prefix:
        entries = [
          item for item in entries if (item.get("path") or "").startswith(path_prefix)
        ]
      limit = min(max(1, int(self.valves.max_tree_entries)), 5000)
      return self._ok(
        {
          "tree": entries[:limit],
          "count": min(len(entries), limit),
          "truncated": bool(data.get("truncated")) or len(entries) > limit,
        }
      )

    return await self._read(
      "read list_tree",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def code_scanning_alerts(
    self,
    repo_full_name: str,
    alert_number: int | None = None,
    state: str | None = "open",
    tool_name: str | None = None,
    ref: str | None = None,
    severity: str | None = None,
    sort: str = "created",
    direction: str = "desc",
    per_page: int = 30,
    page: int = 1,
  ) -> dict:
    """List the code scanning alerts of a repository, or read 1 alert with its instances.

    :param repo_full_name: owner/repo
    :param alert_number: the alert number. Set it to read that alert and its instances
    :param state: open, dismissed or fixed
    :param tool_name: the scanning tool, for example CodeQL
    :param ref: a branch, tag or commit
    :param severity: note, warning or error
    :param sort: created or updated
    :param direction: asc or desc
    :param per_page: alerts per page
    :param page: the page number
    """
    repo = self._repo(repo_full_name)
    if alert_number is not None:
      number = int(alert_number)
      alert = await self._request("GET", f"{API}{repo}/code-scanning/alerts/{number}")
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
        "per_page": min(max(1, int(per_page)), 100),
        "page": int(page),
      },
    )
    return self._ok({"alerts": alerts})

  async def secret_scanning_alerts(
    self,
    repo_full_name: str,
    alert_number: int | None = None,
    state: str | None = "open",
    resolution: str | None = None,
    secret_type: str | None = None,
    validity: str | None = None,
    sort: str = "created",
    direction: str = "desc",
    per_page: int = 30,
    page: int = 1,
  ) -> dict:
    """List the secret scanning alerts of a repository, or read 1 alert with its locations.

    :param repo_full_name: owner/repo
    :param alert_number: the alert number. Set it to read that alert and its locations
    :param state: open or resolved
    :param resolution: false_positive, wont_fix, revoked or used_in_tests
    :param secret_type: the secret type slug, for example github_personal_access_token
    :param validity: active, inactive or unknown
    :param sort: created or updated
    :param direction: asc or desc
    :param per_page: alerts per page
    :param page: the page number
    """
    repo = self._repo(repo_full_name)
    if alert_number is not None:
      number = int(alert_number)
      alert = await self._request("GET", f"{API}{repo}/secret-scanning/alerts/{number}")
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
        "per_page": min(max(1, int(per_page)), 100),
        "page": int(page),
      },
    )
    return self._ok({"alerts": alerts})

  async def dependabot_alerts(
    self,
    repo_full_name: str,
    alert_number: int | None = None,
    state: str | None = "open",
    severity: str | None = None,
    ecosystem: str | None = None,
    package: str | None = None,
    manifest: str | None = None,
    scope: str | None = None,
    sort: str = "created",
    direction: str = "desc",
    per_page: int = 30,
    page: int = 1,
  ) -> dict:
    """List the Dependabot alerts of a repository, or read 1 alert.

    :param repo_full_name: owner/repo
    :param alert_number: the alert number. Set it to read that alert
    :param state: open, dismissed, fixed or auto_dismissed
    :param severity: low, medium, high or critical
    :param ecosystem: the package ecosystem, for example pip or npm
    :param package: the package name
    :param manifest: the manifest path
    :param scope: development or runtime
    :param sort: created or updated
    :param direction: asc or desc
    :param per_page: alerts per page
    :param page: the page number
    """
    repo = self._repo(repo_full_name)
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
        "per_page": min(max(1, int(per_page)), 100),
        "page": int(page),
      },
    )
    return self._ok({"alerts": alerts})

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
    """Open or update a pull request from a file list, in 1 confirmed call.

    Each files entry holds path and content. An entry with delete true removes the file. The call
    creates the blobs, the tree and the commit, then the branch and the pull request, or it moves
    the branch when it already exists and returns its open pull request.

    :param repository_full_name: owner/repo
    :param files: the changes, each {path, content} or {path, delete: true}
    :param branch: the branch to create or move
    :param title: the pull request title, required when the pull request is new
    :param body: the pull request body
    :param base: the base branch, the default branch when absent
    :param commit_message: the commit message, the title when absent
    :param draft: open the pull request as a draft
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

  async def compare_commits(self, repo_full_name: str, base: str, head: str) -> dict:
    """Compare two refs and return the commit and file changes.

    :param repo_full_name: owner/repo
    :param base: the base ref
    :param head: the head ref
    """
    repo = self._repo(repo_full_name)
    data = await self._request(
      "GET", f"{API}{repo}/compare/{self._path(base)}...{self._path(head)}"
    )
    return self._ok(
      {**data, "repository_full_name": repo_full_name, "base": base, "head": head}
    )

  # ═════════════════════ repository metadata and discovery ══════════════

  async def get_repo(
    self,
    repository_full_name: str | None = None,
    repository_id: int | None = None,
    repository_url: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Fetch repository metadata. Normally exactly one selector.

    :param repository_full_name: owner/repo
    :param repository_id: the numeric repository id
    :param repository_url: the repository html or api url
    """

    async def run(
      repository_full_name=repository_full_name,
      repository_id=repository_id,
      repository_url=repository_url,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      path = self._repo_of(repository_full_name, repository_id, repository_url)
      data = await self._request("GET", f"{API}{path}")
      return self._ok({**data, "repository_full_name": data.get("full_name")})

    return await self._read(
      "read get_repo",
      str(locals().get("name", "") or locals().get("path", "") or ""),
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
    """List the repositories of the signed-in user, or of one owner.

    :param page_size: repositories per page
    :param page_offset: how many to skip
    :param owner: an org or user login
    :param include_search_index_status: no effect on REST
    """

    async def run(
      page_size=page_size,
      page_offset=page_offset,
      owner=owner,
      include_search_index_status=include_search_index_status,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      size = min(max(1, int(page_size)), 100)
      page = int(page_offset) // size + 1
      if owner:
        path = f"{API}/users/{self._seg(owner)}/repos"
      else:
        path = f"{API}/user/repos"
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
      return self._ok({"repositories": data})

    return await self._read(
      "read list_repositories",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def search_repositories(
    self,
    query: str,
    per_page: int | None = None,
    page: int = 1,
    org: str | None = None,
    topn: int | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Search repositories by name, description or topic.

    :param query: the search terms, with any GitHub qualifiers
    :param per_page: results per page
    :param page: the page number
    :param org: restrict to one organization
    :param topn: results wanted, used when per_page is absent
    """

    async def run(
      query=query,
      per_page=per_page,
      page=page,
      org=org,
      topn=topn,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      terms = [query] + ([f"org:{org}"] if org else [])
      data = await self._request(
        "GET",
        f"{API}/search/repositories",
        params={
          "q": " ".join(terms),
          "per_page": min(per_page or topn or 20, 100),
          "page": page,
        },
      )
      return self._ok(
        {
          "repositories": data.get("items") or [],
          "archive_filter_applied": None,
          "total_count": data.get("total_count"),
        }
      )

    return await self._read(
      "read search_repositories",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def get_repo_collaborator_permission(
    self,
    repository_full_name: str,
    username: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Report the permission level of a collaborator.

    :param repository_full_name: owner/repo
    :param username: the GitHub login
    """

    async def run(
      repository_full_name=repository_full_name,
      username=username,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      repo = self._repo(repository_full_name)
      data = await self._request(
        "GET", f"{API}{repo}/collaborators/{self._seg(username)}/permission"
      )
      return self._ok({"permission": data.get("permission"), "user": data.get("user")})

    return await self._read(
      "read get_repo_collaborator_permission",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  # ═════════════════════════════════ search ═════════════════════════════

  async def _search_code(
    self, query: str, topn: int, repo: str | None = None, org: str | None = None
  ) -> list:
    terms = [query]
    if repo:
      terms.append(f"repo:{repo}")
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

  async def search(
    self,
    query: str,
    topn: int = 20,
    repository_name: str | list[str] | None = None,
    org: str | None = None,
  ) -> dict:
    """Search code and file names. Use fetch_file for full contents.

    :param query: the search terms, with any GitHub qualifiers
    :param topn: results wanted
    :param repository_name: one repository or a list to restrict to
    :param org: restrict to one organization
    """
    if isinstance(repository_name, list):
      repo = " ".join(repository_name)
    else:
      repo = repository_name
    return self._ok({"results": await self._search_code(query, topn, repo, org)})

  async def search_branches(
    self,
    owner: str,
    repo_name: str,
    query: str,
    page_size: int = 20,
    cursor: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Search the branches of one repository by name.

    :param owner: the repository owner
    :param repo_name: the repository name
    :param query: the text to match in branch names
    :param page_size: branches per page
    :param cursor: the page number from the last call
    """

    async def run(
      owner=owner,
      repo_name=repo_name,
      query=query,
      page_size=page_size,
      cursor=cursor,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      repo = self._repo(f"{owner}/{repo_name}")
      page = int(cursor) if cursor and str(cursor).isdigit() else 1
      size = min(max(1, int(page_size)), 100)
      data = await self._request(
        "GET", f"{API}{repo}/branches", params={"per_page": size, "page": page}
      )
      needle = (query or "").lower()
      branches = [
        {"branch": item.get("name"), "sha": (item.get("commit") or {}).get("sha")}
        for item in data
        if needle in (item.get("name") or "").lower()
      ]
      token = str(page + 1) if len(data) == size else None
      return self._ok({"branches": branches, "cursor": token})

    return await self._read(
      "read search_branches",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def _search_issues(
    self,
    query: str,
    kind: str,
    topn: int,
    repository_full_name: str | list[str] | None,
    repository_id: int | list[int] | None,
    repository_url: str | list[str] | None,
    org: str | None,
    sort: str | None,
    order: str | None,
    state: str | None,
  ) -> dict:
    terms = [query]
    if kind:
      terms.append(f"is:{kind}")
    for value in (repository_full_name, repository_url):
      if value:
        for item in value if isinstance(value, list) else [value]:
          terms.append(f"repo:{str(item).removeprefix('https://github.com/')}")
    if org:
      terms.append(f"org:{org}")
    if state and state != "all":
      terms.append(f"state:{state}")
    if repository_id:
      for item in repository_id if isinstance(repository_id, list) else [repository_id]:
        terms.append(f"repo:{item}")
    sorts = {
      "best-match": None,
      "created": "created",
      "updated": "updated",
      "comments": "comments",
      "reactions": "reactions",
      "interactions": "interactions",
    }
    data = await self._request(
      "GET",
      f"{API}/search/issues",
      params={
        "q": " ".join(terms),
        "per_page": min(max(1, int(topn)), 100),
        "sort": sorts.get(sort or "best-match"),
        "order": order,
      },
    )
    items = [
      {**item, "display_url": item.get("html_url"), "display_title": item.get("title")}
      for item in (data.get("items") or [])
    ]
    return self._ok({"issues": items, "total_count": data.get("total_count")})

  async def search_issues(
    self,
    query: str,
    repository_full_name: str | list[str] | None = None,
    repository_id: int | list[int] | None = None,
    repository_url: str | list[str] | None = None,
    topn: int = 20,
    sort: str | None = None,
    order: str | None = None,
    state: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Search issues and pull requests together.

    :param query: the search terms
    :param repository_full_name: one repository or a list to restrict to
    :param repository_id: one numeric id or a list to restrict to
    :param repository_url: one repository url or a list
    :param topn: results wanted
    :param sort: best-match, created, updated, comments, reactions or interactions
    :param order: desc or asc
    :param state: open, closed or all
    """

    async def run(
      query=query,
      repository_full_name=repository_full_name,
      repository_id=repository_id,
      repository_url=repository_url,
      topn=topn,
      sort=sort,
      order=order,
      state=state,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      return await self._search_issues(
        query,
        "",
        topn,
        repository_full_name,
        repository_id,
        repository_url,
        None,
        sort,
        order,
        state,
      )

    return await self._read(
      "read search_issues",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def search_prs(
    self,
    query: str,
    repository_full_name: str | list[str] | None = None,
    repository_id: int | list[int] | None = None,
    repository_url: str | list[str] | None = None,
    org: str | None = None,
    topn: int = 20,
    sort: str | None = None,
    order: str | None = None,
    state: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Search pull requests. The output field keeps its GitHub name, issues.

    :param query: the search terms
    :param repository_full_name: one repository or a list to restrict to
    :param repository_id: one numeric id or a list to restrict to
    :param repository_url: one repository url or a list
    :param org: restrict to one organization
    :param topn: results wanted
    :param sort: best-match, created, updated, comments, reactions or interactions
    :param order: desc or asc
    :param state: open, closed or all
    """

    async def run(
      query=query,
      repository_full_name=repository_full_name,
      repository_id=repository_id,
      repository_url=repository_url,
      org=org,
      topn=topn,
      sort=sort,
      order=order,
      state=state,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      return await self._search_issues(
        query,
        "pr",
        topn,
        repository_full_name,
        repository_id,
        repository_url,
        org,
        sort,
        order,
        state,
      )

    return await self._read(
      "read search_prs",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def search_commits(
    self,
    query: str,
    repository_full_name: str | list[str] | None = None,
    repository_id: int | list[int] | None = None,
    repository_url: str | list[str] | None = None,
    org: str | None = None,
    topn: int = 20,
    sort: str | None = None,
    order: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Search commits.

    :param query: the search terms
    :param repository_full_name: one repository or a list to restrict to
    :param repository_id: one numeric id or a list to restrict to
    :param repository_url: one repository url or a list
    :param org: restrict to one organization
    :param topn: results wanted
    :param sort: best-match, author-date or committer-date
    :param order: desc or asc
    """

    async def run(
      query=query,
      repository_full_name=repository_full_name,
      repository_id=repository_id,
      repository_url=repository_url,
      org=org,
      topn=topn,
      sort=sort,
      order=order,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      terms = [query]
      for value in (repository_full_name, repository_url):
        if value:
          for item in value if isinstance(value, list) else [value]:
            terms.append(f"repo:{str(item).removeprefix('https://github.com/')}")
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
          "per_page": min(max(1, int(topn)), 100),
          "sort": sorts.get(sort or "best-match"),
          "order": order,
        },
      )
      items = [
        {**item, "html_url": item.get("html_url")} for item in (data.get("items") or [])
      ]
      return self._ok({"commits": items, "total_count": data.get("total_count")})

    return await self._read(
      "read search_commits",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  # ═══════════════════════ commit read (draft extra) ════════════════════

  async def fetch_commit(self, repository_full_name: str, commit_sha: str) -> dict:
    """Fetch one commit with its files and stats.

    :param repository_full_name: owner/repo
    :param commit_sha: the commit SHA
    """
    repo = self._repo(repository_full_name)
    data = await self._request("GET", f"{API}{repo}/commits/{self._seg(commit_sha)}")
    return self._ok(data)

  # ═════════════════════════ workspace actions ══════════════════════════

  async def check_runs(
    self,
    repo_full_name: str,
    ref: str,
    check_name: str | None = None,
    check_run_id: int | None = None,
    status: str | None = None,
    filter: str | None = None,
    per_page: int = 30,
    page: int = 1,
  ) -> dict:
    """List the check runs of a ref, or read 1 check run with its annotations.

    :param repo_full_name: owner/repo
    :param ref: the commit SHA, branch or tag
    :param check_name: only the runs of this check name
    :param check_run_id: the check run ID. Set it to read that run and its annotations
    :param status: queued, in_progress or completed
    :param filter: latest keeps the newest run per name, all keeps every run
    :param per_page: runs per page
    :param page: the page number
    """
    repo = self._repo(repo_full_name)
    if check_run_id is not None:
      number = int(check_run_id)
      run = await self._request("GET", f"{API}{repo}/check-runs/{number}")
      annotations = await self._request(
        "GET", f"{API}{repo}/check-runs/{number}/annotations"
      )
      return self._ok({"check_run": run, "annotations": annotations})
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

  async def list_workflows(
    self,
    repo_full_name: str,
    per_page: int = 30,
    page: int = 1,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List the Actions workflows of a repository.

    :param repo_full_name: owner/repo
    :param per_page: workflows per page
    :param page: the page number
    """

    async def run(
      repo_full_name=repo_full_name,
      per_page=per_page,
      page=page,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      repo = self._repo(repo_full_name)
      data = await self._request(
        "GET",
        f"{API}{repo}/actions/workflows",
        params={"per_page": min(max(1, int(per_page)), 100), "page": int(page)},
      )
      return self._ok(
        {
          "workflows": data.get("workflows") or [],
          "total_count": data.get("total_count"),
        }
      )

    return await self._read(
      "read list_workflows",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def fetch_commit_workflow_runs(
    self, repo_full_name: str, commit_sha: str
  ) -> dict:
    """Fetch the pull-request-triggered workflow runs of a commit, first page.

    :param repo_full_name: owner/repo
    :param commit_sha: the commit SHA
    """
    repo = self._repo(repo_full_name)
    data = await self._request(
      "GET",
      f"{API}{repo}/actions/runs",
      params={"head_sha": commit_sha, "event": "pull_request", "per_page": 100},
    )
    return self._ok({"workflow_runs": data.get("workflow_runs") or []})

  async def fetch_workflow_run_jobs(self, repo_full_name: str, run_id: int) -> dict:
    """Fetch the jobs of a workflow run, latest attempt.

    :param repo_full_name: owner/repo
    :param run_id: the workflow run id
    """
    repo = self._repo(repo_full_name)
    data = await self._request(
      "GET",
      f"{API}{repo}/actions/runs/{int(run_id)}/jobs",
      params={"filter": "latest", "per_page": 100},
    )
    return self._ok({"jobs": data.get("jobs") or []})

  async def fetch_workflow_job_steps(self, repo_full_name: str, job_id: int) -> dict:
    """Fetch the steps of a workflow job.

    :param repo_full_name: owner/repo
    :param job_id: the job id
    """
    repo = self._repo(repo_full_name)
    data = await self._request("GET", f"{API}{repo}/actions/jobs/{int(job_id)}")
    return self._ok({"steps": data.get("steps") or [], "job": data})

  async def fetch_workflow_job_logs(self, repo_full_name: str, job_id: int) -> dict:
    """Fetch the raw log text of a workflow job.

    :param repo_full_name: owner/repo
    :param job_id: the job id
    """
    repo = self._repo(repo_full_name)
    text = await self._request(
      "GET",
      f"{API}{repo}/actions/jobs/{int(job_id)}/logs",
      accept="application/vnd.github+json",
      raw=True,
    )
    if len(text) > 400000:
      text = text[-400000:]
      text = "[log truncated to the last 400000 characters]\n" + text
    return self._ok({"content": text})

  async def fetch_workflow_run_artifacts(
    self, repo_full_name: str, run_id: int, name: str | None = None
  ) -> dict:
    """Fetch the artifacts of a workflow run, first page.

    :param repo_full_name: owner/repo
    :param run_id: the workflow run id
    :param name: keep one artifact name
    """
    repo = self._repo(repo_full_name)
    data = await self._request(
      "GET",
      f"{API}{repo}/actions/runs/{int(run_id)}/artifacts",
      params={"per_page": 100},
    )
    artifacts = data.get("artifacts") or []
    if name:
      artifacts = [a for a in artifacts if a.get("name") == name]
    return self._ok({"artifacts": artifacts})

  async def download_workflow_artifact(
    self,
    repo_full_name: str,
    artifact_id: int,
    file_name: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Fetch an artifact's metadata and its download url. The archive needs the token.

    :param repo_full_name: owner/repo
    :param artifact_id: the artifact id
    :param file_name: a name to report for the archive
    """

    async def run(
      repo_full_name=repo_full_name,
      artifact_id=artifact_id,
      file_name=file_name,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      repo = self._repo(repo_full_name)
      data = await self._request(
        "GET", f"{API}{repo}/actions/artifacts/{int(artifact_id)}"
      )
      name = file_name or f"{data.get('name') or 'artifact'}.zip"
      return self._ok(
        {
          "file_uri": {
            "download_url": data.get("archive_download_url"),
            "file_id": str(data.get("id")),
            "mime_type": "application/zip",
            "file_name": name,
          },
          "artifact_id": int(artifact_id),
          "file_name": name,
          "mime_type": "application/zip",
          "size_in_bytes": data.get("size_in_bytes"),
          "expired": data.get("expired"),
          "artifact": data,
        }
      )

    return await self._read(
      "read download_workflow_artifact",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def get_commit_combined_status(
    self,
    repo_full_name: str,
    commit_sha: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Fetch the combined status of a commit.

    :param repo_full_name: owner/repo
    :param commit_sha: the commit SHA
    """

    async def run(
      repo_full_name=repo_full_name,
      commit_sha=commit_sha,
      __user__=__user__,
      __event_call__=__event_call__,
    ):
      repo = self._repo(repo_full_name)
      data = await self._request(
        "GET", f"{API}{repo}/commits/{self._seg(commit_sha)}/status"
      )
      return self._ok(
        {
          "statuses": data.get("statuses") or [],
          "state": data.get("state"),
          "total_count": data.get("total_count"),
        }
      )

    return await self._read(
      "read get_commit_combined_status",
      str(locals().get("name", "") or locals().get("path", "") or ""),
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
    """Rerun the failed jobs of a workflow run. Needs Actions write permission.

    :param repo_full_name: owner/repo
    :param run_id: the workflow run id
    """
    repo = self._repo(repo_full_name)

    async def run():
      await self._request(
        "POST", f"{API}{repo}/actions/runs/{int(run_id)}/rerun-failed-jobs"
      )
      return self._ok({"success": True})

    return await self._write(
      f"rerun the failed jobs of run {run_id} in {repo_full_name}",
      "",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # ═════════════════════════ identity and accounts ══════════════════════

  async def get_profile(
    self,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Fetch the signed-in user profile."""

    async def run(__user__=__user__, __event_call__=__event_call__):
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

    return await self._read(
      "read get_profile",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def list_user_orgs(
    self,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List the organizations of the signed-in user."""

    async def run(__user__=__user__, __event_call__=__event_call__):
      data = await self._request("GET", f"{API}/user/orgs")
      return self._ok(
        {"orgs": [item.get("login") for item in data], "organizations": data}
      )

    return await self._read(
      "read list_user_orgs",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  async def list_installations(
    self,
    manageable_only: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """List the app installations the signed-in user can reach.

    :param manageable_only: keep installs the user can manage, when REST says so
    """

    async def run(
      manageable_only=manageable_only, __user__=__user__, __event_call__=__event_call__
    ):
      data = await self._request("GET", f"{API}/user/installations")
      installations = (data or {}).get("installations") or []
      if manageable_only:
        installations = [
          i
          for i in installations
          if (i.get("permissions") or {}).get("administration") == "write"
          or (i.get("repository_selection") or "") == "all"
        ]
      return self._ok(
        {"installations": installations, "allow_all_repositories_for_testing": None}
      )

    return await self._read(
      "read list_installations",
      str(locals().get("name", "") or locals().get("path", "") or ""),
      run,
      __user__,
      __event_call__,
    )

  # ═════════════════════ releases, tags, packages, minutes ═══════════════

  async def actions_minutes(self, owner: str, owner_type: str = "org") -> dict:
    """Read the Actions minutes used, for an organization or a user.

    :param owner: the organization or the user login
    :param owner_type: org reads /orgs/{owner}/settings/billing/actions, user reads /users/{owner}/settings/billing/actions
    """
    kind = (owner_type or "org").strip().lower()
    if kind not in ("org", "user"):
      raise GitHubError(422, "owner_type must be 'org' or 'user'.", "GET", "")
    base = "orgs" if kind == "org" else "users"
    data = await self._request(
      "GET", f"{API}/{base}/{self._seg(owner)}/settings/billing/actions"
    )
    return self._ok({"minutes": data})

  async def releases(
    self,
    repo_full_name: str,
    release_id: int | None = None,
    tag: str | None = None,
    per_page: int = 30,
    page: int = 1,
  ) -> dict:
    """List the releases of a repository, or read 1 release by id or by tag.

    :param repo_full_name: owner/repo
    :param release_id: the release id. It wins over tag
    :param tag: the tag name of the release
    :param per_page: releases per page
    :param page: the page number
    """
    repo = self._repo(repo_full_name)
    if release_id is not None:
      data = await self._request("GET", f"{API}{repo}/releases/{int(release_id)}")
      return self._ok({"release": data})
    if tag:
      data = await self._request("GET", f"{API}{repo}/releases/tags/{self._seg(tag)}")
      return self._ok({"release": data})
    data = await self._request(
      "GET",
      f"{API}{repo}/releases",
      params={"per_page": min(max(1, int(per_page)), 100), "page": int(page)},
    )
    return self._ok({"releases": data})

  async def tags(self, repo_full_name: str, per_page: int = 30, page: int = 1) -> dict:
    """List the tags of a repository.

    :param repo_full_name: owner/repo
    :param per_page: tags per page
    :param page: the page number
    """
    repo = self._repo(repo_full_name)
    data = await self._request(
      "GET",
      f"{API}{repo}/tags",
      params={"per_page": min(max(1, int(per_page)), 100), "page": int(page)},
    )
    return self._ok({"tags": data})

  async def packages(
    self,
    package_type: str | None = None,
    owner: str | None = None,
    owner_type: str = "org",
    package_name: str | None = None,
    per_page: int = 30,
    page: int = 1,
  ) -> dict:
    """List the packages of the signed-in user, a user or an organization, or read 1 package.

    :param package_type: npm, maven, rubygems, docker, nuget, container or generic. Required with owner.
    :param owner: the user or organization login. Absent reads the packages of the signed-in user
    :param owner_type: org reads /orgs/{owner}/packages, user reads /users/{owner}/packages
    :param package_name: read 1 package with its versions
    :param per_page: versions per page, on the read of 1 package
    :param page: the page number, on the read of 1 package
    """
    kind = (owner_type or "org").strip().lower()
    if kind not in ("org", "user"):
      raise GitHubError(422, "owner_type must be 'org' or 'user'.", "GET", "")
    if owner and not package_type:
      raise GitHubError(422, "Give package_type with owner.", "GET", "")
    if package_name and not package_type:
      raise GitHubError(422, "Give package_type with package_name.", "GET", "")
    base = f"/{kind}s/{self._seg(owner)}" if owner else "/user"
    if package_name:
      path = f"{API}{base}/packages/{self._seg(package_type)}/{self._seg(package_name)}"
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

  # ═════════════════════════════════ gists ═══════════════════════════════

  async def gists(
    self,
    username: str | None = None,
    per_page: int = 30,
    page: int = 1,
    since: str | None = None,
  ) -> dict:
    """List the gists of a user, or the gists of the signed-in user.

    :param username: the user login. Absent reads the gists of the signed-in user
    :param per_page: gists per page
    :param page: the page number
    :param since: an ISO 8601 timestamp. Only the gists updated after it
    """
    url = f"{API}/users/{self._seg(username)}/gists" if username else f"{API}/gists"
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

  async def fetch_gist(self, gist_id: str) -> dict:
    """Fetch one gist with its files.

    :param gist_id: the gist id
    """
    data = await self._request("GET", f"{API}/gists/{self._seg(gist_id)}")
    return self._ok({"gist": data})

  async def create_gist(
    self,
    files: dict,
    description: str | None = None,
    public: bool = False,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Create a gist. The gate asks first.

    :param files: the files, each {name: {"content": "..."}}
    :param description: the gist description
    :param public: true makes the gist public
    """
    if not files:
      raise GitHubError(422, "Give at least 1 file.", "POST", "")

    async def run():
      data = await self._request(
        "POST",
        f"{API}/gists",
        payload={"files": files, "description": description, "public": bool(public)},
      )
      return self._ok({"gist": data})

    return await self._write(
      f"create a {'public' if public else 'secret'} gist with {len(files)} file(s)",
      ", ".join(list(files)[:10]),
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def update_gist(
    self,
    gist_id: str,
    files: dict | None = None,
    description: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Edit a gist. The gate asks first.

    :param gist_id: the gist id
    :param files: the files to add or replace, each {name: {"content": "..."}}
    :param description: the new description
    """

    async def run():
      data = await self._request(
        "PATCH",
        f"{API}/gists/{self._seg(gist_id)}",
        payload={"files": files, "description": description},
      )
      return self._ok({"gist": data})

    return await self._write(
      f"edit gist {gist_id}",
      ", ".join(list(files or {})[:10]),
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def delete_gist(
    self,
    gist_id: str,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Delete a gist. The gate asks first.

    :param gist_id: the gist id
    """

    async def run():
      await self._request("DELETE", f"{API}/gists/{self._seg(gist_id)}")
      return self._ok({"deleted": True, "gist_id": gist_id})

    return await self._write(
      f"delete gist {gist_id}",
      gist_id,
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  # ═══════════════════════════ security alert writes ═════════════════════

  async def update_code_scanning_alert(
    self,
    repo_full_name: str,
    alert_number: int,
    state: str = "dismissed",
    reason: str | None = None,
    comment: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Dismiss or reopen a code scanning alert. The gate asks first.

    :param repo_full_name: owner/repo
    :param alert_number: the alert number
    :param state: dismissed closes the alert, open reopens it
    :param reason: false positive, won't fix or used in tests. Required when dismissed.
    :param comment: the dismissal comment
    """
    repo = self._repo(repo_full_name)

    async def run():
      data = await self._request(
        "PATCH",
        f"{API}{repo}/code-scanning/alerts/{int(alert_number)}",
        payload={
          "state": state,
          "dismissed_reason": reason,
          "dismissed_comment": comment,
        },
      )
      return self._ok({"alert": data})

    return await self._write(
      f"set code scanning alert {int(alert_number)} to {state} in {repo_full_name}",
      f"reason: {reason or 'none'}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def update_secret_scanning_alert(
    self,
    repo_full_name: str,
    alert_number: int,
    state: str = "resolved",
    resolution: str | None = None,
    comment: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Resolve or reopen a secret scanning alert. The gate asks first.

    :param repo_full_name: owner/repo
    :param alert_number: the alert number
    :param state: resolved closes the alert, open reopens it
    :param resolution: false_positive, wont_fix, revoked or used_in_tests. Required when resolved.
    :param comment: the resolution comment
    """
    repo = self._repo(repo_full_name)

    async def run():
      data = await self._request(
        "PATCH",
        f"{API}{repo}/secret-scanning/alerts/{int(alert_number)}",
        payload={
          "state": state,
          "resolution": resolution,
          "resolution_comment": comment,
        },
      )
      return self._ok({"alert": data})

    return await self._write(
      f"set secret scanning alert {int(alert_number)} to {state} in {repo_full_name}",
      f"resolution: {resolution or 'none'}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )

  async def update_dependabot_alert(
    self,
    repo_full_name: str,
    alert_number: int,
    state: str = "dismissed",
    reason: str | None = None,
    comment: str | None = None,
    __user__: dict | None = None,
    __event_call__: Callable | None = None,
  ) -> dict:
    """Dismiss or reopen a Dependabot alert. The gate asks first.

    :param repo_full_name: owner/repo
    :param alert_number: the alert number
    :param state: dismissed closes the alert, open reopens it
    :param reason: fix_started, inaccurate, no_bandwidth, not_used or tolerable_risk. Required when dismissed.
    :param comment: the dismissal comment, at most 280 characters
    """
    repo = self._repo(repo_full_name)

    async def run():
      data = await self._request(
        "PATCH",
        f"{API}{repo}/dependabot/alerts/{int(alert_number)}",
        payload={
          "state": state,
          "dismissed_reason": reason,
          "dismissed_comment": comment,
        },
      )
      return self._ok({"alert": data})

    return await self._write(
      f"set dependabot alert {int(alert_number)} to {state} in {repo_full_name}",
      f"reason: {reason or 'none'}",
      run,
      __user__=__user__,
      __event_call__=__event_call__,
    )
