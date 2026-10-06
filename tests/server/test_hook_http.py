"""Tests for `POST /v1/hook/{file}`: the HTTP surface of the hook files."""

from fastapi.testclient import TestClient

from daedalus.providers import hooks
from daedalus.server import api

MASTER = "test-master-key-0003"
AUTH = {"Authorization": f"Bearer {MASTER}"}
CHAT = [{"role": "user", "content": "why is this slow"}]

GOOD = """def on_http(body, key="", prompt="", headers=None):
  return {"model": body.get("model"), "key": key, "prompt": prompt, "header": headers.get("x-probe")}
"""

GOOD_PIN = """def on_http(body, key="", prompt="", headers=None, pin=None):
  return {"pin": pin}
"""

GOOD_LEVEL = """def on_http(body, key="", prompt="", headers=None, level=None):
  return {"level": level}
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


def test_hook_http_hands_the_pin_of_the_session() -> None:
  """The route passes the last pin of the session key to a file that names `pin`."""
  name = written("probe_pin.py", GOOD_PIN)
  api.PENALTIES.pin(api.session_key(MASTER, CHAT), "daedalus/deinos", "kilo/x")
  response = TestClient(api.app, headers=AUTH).post(
    f"/v1/hook/{name}", json={"messages": CHAT}
  )
  assert response.status_code == 200, response.text
  assert response.json()["pin"] == ["daedalus/deinos", "kilo/x"]


def test_hook_http_hands_the_level_of_the_session() -> None:
  """The route passes the reasoning level of the last answer to a file that names `level`."""
  name = written("probe_level.py", GOOD_LEVEL)
  api.PENALTIES.record_level(api.session_key(MASTER, CHAT), "medium")
  response = TestClient(api.app, headers=AUTH).post(
    f"/v1/hook/{name}", json={"messages": CHAT}
  )
  assert response.status_code == 200, response.text
  assert response.json()["level"] == "medium"


def test_hook_http_takes_a_nested_path() -> None:
  """A file in a folder under `config/hooks` is named by its path, `nested/probe.py`."""
  root = hooks.CONFIG_DIR / "hooks" / "nested"
  root.mkdir(parents=True, exist_ok=True)
  (root / "probe.py").write_text(GOOD)
  response = TestClient(api.app, headers=AUTH).post(
    "/v1/hook/nested/probe.py", json={"messages": CHAT}
  )
  assert response.status_code == 200, response.text


def test_hook_http_takes_the_short_name() -> None:
  """The route takes the file name of the docs with no suffix, `owui_auto_reasoning`."""
  name = written("probe_short.py", GOOD)
  response = TestClient(api.app, headers=AUTH).post(
    f"/v1/hook/{name.removesuffix('.py')}", json={"messages": CHAT}
  )
  assert response.status_code == 200, response.text
  assert response.json()["prompt"] == "why is this slow"


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
  """A hook that raises is a 500. The message names the file, and the exception text stays in the log."""
  name = written("broken.py", BROKEN)
  response = TestClient(api.app, headers=AUTH).post(f"/v1/hook/{name}", json={})
  assert response.status_code == 500, response.text
  assert name in response.text
  assert "boom" not in response.text, "the exception text is not for the caller"


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
