"""The key check for `/v1`: the master key or a named API key."""

import hmac
from collections.abc import Mapping

from fastapi import Request
from fastapi.responses import JSONResponse

from daedalus import dashboard, store
from daedalus.server.upstream import error_response, forward
from daedalus.store import keys

# The short name of each client app: a header name prefix, a part of its value, and the name.
# The first match wins.
APPS = (
  ("x-title", "kilo", "Kilo"),
  ("http-referer", "kilo", "Kilo"),
  ("user-agent", "kilo-code", "Kilo"),
  ("x-openwebui-", "", "OWUI"),
)
# Characters of a title header that the Requests page shows for an app not in APPS.
TITLE_LIMIT = 24


def app_name(headers: Mapping[str, str]) -> str | None:
  """The short name of the client app from its headers, else its title header, else None."""
  lowered = [(name.lower(), value.lower()) for name, value in headers.items()]
  for prefix, part, app in APPS:
    if any(name.startswith(prefix) and part in value for name, value in lowered):
      return app
  title = headers.get("x-openrouter-title") or headers.get("x-title")
  return title[:TITLE_LIMIT] if title else None


def bearer(request: Request) -> str:
  return keys.bearer(request.headers.get("authorization", ""))


def check_api_key(request: Request) -> JSONResponse | None:
  """Reject the request unless the bearer token is the master key or an API key. Keep the client headers
  of an accepted request.
  """
  token, master = bearer(request), dashboard.master()
  if master is not None and hmac.compare_digest(token.encode(), master.encode()):
    name = "master"
  else:
    name = keys.find(store.MODELS_DB, token)
  if name is None:
    return error_response(
      401,
      "Send the master key or an API key as 'Authorization: Bearer <key>'.",
      "authentication_error",
    )
  request.state.key = name
  request.state.app = app_name(request.headers)
  if request.state.app:
    dashboard.live_update(request, app=request.state.app)
  forward(request.headers)
  return None
