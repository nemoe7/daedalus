"""The OpenAI endpoints for models that do not chat, each for one model with no fallback."""

import base64
import re
import struct
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from daedalus import providers
from daedalus.config import get_config
from daedalus.routing import router
from daedalus.server import access, stream, upstream

routes = APIRouter()
TRANSCRIPT_FORMATS = ("json", "text", "srt", "verbose_json", "vtt")
SPEECH_FORMATS = ("mp3", "opus", "aac", "flac", "wav", "pcm")
SIZE = re.compile(r"auto|[1-9][0-9]*x[1-9][0-9]*")


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


def provider_for(model: str) -> tuple[providers.OpenAIProvider, str] | Response:
  """The provider and the slug of a `provider/slug` model, or the error answer."""
  if model == router.RESERVED_MODEL or model in router.POOLS:
    return invalid("This endpoint needs a provider/slug model")
  try:
    return providers.provider_for(model, get_config())
  except providers.ProviderError as exc:
    return invalid(str(exc))


async def attempt(
  request: Request, model: str, call: Callable[[], Awaitable[Any]]
) -> Any:
  """Run the one upstream call, and record it for the dashboard."""
  attempts: list[dict[str, Any]] = []
  request.state.attempts, request.state.fallbacks = attempts, "0"
  started = time.perf_counter()
  try:
    answer = await call()
  except upstream.UpstreamStatus as exc:
    attempts.append(upstream.failure_note(model, started, exc))
    return upstream.error_response(
      exc.status, "Upstream provider rejected the request", "upstream_error"
    )
  except stream.ATTEMPT_ERRORS as exc:
    attempts.append(upstream.failure_note(model, started, exc))
    return upstream.error_response(
      502, "Upstream provider attempt failed", "upstream_error"
    )
  request.state.via = model
  attempts.append(upstream.note(model, "answered", started))
  return answer


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
  model = body["model"]
  found = provider_for(model)
  if isinstance(found, Response):
    return found
  provider, slug = found
  try:
    url, payload, headers = provider.embed_request(slug, body)
  except providers.ProviderError as exc:
    return invalid(str(exc))

  async def call() -> dict:
    response = await upstream.post(model, url, headers, json=payload)
    return provider.embeddings(response.json(), model)

  result = await attempt(request, model, call)
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
  found = provider_for(model)
  if isinstance(found, Response):
    return found
  provider, slug = found
  media = upload.content_type or "application/octet-stream"
  audio = (upload.filename or "audio", await upload.read(), media)
  try:
    url, content, headers = provider.transcribe_request(slug, fields, audio)
  except providers.ProviderError as exc:
    return invalid(str(exc))

  async def call() -> tuple[bytes, str]:
    response = await upstream.post(model, url, headers, **content)
    return provider.transcription(response, fields)

  result = await attempt(request, model, call)
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
  model = body["model"]
  found = provider_for(model)
  if isinstance(found, Response):
    return found
  provider, slug = found
  try:
    url, content, headers = provider.speech_request(slug, body)
  except providers.ProviderError as exc:
    return invalid(str(exc))

  async def call() -> tuple[bytes, str]:
    response = await upstream.post(model, url, headers, **content)
    return provider.speech(response, body)

  result = await attempt(request, model, call)
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
  model = body["model"]
  found = provider_for(model)
  if isinstance(found, Response):
    return found
  provider, slug = found
  try:
    url, content, headers = provider.image_request(slug, body)
  except providers.ProviderError as exc:
    return invalid(str(exc))

  async def call() -> dict:
    response = await upstream.post(model, url, headers, **content)
    return provider.images(response, body)

  result = await attempt(request, model, call)
  return result if isinstance(result, Response) else JSONResponse(result)
