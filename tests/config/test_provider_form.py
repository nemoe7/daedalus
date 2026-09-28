"""Tests of the Providers form save."""

import shutil
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from daedalus import config, dashboard
from daedalus.config.provider_edit import merge_text
from daedalus.server import api

MASTER = "master-key-0123456789"

MAIN = """\
# Free providers.
groq:
  api_key: os.environ/GROQ_API_KEY
  exclude:
    - "*guard*" # safety models
    - compound*
  tier:
    TIER-A:
      - openai/gpt-oss-120b
    TIER-D:
      - allam-2-7b
  models:
    "whisper-*": { mode: audio_transcription } # speech to text
  pool: false
"""
SINGLE = """\
api_key: os.environ/OPENROUTER_API_KEY
models:
  z-ai/glm-5.3-flash:
    # A short wait.
    timeout: 15
"""


def test_merge() -> None:
  for path in Path("config/providers").glob("*.yml"):
    text = path.read_text()
    assert merge_text(text, yaml.safe_load(text)) == text, f"{path} changes on a no-op"
  blocks = yaml.safe_load(MAIN)
  groq = blocks["groq"]
  groq["exclude"].append("*tts*")
  groq["tier"]["TIER-A"].append("qwen/qwen3.8-27b")
  del groq["tier"]["TIER-D"]
  groq["models"]["whisper-*"]["rpm"] = 20
  groq["models"]["llama-*"] = {"tpm": 6000}
  groq["models"]["*embed*"] = {"mode": "embedding"}
  groq["pool"] = 0
  text = merge_text(MAIN, blocks)
  assert yaml.safe_load(text) == blocks, text
  for kept in ("# Free providers.", '- "*guard*" # safety models', "# speech to text"):
    assert kept in text, (kept, text)
  assert '"whisper-*": { mode: audio_transcription, rpm: 20 }' in text, text
  assert "llama-*: { tpm: 6000 }" in text, "a new pattern gets a one-line map"
  assert "TIER-D" not in text and "pool: 0" in text, "0 does not stay false"
  assert text.count("\n    - ") == 3, "the list indent stays"
  assert '    - "*tts*"\n' in text, "a new pattern gets double quotes, as in the files"
  assert '"llama-*"' not in text, "a plain pattern gets none"
  assert '    "*embed*": { mode: embedding }\n' in text, "a new key gets double quotes"

  single = yaml.safe_load(SINGLE)
  single["models"]["z-ai/glm-5.3-flash"]["rpm"] = 15
  single["models"]["z-ai/*"] = {"pool": False}
  single["exclude"] = ["*:free"]
  text = merge_text(SINGLE, single)
  assert yaml.safe_load(text) == single, text
  assert "    # A short wait.\n    timeout: 15\n    rpm: 15\n" in text, (
    "block style stays"
  )
  assert "z-ai/*: { pool: false }" in text and '- "*:free"' in text, text
  fresh = merge_text("", {"x": {"api_key": "k", "models": {"a*": {"rpm": 1}}}})
  assert "models:\n    a*: { rpm: 1 }" in fresh, fresh


def test_endpoints(folder: Path) -> None:
  session = dashboard.cookie(dashboard.secret(), time.time())
  client = TestClient(api.app, headers={"X-Daedalus-Session": session})
  main = config.DEFAULT_PATH
  single = main.parent / "openrouter.yml"
  single.write_text(SINGLE)
  files = client.get("/ui/api/files").json()
  forms = {Path(f["path"]).name: f["blocks"] for f in files}
  assert forms["free.yml"] == yaml.safe_load(MAIN), forms
  assert forms["openrouter.yml"] == {"openrouter": yaml.safe_load(SINGLE)}, forms
  keys = client.get("/ui/api/provider-keys").json()
  assert {"pool", "timeout", "rpm", "mode"} <= set(keys), keys

  blocks = forms["free.yml"]
  blocks["groq"]["exclude"].append("*tts*")
  saved = client.put("/ui/api/providers", json={"path": str(main), "blocks": blocks})
  assert saved.status_code == 200, saved.text
  assert saved.json()["text"] == main.read_text(), "the answer holds the new text"
  assert '- "*tts*"' in main.read_text(), main.read_text()
  assert "# speech to text" in main.read_text(), "the comments stay"
  assert "*tts*" in config.get_config()["groq"]["exclude"], "the save reloads"

  moved = {"openrouter": {**yaml.safe_load(SINGLE), "api_key": "os.environ/OR_KEY"}}
  saved = client.put("/ui/api/providers", json={"path": str(single), "blocks": moved})
  assert saved.status_code == 200, saved.text
  assert single.read_text().startswith("api_key: os.environ/OR_KEY\n"), (
    "no provider key"
  )
  assert "# A short wait." in single.read_text()

  for bad in (
    {"path": str(folder / "x.yml"), "blocks": {}},
    {"path": str(main), "blocks": {"groq": ["a"]}},
    {"path": str(single), "blocks": {"other": {}}},
  ):
    assert client.put("/ui/api/providers", json=bad).status_code == 400, bad
  assert TestClient(api.app).get("/ui/api/provider-keys").status_code == 401


@pytest.fixture(scope="module")
def folder(tmp_path_factory: pytest.TempPathFactory):
  original = config.DEFAULT_PATH, dashboard.FILES
  folder = tmp_path_factory.mktemp("config")
  config.DEFAULT_PATH = Path(shutil.copy("config/providers/free.yml", folder))
  config.DEFAULT_PATH.write_text(MAIN)
  dashboard.FILES = (config.DEFAULT_PATH,)
  yield folder
  config.DEFAULT_PATH, dashboard.FILES = original
  config.set_config(None)
