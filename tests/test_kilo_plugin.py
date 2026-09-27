"""The Kilo Code plugin copies the Daedalus limits into the Kilo config."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "integrations" / "kilo" / "daedalus.js"
KEY = "sk-test"
ROWS = [
  {
    "id": "daedalus/auto",
    "object": "model",
    "owned_by": "daedalus",
    "max_input_tokens": 262144,
    "max_output_tokens": 32768,
    "supports_function_calling": True,
    "supports_reasoning": True,
  },
  {
    "id": "gemini/gemini-3.7-flash",
    "object": "model",
    "owned_by": "daedalus",
    "max_input_tokens": 1048576,
    "supports_reasoning": False,
    "supports_vision": True,
  },
]
DRIVER = """
const plugin = (await import(process.argv[1])).default;
const hooks = await plugin.server({});
const config = JSON.parse(process.argv[2]);
await hooks.config(config);
console.log(JSON.stringify(config));
"""


class Stub(BaseHTTPRequestHandler):
  def do_GET(self) -> None:
    allowed = self.headers.get("Authorization") == f"Bearer {KEY}"
    body = json.dumps({"object": "list", "data": ROWS} if allowed else {}).encode()
    self.send_response(200 if allowed else 401)
    self.send_header("Content-Type", "application/json")
    self.end_headers()
    self.wfile.write(body)

  def log_message(self, *args: object) -> None:
    pass


def provider(base: str, options: dict | None = None) -> dict:
  return {
    "provider": {
      "daedalus": {
        "options": {"baseURL": base, **(options or {})},
        "models": {
          "daedalus/auto": {"limit": {"context": 1, "input": 5, "output": 9}},
          "gemini/gemini-3.7-flash": {"name": "Flash"},
          "missing/model": {"name": "Kept"},
        },
      },
      "other": {"options": {"baseURL": base}, "models": {"daedalus-like": {}}},
    }
  }


def run(config: dict, data: str, env_key: str = "") -> tuple[dict, str]:
  env = {**os.environ, "XDG_DATA_HOME": data, "DAEDALUS_API_KEY": env_key}
  done = subprocess.run(
    ["node", "--input-type=module", "-e", DRIVER, PLUGIN.as_uri(), json.dumps(config)],
    capture_output=True,
    text=True,
    env=env,
    timeout=30,
    check=True,
  )
  return json.loads(done.stdout)["provider"], done.stderr


def check_patch(base: str, data: str) -> None:
  found, log = run(provider(base, {"apiKey": KEY}), data)
  models = found["daedalus"]["models"]
  assert models["daedalus/auto"] == {
    "limit": {"context": 262144, "output": 0},
    "reasoning": True,
    "tool_call": True,
  }, models["daedalus/auto"]
  flash = models["gemini/gemini-3.7-flash"]
  assert flash["limit"] == {"context": 1048576, "output": 0}, flash
  assert flash["modalities"] == {"input": ["text", "image"]} and flash["attachment"], (
    flash
  )
  assert flash["reasoning"] is False and flash["name"] == "Flash", flash
  assert models["missing/model"] == {"name": "Kept"}, "a model Daedalus does not list"
  assert found["other"]["models"] == {"daedalus-like": {}}, "not a Daedalus provider"
  assert "patched 2 models" in log, log


def check_keys(base: str, data: str) -> None:
  kilo = Path(data) / "kilo"
  kilo.mkdir()
  (kilo / "auth.json").write_text(json.dumps({"daedalus": {"type": "api", "key": KEY}}))
  found, _ = run(provider(base), data)
  assert "limit" in found["daedalus"]["models"]["gemini/gemini-3.7-flash"], "auth store"
  (kilo / "auth.json").unlink()
  found, _ = run(provider(base), data, env_key=KEY)
  assert "limit" in found["daedalus"]["models"]["gemini/gemini-3.7-flash"], "variable"


def check_fail_open(base: str, data: str) -> None:
  config = provider(base, {"apiKey": "sk-wrong"})
  found, log = run(config, data)
  assert found == config["provider"], "no change on an error"
  assert "fail open GET" in log and "HTTP 401" in log, log


def main() -> None:
  if shutil.which("node") is None:
    print("skip: kilo plugin, no node")
    return
  server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  base = f"http://127.0.0.1:{server.server_address[1]}/v1"
  try:
    with tempfile.TemporaryDirectory() as data:
      check_patch(base, data)
      check_keys(base, data)
      check_fail_open(base, data)
  finally:
    server.shutdown()
  print("ok: kilo plugin")


if __name__ == "__main__":
  main()
