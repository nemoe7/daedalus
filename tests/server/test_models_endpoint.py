"""Pool models advertise the canonical effort ladder in the served model list."""

import pytest
from fastapi.testclient import TestClient

from daedalus import store
from daedalus.routing import router
from daedalus.server import api

MASTER = "test-master-key-0001"
CONFIG = {
  "b": {"api_key": "k", "api_base": "https://b.test/v1", "tier": {"TIER-B": ["*"]}}
}
ROWS = [
  {
    "id": "b/1",
    "mode": "chat",
    "supports_reasoning": 1,
    "supported_efforts": ["low", "high"],
  },
  {"id": "b/2", "mode": "chat", "supports_reasoning": 1},
]


@pytest.fixture(scope="module")
def client():
  store.write_store(ROWS)
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(api, "get_config", lambda: CONFIG)
    yield TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})


def by_id(data: list[dict], model: str) -> dict:
  return next(entry for entry in data if entry["id"] == model)


def test_pools_advertise_canonical_ladder(client: TestClient) -> None:
  data = client.get("/v1/models").json()["data"]
  for pool in (*router.POOLS, router.RESERVED_MODEL):
    assert by_id(data, pool)["supported_efforts"] == list(router.EFFORT_RANKS), pool


def test_concrete_models_keep_served_fields(client: TestClient) -> None:
  data = client.get("/v1/models").json()["data"]
  assert "supported_efforts" not in by_id(data, "b/1"), "a stored list stays out"
  assert "supported_efforts" not in by_id(data, "b/2"), "no stored list, no field"
