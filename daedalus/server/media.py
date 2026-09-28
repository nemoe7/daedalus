"""The OpenAI endpoints for models that do not chat, for one model or a media pool."""

import base64
import hashlib
import json
import re
import struct
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from daedalus import providers, store
from daedalus.config import get_config
from daedalus.routing import cooldowns, pacing, penalties, retries, router
from daedalus.server import access, stream, upstream
from daedalus.store import keys

routes = APIRouter()
TRANSCRIPT_FORMATS = ("json", "text", "srt", "verbose_json", "vtt")
SPEECH_FORMATS = ("mp3", "opus", "aac", "flac", "wav", "pcm")
SIZE = re.compile(r"auto|[1-9][0-9]*x[1-9][0-9]*")
# The server sets the shared weights, cooldowns and pacing at start.
PENALTIES: penalties.Penalties | None = None
COOLDOWNS: cooldowns.Cooldowns | None = None
PACING: pacing.Pacing | None = None
REPEATS = retries.Retries()
TRANSCRIPTION_POOL, IMAGE_POOL = router.MEDIA_POOLS
Call = Callable[[providers.OpenAIProvider, str, str], Awaitable[Any]]


def invalid(message: str) -> JSONResponse:
  return upstream.error_response(400, message, "invalid_request_error")


async def json_body(request: Request) -> dict | Response:
  """The JSON body with its model, or the error answer."""
  try:
    body = await request.json()
  except ValueError:
    return invalid("Invalid JSON")
  if not isinstance(body, dict) or not isinstance(body.get("model"), str):
    return invalid("A model is required")
  request.state.model = body["model"]
  return body


def models_for(
  request: Request, model: str, pool: str | None, content: bytes
) -> list[str] | Response:
  """The models to try: the one model, or the pool models by weight after the models that answered this content."""
  if model != pool:
    if (
      model == router.RESERVED_MODEL
      or model in router.POOLS
      or model in router.MEDIA_POOLS
    ):
      return invalid(
        f"This endpoint needs a provider/slug model{f' or {pool}' if pool else ''}"
      )
    return [model]
  config = get_config()
  members = [
    name
    for name in store.mode_models(router.MEDIA_POOLS[pool])
    if router.pooled(config, name)
  ]
  if not members:
    return invalid(f"{pool} has no models")
  turn = REPEATS.start_digest(
    keys.digest(access.bearer(request) + pool), hashlib.sha256(content).hexdigest()
  )
  request.state.turn = turn
  if turn.count:
    request.state.retry = str(turn.count)
    members = retries.fresh_models(turn, members)
  return PENALTIES.order([members]) if PENALTIES else members


def failed(status: int) -> JSONResponse:
  """The error answer after the last attempt failed."""
  if status:
    return upstream.error_response(
      status, "Upstream provider rejected the request", "upstream_error"
    )
  return upstream.error_response(
    502, "Upstream provider attempt failed", "upstream_error"
  )


async def attempt(
  request: Request, model: str, pool: str | None, content: bytes, call: Call
) -> Any:
  """Try the models in turn, record each attempt for the dashboard, and update the pool weights."""
  models = models_for(request, model, pool, content)
  if isinstance(models, Response):
    return models
  if COOLDOWNS:
    ends = COOLDOWNS.ends()
    cooling = {m: COOLDOWNS.until(m, ends) for m in models}
    if models and all(cooling.values()):
      return upstream.cooling_response(min(cooling.values()) - time.time())
    models = [m for m in models if not cooling[m]]
  if PACING:
    paces = store.pace_limits()
    left = [m for m in models if not PACING.full(m, paces)]
    if models and not left:
      return upstream.cooling_response(PACING.wait(models))
    models = left
  pooled = model == pool
  attempts: list[dict[str, Any]] = []
  request.state.attempts, status = attempts, 0
  for index, candidate in enumerate(models):
    request.state.fallbacks = str(index)
    started = time.perf_counter()
    try:
      provider, slug = providers.provider_for(candidate, get_config())
      pending = call(provider, slug, candidate)
    except providers.ProviderError as exc:
      if not pooled:
        return invalid(str(exc))
      # A pool model that cannot take this request is not a fault.
      attempts.append(upstream.note(candidate, "skipped", None, str(exc)))
      continue
    if PACING:
      PACING.record(candidate)
    try:
      answer = await pending
    except (upstream.UpstreamStatus, *stream.ATTEMPT_ERRORS) as exc:
      attempts.append(upstream.failure_note(candidate, started, exc))
      status = exc.status if isinstance(exc, upstream.UpstreamStatus) else 0
      limited = isinstance(exc, upstream.RateLimitError)
      if pooled and PENALTIES:
        PENALTIES.record(
          candidate, PENALTIES.rate_limit if limited else PENALTIES.fault
        )
      if limited and COOLDOWNS:
        attempts[-1]["cooldown"] = COOLDOWNS.start(candidate, exc.headers, exc.body)
      continue
    request.state.via = candidate
    attempts.append(upstream.note(candidate, "answered", started))
    if COOLDOWNS:
      COOLDOWNS.succeeded(candidate)
    if pooled:
      if PENALTIES:
        PENALTIES.record(candidate, PENALTIES.success)
      retries.record(request.state.turn, None, candidate)
    return answer
  if not attempts:
    return invalid(f"No model of {pool} can take this request")
  return failed(status)


def packed(vector: list[float]) -> str:
  """The base64 text of a vector as little-endian float32 values."""
  return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()


