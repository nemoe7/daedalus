"""OpenAI-compatible local proxy with the upstream key set below, never committed."""

import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from secrets import compare_digest
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import catalog, gemini, router
from daedalus.config import get_config

# Hardcoded for now. Edit here, not through environment variables.
UPSTREAM_BASE_URL = "https://api.openai.com"
API_KEY = ""

# When set, clients must send "Authorization: Bearer <LOCAL_API_KEY>".
LOCAL_API_KEY = ""

HOST = "0.0.0.0"
PORT = 8080
TIMEOUT_SECONDS = 600.0
# ADR 2: the wait for one answer. A provider that sends bytes resets it, so a slow
# stream is not cut off.
WAIT_SECONDS = 60.0

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
    _client = httpx.AsyncClient(
      timeout=httpx.Timeout(TIMEOUT_SECONDS, read=WAIT_SECONDS)
    )
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


def routed_models(body: bytes) -> tuple[dict[str, Any] | None, list[str]]:
  """Split a request into its JSON body and the models to try, in order."""
  empty: tuple[dict[str, Any] | None, list[str]] = (None, [])
  if "daedalus/" not in body.decode("utf-8", "ignore"):
    return empty
  try:
    payload = json.loads(body)
  except ValueError:
    return empty
  if not isinstance(payload, dict):
    return empty
  name = payload.get("model")
  if not isinstance(name, str) or not name.startswith("daedalus/"):
    return empty
  if name in router.POOLS:
    models = router.route_pool(name, get_config(), catalog.read_models_txt())
  elif name == router.RESERVED_MODEL:
    prompt = last_user_text(payload.get("messages"))
    models = router.route(prompt, get_config(), catalog.read_models_txt())
  else:
    return empty
  if not models:
    logger.warning("no model in any tier for %s", name)
  logger.info("routed %s to %d models", name, len(models))
  return payload, models


def body_for(payload: dict[str, Any], model: str) -> bytes:
  """Encode one attempt of a routed request."""
  return json.dumps({**payload, "model": model}).encode("utf-8")


@app.get("/health")
async def health() -> dict[str, str]:
  return {"status": "ok", "upstream": UPSTREAM_BASE_URL}


@app.api_route("/v1/{rest_of_path:path}", methods=["GET", "POST"])
async def proxy(request: Request, rest_of_path: str) -> Response:
  denied = check_local_key(request)
  if denied is not None:
    return denied

  query = f"?{request.url.query}" if request.url.query else ""
  await_body = await request.body()
  url = build_upstream_url(request.url.path + query)
  payload, models = routed_models(await_body)
  attempts = [body_for(payload, model) for model in models] if payload else [await_body]
  client = get_client()
  headers = build_upstream_headers(request.headers)
  failure: Response | None = None

  for content in attempts:
    upstream_request = client.build_request(
      request.method, url, content=content, headers=headers
    )
    try:
      upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
      logger.warning("upstream request failed: %s", exc)
      failure = error_response(502, f"Upstream request failed: {exc}", "upstream_error")
      continue

    if upstream_response.status_code >= 400:
      detail = await upstream_response.aread()
      await upstream_response.aclose()
      logger.warning(
        "upstream answered %s, so the next model tries", upstream_response.status_code
      )
      failure = Response(
        content=detail,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
      )
      continue

    return StreamingResponse(
      stream_from(upstream_response),
      status_code=upstream_response.status_code,
      media_type=upstream_response.headers.get("content-type", "application/json"),
    )

  if failure is not None:
    return failure
  return error_response(502, "No model answered the request", "upstream_error")


def stream_from(upstream_response: httpx.Response) -> AsyncIterator[bytes]:
  """Pass one upstream answer through, unbuffered."""

  async def stream() -> AsyncIterator[bytes]:
    try:
      async for chunk in upstream_response.aiter_raw():
        if chunk:
          yield chunk
    finally:
      await upstream_response.aclose()

  return stream()


async def gemini_frames(
  upstream_response: httpx.Response, model: str
) -> AsyncIterator[bytes]:
  """Translate an OpenAI SSE answer into Gemini SSE frames."""
  buffer = b""
  try:
    async for piece in upstream_response.aiter_bytes():
      buffer += piece
      while b"\n\n" in buffer:
        frame, buffer = buffer.split(b"\n\n", 1)
        for line in frame.split(b"\n"):
          if not line.startswith(b"data:"):
            continue
          payload = line[5:].strip()
          if payload == b"[DONE]":
            yield b"data: [DONE]\n\n"
            continue
          try:
            chunk = json.loads(payload)
          except ValueError:
            continue
          out = json.dumps(gemini.chunk_to_gemini(chunk, model)).encode("utf-8")
          yield b"data: " + out + b"\n\n"
  finally:
    await upstream_response.aclose()


@app.post("/v1beta/models/{target:path}")
async def gemini_proxy(request: Request, target: str) -> Response:
  """Take a Gemini generateContent request and answer in the Gemini shape."""
  denied = check_local_key(request)
  if denied is not None:
    return denied

  model, _, action = target.rpartition(":")
  if not model or action not in {"generateContent", "streamGenerateContent"}:
    return error_response(
      404, f"Unknown Gemini action: {target}", "invalid_request_error"
    )
  try:
    body = json.loads(await request.body())
  except ValueError:
    return error_response(
      400, "Request body is not valid JSON", "invalid_request_error"
    )
  if not isinstance(body, dict):
    return error_response(
      400, "Request body is not a JSON object", "invalid_request_error"
    )

  streaming = action == "streamGenerateContent"
  payload = gemini.to_openai(model, body)
  if streaming:
    payload["stream"] = True
  logger.info("gemini %s to %s, stream=%s", action, model, streaming)

  client = get_client()
  upstream_request = client.build_request(
    "POST",
    build_upstream_url("/v1/chat/completions"),
    content=json.dumps(payload).encode("utf-8"),
    headers=build_upstream_headers(request.headers),
  )
  try:
    upstream_response = await client.send(upstream_request, stream=True)
  except httpx.HTTPError as exc:
    logger.warning("upstream request failed: %s", exc)
    return error_response(502, f"Upstream request failed: {exc}", "upstream_error")

  if upstream_response.status_code >= 400:
    detail = await upstream_response.aread()
    await upstream_response.aclose()
    return Response(
      content=detail,
      status_code=upstream_response.status_code,
      media_type=upstream_response.headers.get("content-type", "application/json"),
    )

  if streaming:
    return StreamingResponse(
      gemini_frames(upstream_response, model), media_type="text/event-stream"
    )

  raw = await upstream_response.aread()
  await upstream_response.aclose()
  try:
    answer = json.loads(raw)
  except ValueError:
    return Response(content=raw, status_code=200, media_type="application/json")
  return JSONResponse(gemini.to_gemini(answer, model))


def run() -> None:
  """Entry point for `daedalus` and `uvicorn daedalus.api:app`."""
  import uvicorn

  uvicorn.run(app, host=HOST, port=PORT)
