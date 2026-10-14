"""Check valid sessions and malformed tokens at the dashboard boundary."""

import time

import pytest
from fastapi.testclient import TestClient

from daedalus import dashboard
from daedalus.server import api


@pytest.mark.parametrize(
  "token",
  ["bad", "4102444800.é", "².x", "9" * 5000 + ".x"],
  ids=["invalid", "unicode-signature", "unicode-expiry", "long-expiry"],
)
def test_malformed_live_session_returns_unauthorized(token: str) -> None:
  client = TestClient(api.app, raise_server_exceptions=False)
  response = client.get("/ui/api/requests/live", params={"session": token})
  assert response.status_code == 401
  assert response.json()["error"]["code"] == 401


def test_valid_expired_and_changed_signatures_keep_their_contract() -> None:
  key = "synthetic-session-key"
  token = dashboard.cookie(key, time.time(), seconds=60)
  assert dashboard.live(key, token)
  assert not dashboard.live(key, dashboard.cookie(key, time.time() - 120, seconds=60))
  changed = token[:-1] + ("0" if token[-1] != "0" else "1")
  assert not dashboard.live(key, changed)
