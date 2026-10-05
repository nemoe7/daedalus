"""Tests for `POST /v1/hook/{file}`: the HTTP surface of the hook files."""

from pathlib import Path

from fastapi.testclient import TestClient

from daedalus.providers import hooks
from daedalus.server import api

MASTER = "test-master-key-0003"
AUTH = {"Authorization": f"Bearer {MASTER}"}
CHAT = [{"role": "user", "content": "why is this slow"}]

GOOD = """def on_http(body, key="", prompt="", headers=None):
  return {"model": body.get("model"), "key": key, "prompt": prompt, "header": headers.get("x-probe")}
"""

BROKEN = """def on_http(body, key="", prompt="", headers=None):
  raise RuntimeError("boom")
"""

TEXT = """def on_http(body, key="", prompt="", headers=None):
  return "plain"
"""


def written(name: str, source: str) -> str:
  """Write a hook file in the config folder of the test, and name it for the route."""
  root = hooks.CONFIG_DIR / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / name).write_text(source)
  return name


def test_hook_http_answers_with_the_dict() -> None:
  """The route loads the named file, calls its `on_http`, and answers with the dict."""
  name = written("probe.py", GOOD)
  response = TestClient(api.app, headers={**AUTH, "x-probe": "1"}).post(
    f"/v1/hook/{name}", json={"model": "daedalus/auto", "messages": CHAT}
  )
  assert response.status_code == 200, response.text
  found = response.json()
  assert found["model"] == "daedalus/auto"
  assert found["prompt"] == "why is this slow"
  assert found["header"] == "1"
  assert found["key"] == api.session_key(MASTER, CHAT), (
    "the route passes the session key of the chat"
  )


def test_hook_http_takes_a_nested_path() -> None:
  """A file in a folder under `config/hooks` is named by its path, `nested/probe.py`."""
  root = hooks.CONFIG_DIR / "hooks" / "nested"
  root.mkdir(parents=True, exist_ok=True)
  (root / "probe.py").write_text(GOOD)
  response = TestClient(api.app, headers=AUTH).post(
    "/v1/hook/nested/probe.py", json={"messages": CHAT}
  )
  assert response.status_code == 200, response.text


def test_hook_http_refuses_a_missing_file() -> None:
  """A name with no file under `config/hooks` is a 404."""
  response = TestClient(api.app, headers=AUTH).post(
    "/v1/hook/gone.py", json={"messages": CHAT}
  )
  assert response.status_code == 404, response.text


def test_hook_http_refuses_a_path_outside_the_folder() -> None:
  """A path that leaves the config folder never loads."""
  response = TestClient(api.app, headers=AUTH).post(
    "/v1/hook/../outside.py", json={"messages": CHAT}
  )
  assert response.status_code == 404, response.text


def test_hook_http_reports_a_failure() -> None:
  """A hook that raises is a 500, and the message names the file."""
  name = written("broken.py", BROKEN)
  response = TestClient(api.app, headers=AUTH).post(f"/v1/hook/{name}", json={})
  assert response.status_code == 500, response.text
  assert name in response.text and "boom" in response.text


def test_hook_http_refuses_a_non_dict_answer() -> None:
  """A hook that answers with no dict is a 500."""
  name = written("text.py", TEXT)
  response = TestClient(api.app, headers=AUTH).post(f"/v1/hook/{name}", json={})
  assert response.status_code == 500, response.text


def test_hook_http_asks_for_the_key() -> None:
  """The route carries the same key check as the rest of `/v1`."""
  name = written("probe2.py", GOOD)
  response = TestClient(api.app).post(f"/v1/hook/{name}", json={})
  assert response.status_code == 401, response.text


def test_hook_http_refuses_a_bad_body() -> None:
  """A body that is not a JSON object is a 400."""
  name = written("probe3.py", GOOD)
  client = TestClient(api.app, headers=AUTH)
  assert client.post(f"/v1/hook/{name}", content="[").status_code == 400
  assert client.post(f"/v1/hook/{name}", json=["a"]).status_code == 400


def test_the_shipped_ladder_hook_steps_the_pin() -> None:
  """The shipped `auto_reasoning.py` moves 1 rung up the pin of the chat, and names its effort."""
  module = hooks.load(Path("config") / "hooks" / "auto_reasoning.py")
  api.PENALTIES.pin("chat-1", "daedalus/auto:TIER-C", "kilo/poolside/laguna-s-2.1:free")
  found = module.on_http({}, key="chat-1", prompt="why is this slow", headers={})
  assert found["tier_name"] == "TIER-B"
  assert found["model"] == "daedalus/deinos"
  assert found["reasoning_effort"] == "medium"
  assert found["before"]["tier_name"] == "TIER-C"
  assert found["top"] is False


def test_the_shipped_ladder_hook_reads_a_pool_and_the_prompt() -> None:
  """A pinned pool names its own rung, and a chat with no pin starts from its prompt."""
  module = hooks.load(Path("config") / "hooks" / "auto_reasoning.py")
  api.PENALTIES.pin("chat-2", "daedalus/koinos", "kilo/poolside/laguna-s-2.1:free")
  found = module.on_http({}, key="chat-2", prompt="why is this slow", headers={})
  assert found["before"]["tier_name"] == "TIER-C" and found["tier_name"] == "TIER-B"
  read = api.router.required_tier("why is this slow")
  fresh = module.on_http({}, key="chat-3", prompt="why is this slow", headers={})
  assert fresh["before"]["tier"] == read
  assert fresh["tier"] == min(read + 1, 4)


def test_the_shipped_ladder_hook_reads_a_pool_of_the_body() -> None:
  """A chat with no pin starts from the pool its body names."""
  module = hooks.load(Path("config") / "hooks" / "auto_reasoning.py")
  found = module.on_http(
    {"model": "daedalus/koinos"}, key="chat-5", prompt="hi", headers={}
  )
  assert found["before"]["tier_name"] == "TIER-C"
  assert found["tier_name"] == "TIER-B"


def test_the_shipped_ladder_hook_stops_at_the_top() -> None:
  """`TIER-A` is the top rung: the answer keeps it and `top` says so."""
  module = hooks.load(Path("config") / "hooks" / "auto_reasoning.py")
  api.PENALTIES.pin("chat-4", "daedalus/auto:TIER-A", "kilo/poolside/laguna-s-2.1:free")
  found = module.on_http({}, key="chat-4", prompt="hard", headers={})
  assert found["tier_name"] == "TIER-A"
  assert found["reasoning_effort"] == "high"
  assert found["top"] is True
