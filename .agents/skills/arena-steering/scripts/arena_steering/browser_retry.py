"""Signed lifetime bounds for browser write retries."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

TOKEN_LIFETIME_SECONDS = 24 * 60 * 60
RECEIPT_LIMIT = 10_000
RETAINED_BYTES_LIMIT = 32 * 1024 * 1024
CLEANUP_BATCH_SIZE = 100
_TOKEN = re.compile(
  r"v1_([0-9a-f]{16})_([0-9a-f]{1,12})_([0-9a-f]{1,12})_([0-9a-f]{16})_([0-9a-f]{64})"
)


class RetryCapacityError(RuntimeError):
  """A retryable admission failure with no committed operation."""


@dataclass(frozen=True)
class RetryToken:
  generation: str
  issued_at: int
  expires_at: int


def issue_token(secret: bytes, generation: str, now: int) -> str:
  """Issue a signed token without admitting or executing an operation."""
  if not re.fullmatch(r"[0-9a-f]{16}", generation) or not 0 <= now < 2**48:
    raise ValueError("Invalid browser retry signing state")
  expires_at = now + TOKEN_LIFETIME_SECONDS
  if expires_at >= 2**48:
    raise ValueError("Browser retry token expiry is out of range")
  payload = f"v1_{generation}_{now:x}_{expires_at:x}_{secrets.token_hex(8)}"
  signature = hmac.digest(secret, payload.encode("ascii"), hashlib.sha256).hex()
  return f"{payload}_{signature}"


def verify_token(token: str, secret: bytes, generation: str, now: int) -> RetryToken:
  """Reject unsigned, changed, expired, future, and wrong-generation retries."""
  match = _TOKEN.fullmatch(token) if isinstance(token, str) else None
  if match is None:
    raise ValueError("Invalid browser retry token")
  payload, signature = token.rsplit("_", 1)
  expected = hmac.digest(secret, payload.encode("ascii"), hashlib.sha256).hex()
  if not hmac.compare_digest(signature, expected):
    raise ValueError("Invalid browser retry signature")
  token_generation, issued_hex, expiry_hex, _, _ = match.groups()
  issued_at, expires_at = int(issued_hex, 16), int(expiry_hex, 16)
  if not hmac.compare_digest(token_generation, generation):
    raise ValueError("Browser retry token belongs to another state generation")
  if expires_at - issued_at != TOKEN_LIFETIME_SECONDS or issued_at > now:
    raise ValueError("Invalid browser retry token lifetime")
  if now >= expires_at:
    raise ValueError("Browser retry token expired; reconcile the operation outcome")
  return RetryToken(token_generation, issued_at, expires_at)
