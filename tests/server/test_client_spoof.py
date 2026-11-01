"""The client spoof hook presents one harness as another through outbound identity headers."""

import json
import shutil
from pathlib import Path

import httpx
import pytest

from daedalus import config, store
from daedalus.providers import hooks
from daedalus.server import api, upstream

REPO = Path(__file__).resolve().parents[2]
MASTER = "test-master-key-0001"
SEEN: list[httpx.Headers] = []
CHAT = {"model": "daedalus/moros", "messages": [{"role": "user", "content": "hi"}]}


def upstream_answer(request: httpx.Request) -> httpx.Response:
  SEEN.append(request.headers)
  message = {"role": "assistant", "content": "hi"}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


@pytest.fixture
async def client(state_folder: Path):
  folder = state_folder / "hooks"
  folder.mkdir(parents=True, exist_ok=True)
  shutil.copy(REPO / "hooks" / "client_spoof.py", folder / "client_spoof.py")
  block = {
    "api_base": "https://p.test/v1",
    "api_key": "provider-key",
    "tier": {"TIER-D": ["x"]},
    "hooks": [{"on-upstream": "hooks/client_spoof.py"}],
  }
  config.set_config({"p": block})
  store.write_store([{"id": "p/x"}])
  api.PENALTIES.clear()
  api.COOLDOWNS.clear()
  SEEN.clear()
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_answer)) as outer:
    upstream.set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as inner:
      yield inner
  upstream.set_client(None)
  config.set_config(None)


def save_rules(state_folder: Path, rules: list[dict]) -> None:
  path = state_folder / "hooks" / "client_spoof.json"
  path.write_text(json.dumps({"rules": rules}), encoding="utf-8")


async def test_kilo_becomes_opencode(
  client: httpx.AsyncClient, state_folder: Path
) -> None:
  """First rule presents the Kilo harness as OpenCode, and keeps session metadata."""
  save_rules(
    state_folder, [{"source": "kilo", "target": "opencode", "version": "2.2.5"}]
  )
  headers = {
    "Authorization": f"Bearer {MASTER}",
    "User-Agent": "Kilo-Code/7.8.18",
    "X-Title": "Kilo Code",
    "HTTP-Referer": "https://kilo.ai/",
    "X-Session-Affinity": "s1",
    "x-opencode-session": "ses_keep",
  }
  response = await client.post("/v1/chat/completions", json=CHAT, headers=headers)
  assert response.status_code == 200, response.text
  sent = SEEN[-1]
  assert sent["user-agent"] == "opencode/latest/2.2.5/cli", sent
  assert sent["x-title"] == "opencode", sent
  assert sent["http-referer"] == "https://opencode.ai/", sent
  assert sent["x-opencode-client"] == "cli", sent
  assert sent["x-session-affinity"] == "s1", sent
  assert sent["x-opencode-session"] == "ses_keep", sent
  assert sent["authorization"] == "Bearer provider-key", sent


async def test_no_rule_leaves_identity_unchanged(
  client: httpx.AsyncClient, state_folder: Path
) -> None:
  """An empty rule list changes no identity header."""
  save_rules(state_folder, [])
  headers = {
    "Authorization": f"Bearer {MASTER}",
    "User-Agent": "Kilo-Code/7.8.18",
    "X-Title": "Kilo Code",
  }
  response = await client.post("/v1/chat/completions", json=CHAT, headers=headers)
  assert response.status_code == 200, response.text
  sent = SEEN[-1]
  assert sent["user-agent"] == "Kilo-Code/7.8.18", sent
  assert sent["x-title"] == "Kilo Code", sent


async def test_first_matching_rule_wins(
  client: httpx.AsyncClient, state_folder: Path
) -> None:
  """The first matching source rule wins over a later rule."""
  save_rules(
    state_folder,
    [
      {"source": "kilo", "target": "opencode", "version": "2.2.5"},
      {"source": "kilo", "target": "owui", "version": "0.12.5"},
    ],
  )
  headers = {"Authorization": f"Bearer {MASTER}", "User-Agent": "Kilo-Code/7.8.18"}
  response = await client.post("/v1/chat/completions", json=CHAT, headers=headers)
  assert response.status_code == 200, response.text
  assert SEEN[-1]["x-title"] == "opencode", SEEN[-1]


def test_source_detection_and_profiles() -> None:
  """The hook detects the three supported clients and builds only identity headers."""
  path = REPO / "hooks" / "client_spoof.py"
  module = hooks.load(path)
  assert module is not None
  assert module.source_of({"user-agent": "Kilo-Code/7.8.18"}) == "kilo"
  assert module.source_of({"x-title": "Open WebUI"}) == "owui"
  assert module.source_of({"user-agent": "opencode/latest/2.2.5/cli"}) == "opencode"
  assert module.source_of({"user-agent": "curl/8"}) == ""
  profile = module.profile("opencode", "2.2.5")
  assert profile["User-Agent"] == "opencode/latest/2.2.5/cli"
  assert profile["X-Title"] == "opencode"
  assert "Authorization" not in profile


def test_rules_validate() -> None:
  """Rules accept the supported clients and reject unknown clients and duplicate sources."""
  module = hooks.load(REPO / "hooks" / "client_spoof.py")
  assert module is not None
  assert module.rules_ok(
    [{"source": "kilo", "target": "opencode", "version": "latest"}]
  )
  assert not module.rules_ok([{"source": "curl", "target": "opencode", "version": "1"}])
  assert not module.rules_ok(
    [
      {"source": "kilo", "target": "opencode", "version": "1"},
      {"source": "kilo", "target": "owui", "version": "1"},
    ]
  )
