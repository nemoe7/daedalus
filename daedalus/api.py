"""OpenAI-compatible local proxy with the upstream key set below, never committed."""

import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from secrets import compare_digest

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import catalog, router
from daedalus.config import get_config

# Hardcoded for now. Edit here, not through environment variables.
UPSTREAM_BASE_URL = "https://api.openai.com"
API_KEY = ""

# When set, clients must send "Authorization: Bearer <LOCAL_API_KEY>".
LOCAL_API_KEY = ""

HOST = "0.0.0.0"
PORT = 8080
TIMEOUT_SECONDS = 600.0

# Headers that must not cross a proxy hop.
HOP_BY_HOP = frozenset(
  {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
  }
)

logger = logging.getLogger("daedalus")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
  """Set up logging, then close the upstream client on shutdown."""
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
  yield
  if _client is not None:
    await _client.aclose()
    set_client(None)


app = FastAPI(title="daedalus", version="0.1.0", lifespan=lifespan)

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
  global _client
  if _client is None:
    _client = httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS))
  return _client


def set_client(client: httpx.AsyncClient | None) -> None:
  """Replace the upstream client. Test seam."""
  global _client
  _client = client


def build_upstream_url(path_and_query: str) -> str:
  """Join the upstream origin with the incoming path and query."""
  return UPSTREAM_BASE_URL.rstrip("/") + path_and_query


def build_upstream_headers(request_headers: Mapping[str, str]) -> dict[str, str]:
  """Pass client headers through, without hop-by-hop headers and the client key."""
  headers: dict[str, str] = {}
  for name, value in request_headers.items():
    if name.lower() in HOP_BY_HOP or name.lower() in {"host", "authorization"}:
      continue
    headers[name] = value
  if API_KEY:
    headers["Authorization"] = f"Bearer {API_KEY}"
  return headers


def error_response(status: int, message: str, error_type: str) -> JSONResponse:
  """Answer in the OpenAI error shape."""
  return JSONResponse(
    status_code=status,
    content={"error": {"message": message, "type": error_type, "code": status}},
  )


def check_local_key(request: Request) -> JSONResponse | None:
  """Reject the request when a local key is set and not matched."""
  if not LOCAL_API_KEY:
    return None
  header = request.headers.get("authorization", "")
  token = header[7:].strip() if header.lower().startswith("bearer ") else ""
  if compare_digest(token, LOCAL_API_KEY):
    return None
  return error_response(
    401,
    "Invalid local API key. Send 'Authorization: Bearer <LOCAL_API_KEY>'.",
    "authentication_error",
  )


@app.middleware("http")
async def log_request(request: Request, call_next):
  started = time.perf_counter()
  response = await call_next(request)
  elapsed_ms = (time.perf_counter() - started) * 1000
  logger.info(
    "%s %s %s %.1fms",
    request.method,
    request.url.path,
    response.status_code,
    elapsed_ms,
  )
  return response


def last_user_text(messages: object) -> str:
  """The text of the last user turn, whatever shape its content takes."""
  if not isinstance(messages, list):
    return ""
  for message in reversed(messages):
    if not isinstance(message, dict) or message.get("role") != "user":
      continue
    content = message.get("content")
    if isinstance(content, str):
      return content
    if isinstance(content, list):
      parts = [part.get("text", "") for part in content if isinstance(part, dict)]
      return "\n".join(part for part in parts if part)
  return ""


def routed_body(body: bytes) -> bytes:
  """Swap a reserved model name for a routed provider model, and pass any other body on."""
  if "daedalus/" not in body.decode("utf-8", "ignore"):
    return body
  try:
    payload = json.loads(body)
  except ValueError:
    return body
  if not isinstance(payload, dict):
    return body
  name = payload.get("model")
  if not isinstance(name, str) or not name.startswith("daedalus/"):
    return body
  if name in router.POOLS:
    target = router.route_pool(name, get_config(), catalog.read_models_txt())
  elif name == router.RESERVED_MODEL:
    prompt = last_user_text(payload.get("messages"))
    target = router.route(prompt, get_config(), catalog.read_models_txt())
  else:
    return body
  if target is None:
    logger.warning("no model in any tier for %s", name)
    return body
  payload["model"] = target
  logger.info("routed %s to %s", name, target)
  return json.dumps(payload).encode("utf-8")


@app.get("/health")
async def health() -> dict[str, str]:
  return {"status": "ok", "upstream": UPSTREAM_BASE_URL}


@app.api_route("/v1/{rest_of_path:path}", methods=["GET", "POST"])
async def proxy(request: Request, rest_of_path: str) -> Response:
  denied = check_local_key(request)
  if denied is not None:
    return denied

  query = f"?{request.url.query}" if request.url.query else ""
  url = build_upstream_url(request.url.path + query)
  body = routed_body(await request.body())
  client = get_client()
  upstream_request = client.build_request(
    request.method,
    url,
    content=body,
    headers=build_upstream_headers(request.headers),
  )

  try:
    upstream_response = await client.send(upstream_request, stream=True)
  except httpx.HTTPError as exc:
    return error_response(502, f"Upstream request failed: {exc}", "upstream_error")

  if upstream_response.status_code >= 400:
    payload = await upstream_response.aread()
    await upstream_response.aclose()
    return Response(
      content=payload,
      status_code=upstream_response.status_code,
      media_type=upstream_response.headers.get("content-type", "application/json"),
    )

  async def stream() -> AsyncIterator[bytes]:
    try:
      async for chunk in upstream_response.aiter_raw():
        if chunk:
          yield chunk
    finally:
      await upstream_response.aclose()

  return StreamingResponse(
    stream(),
    status_code=upstream_response.status_code,
    media_type=upstream_response.headers.get("content-type", "application/json"),
  )


def run() -> None:
  """Entry point for `daedalus` and `uvicorn daedalus.api:app`."""
  import uvicorn

  uvicorn.run(app, host=HOST, port=PORT)