def text_input(value: Any) -> bool:
  """Accept a string, a list of strings, a token list, or a list of token lists."""
  if isinstance(value, str):
    return bool(value)
  if not isinstance(value, list) or not value:
    return False
  if all(isinstance(item, int) for item in value):
    return True
  return all(
    isinstance(item, str)
    or (isinstance(item, list) and all(isinstance(t, int) for t in item))
    for item in value
  )


@routes.post("/v1/embeddings")
async def embeddings(request: Request) -> Response:
  denied = access.check_api_key(request)
  if denied is not None:
    return denied
  body = await json_body(request)
  if isinstance(body, Response):
    return body
  if not text_input(body.get("input")):
    return invalid("input must be a string or a list of strings or tokens")
  encoding = body.get("encoding_format", "float")
  if encoding not in ("float", "base64"):
    return invalid("encoding_format must be float or base64")

  def call(
    provider: providers.OpenAIProvider, slug: str, model: str
  ) -> Awaitable[dict]:
    url, payload, headers = provider.embed_request(slug, body)

    async def send() -> dict:
      response = await upstream.post(model, url, headers, json=payload)
      return provider.embeddings(response.json(), model)

    return send()

  result = await attempt(request, body["model"], None, b"", call)
  if isinstance(result, Response):
    return result
  if encoding == "base64":
    for item in result["data"]:
      item["embedding"] = packed(item["embedding"])
  return JSONResponse(result)


@routes.post("/v1/audio/transcriptions")
async def transcriptions(request: Request) -> Response:
  denied = access.check_api_key(request)
  if denied is not None:
    return denied
  try:
    form = await request.form()
  except (AssertionError, ValueError):
    return invalid("A multipart form is required")
  model, upload = form.get("model"), form.get("file")
  if not isinstance(model, str) or not model:
    return invalid("A model is required")
  request.state.model = model
  if not isinstance(upload, UploadFile):
    return invalid("A file is required")
  fields: dict[str, Any] = {
    key: form.getlist(key) if key.endswith("[]") else form[key]
    for key in form
    if key not in ("model", "file") and isinstance(form[key], str)
  }
  if fields.get("response_format", "json") not in TRANSCRIPT_FORMATS:
    return invalid("response_format must be json, text, srt, verbose_json or vtt")
  media = upload.content_type or "application/octet-stream"
  audio = (upload.filename or "audio", await upload.read(), media)

  def call(
    provider: providers.OpenAIProvider, slug: str, candidate: str
  ) -> Awaitable[tuple[bytes, str]]:
    url, content, headers = provider.transcribe_request(slug, fields, audio)

    async def send() -> tuple[bytes, str]:
      response = await upstream.post(candidate, url, headers, **content)
      return provider.transcription(response, fields)

    return send()

  seen = audio[1] + json.dumps(fields, sort_keys=True).encode()
  result = await attempt(request, model, TRANSCRIPTION_POOL, seen, call)
  if isinstance(result, Response):
    return result
  body, media_type = result
  return Response(body, media_type=media_type)


@routes.post("/v1/audio/speech")
async def speech(request: Request) -> Response:
  denied = access.check_api_key(request)
  if denied is not None:
    return denied
  body = await json_body(request)
  if isinstance(body, Response):
    return body
  if not isinstance(body.get("input"), str) or not body["input"]:
    return invalid("input must be a string")
  if body.get("response_format", "mp3") not in SPEECH_FORMATS:
    return invalid("response_format must be mp3, opus, aac, flac, wav or pcm")

  def call(
    provider: providers.OpenAIProvider, slug: str, model: str
  ) -> Awaitable[tuple[bytes, str]]:
    url, content, headers = provider.speech_request(slug, body)

    async def send() -> tuple[bytes, str]:
      response = await upstream.post(model, url, headers, **content)
      return provider.speech(response, body)

    return send()

  result = await attempt(request, body["model"], None, b"", call)
  if isinstance(result, Response):
    return result
  audio, media_type = result
  return Response(audio, media_type=media_type)


def image_error(body: dict) -> str | None:
  """The problem with an image request, or None."""
  if not isinstance(body.get("prompt"), str) or not body["prompt"]:
    return "prompt must be a string"
  n = body.get("n", 1)
  if n is not None and (type(n) is not int or n < 1):
    return "n must be a positive integer"
  size = body.get("size")
  if size is not None and not (isinstance(size, str) and SIZE.fullmatch(size)):
    return "size must be auto or WIDTHxHEIGHT"
  if body.get("response_format", "url") not in ("url", "b64_json", None):
    return "response_format must be url or b64_json"
  return None


@routes.post("/v1/images/generations")
async def images(request: Request) -> Response:
  denied = access.check_api_key(request)
  if denied is not None:
    return denied
  body = await json_body(request)
  if isinstance(body, Response):
    return body
  problem = image_error(body)
  if problem:
    return invalid(problem)

  def call(
    provider: providers.OpenAIProvider, slug: str, model: str
  ) -> Awaitable[dict]:
    url, content, headers = provider.image_request(slug, body)

    async def send() -> dict:
      response = await upstream.post(model, url, headers, **content)
      return provider.images(response, body)

    return send()

  seen = json.dumps({k: v for k, v in body.items() if k != "model"}, sort_keys=True)
  result = await attempt(request, body["model"], IMAGE_POOL, seen.encode(), call)
  return result if isinstance(result, Response) else JSONResponse(result)
