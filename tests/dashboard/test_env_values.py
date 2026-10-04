"""Tests of the saved values of the provider keys: env:NAME and db:NAME."""

import os
import shutil
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from daedalus import config, dashboard, providers, store
from daedalus.server import api
from daedalus.store import saved_env

MAIN = """\
groq:
  api_key: db:GROQ_API_KEY
  client_keys:
    kilo: db:GROQ_API_KEY_KILO
  # api_key: db:OLD_KEY
cloudflare:
  api_key: db:CLOUDFLARE_API_KEY
legacy:
  api_key: os.environ/LEGACY_API_KEY
"""
SECRET = "test-secret-value"


def rows(client: TestClient) -> dict[str, dict]:
  found = client.get("/ui/api/env")
  assert found.status_code == 200, found.text
  return {row["name"]: row for row in found.json()}


def test_values(client: TestClient) -> None:
  os.environ["CLOUDFLARE_API_KEY"] = "from-env"
  os.environ["GROQ_API_KEY"] = "gsk-env"
  config.load_config(config.DEFAULT_PATH)
  shown = rows(client)
  assert set(shown) == {
    "GROQ_API_KEY",
    "GROQ_API_KEY_KILO",
    "CLOUDFLARE_API_KEY",
    "CLOUDFLARE_ACCOUNT_ID",
  }, "no comment lines, and the defaults of the providers in use"
  assert shown["CLOUDFLARE_ACCOUNT_ID"]["used"] == ["cloudflare defaults"]
  assert shown["GROQ_API_KEY"]["state"] == "env" and shown["GROQ_API_KEY_KILO"][
    "state"
  ] == ("missing")
  # db: is DB-only, no env fallback, so groq api_key is empty until saved
  assert config.get_config()["groq"]["api_key"] == "", "db: is DB-only"

  saved = client.put(
    "/ui/api/env", json={"name": "GROQ_API_KEY", "value": f" {SECRET} "}
  )
  assert saved.status_code == 200, saved.text
  assert SECRET not in saved.text and SECRET not in client.get("/ui/api/env").text
  row = rows(client)["GROQ_API_KEY"]
  assert row["name"] == "GROQ_API_KEY"
  assert row["state"] == "saved"
  assert row["end"] == "alue"
  assert row["has_saved"] is True
  assert row["has_env"] is True
  assert row["start"] == "test"
  assert row["length"] == len(SECRET)
  assert config.get_config()["groq"]["api_key"] == SECRET, "the saved value wins"
  # cloudflare account_id is separate field, api_base constructed only when account_id set
  assert "account" not in providers.settings("cloudflare", {}).get("api_base", "")
  client.put("/ui/api/env", json={"name": "CLOUDFLARE_ACCOUNT_ID", "value": "acct1"})
  assert "/accounts/acct1/" in providers.settings("cloudflare", {})["api_base"]
  assert rows(client)["CLOUDFLARE_ACCOUNT_ID"]["end"] is None, (
    "a short value shows no end"
  )

  for bad in (
    {"name": "DAEDALUS_MASTER_KEY", "value": "x"},
    {"name": "OLD_KEY", "value": "x"},
    {"name": "GROQ_API_KEY", "value": "two words"},
    {"name": "GROQ_API_KEY", "value": ""},
    {"name": "GROQ_API_KEY", "value": 3},
    {"name": "GROQ_API_KEY", "value": "x" * (dashboard.MAX_VALUE + 1)},
  ):
    assert client.put("/ui/api/env", json=bad).status_code == 400, bad

  cleared = client.request("DELETE", "/ui/api/env", json={"name": "GROQ_API_KEY"})
  assert cleared.status_code == 200, cleared.text
  assert config.get_config()["groq"]["api_key"] == "", "db: is DB-only, back to empty"
  again = client.request("DELETE", "/ui/api/env", json={"name": "GROQ_API_KEY"})
  assert again.status_code == 400, "no saved value"
  assert TestClient(api.app).get("/ui/api/env").status_code == 401
  assert TestClient(api.app).put("/ui/api/env", json={}).status_code == 401


def test_form_moves_keys(client: TestClient) -> None:
  blocks = yaml.safe_load(MAIN)
  blocks["groq"]["api_key"] = "gsk-pasted-0000000001"
  blocks["groq"]["client_keys"]["owui"] = "gsk-pasted-0000000002"
  path = str(config.DEFAULT_PATH)
  saved = client.put("/ui/api/providers", json={"path": path, "blocks": blocks})
  assert saved.status_code == 200, saved.text
  text = config.DEFAULT_PATH.read_text()
  assert "gsk-pasted" not in text, "no key in the YAML"
  assert "api_key: db:GROQ_API_KEY\n" in text, text
  assert "owui: db:GROQ_API_KEY_OWUI" in text, text
  assert saved_env.read(store.MODELS_DB)["GROQ_API_KEY_OWUI"] == "gsk-pasted-0000000002"
  assert config.get_config()["groq"]["api_key"] == "gsk-pasted-0000000001"
  assert (
    config.client_key(config.get_config()["groq"], "owui") == "gsk-pasted-0000000002"
  )
  assert dashboard.env_name("my-llm", "a.b") == "MY_LLM_API_KEY_A_B"
  assert dashboard.new_file_text("my-llm").count("env:MY_LLM_API_KEY\n") == 1


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
  original = config.DEFAULT_PATH, dashboard.FILES
  folder = tmp_path_factory.mktemp("config")
  config.DEFAULT_PATH = Path(shutil.copy("config/providers/free.yml", folder))
  config.DEFAULT_PATH.write_text(MAIN)
  dashboard.FILES = (config.DEFAULT_PATH,)
  session = dashboard.cookie(dashboard.secret(), time.time())
  yield TestClient(api.app, headers={"x-daedalus-session": session})
  config.DEFAULT_PATH, dashboard.FILES = original
  config.set_config(None)
