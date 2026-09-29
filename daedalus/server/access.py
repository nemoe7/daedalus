"""The key check for `/v1`: the master key or a named API key."""

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse

from daedalus import dashboard, store
from daedalus.server.upstream import error_response, forward
from daedalus.store import keys


def bearer(request: Request) -> str:
  return keys.bearer(request.headers.get("authorization", ""))


def check_api_key(request: Request) -> JSONResponse | None:
  """Reject the request unless the bearer token is the master key or an API key. Keep the client headers of an accepted request."""
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
  forward(request.headers)
  return None
