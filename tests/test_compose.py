"""Tests of the Docker Compose files."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _env_file_paths(service: dict[str, object]) -> list[tuple[str, bool]]:
  """Return each Compose env file with its required flag."""
  result = []
  for item in service.get("env_file", []):
    if isinstance(item, str):
      result.append((item, True))
    else:
      result.append((item["path"], item.get("required", True)))
  return result


def _service_env(compose: dict[str, object], name: str) -> dict[str, object]:
  """Resolve checked-in service defaults, then Compose's explicit bindings."""
  service = compose["services"][name]
  result = {}
  for filename, _required in _env_file_paths(service):
    path = ROOT / filename
    if not path.is_file() or path.name != "defaults.env":
      continue
    for line in path.read_text().splitlines():
      if line and not line.startswith("#"):
        key, separator, value = line.partition("=")
        assert separator, line
        result[key] = value
  result.update(service.get("environment", {}))
  return result


def test_every_service_layers_managed_defaults_before_local_overrides() -> None:
  """Every service gets shipped defaults and an optional user-owned env layer."""
  expected = {
    "api": 3,
    "open-webui": 39,
    "webui-db": 3,
    "tika": 1,
    "searxng": 2,
    "headroom": 7,
    "tailscale": 7,
    "tailscale-openwebui": 7,
  }
  for filename in ("compose.yml", "compose.dev.yml"):
    compose = yaml.safe_load((ROOT / filename).read_text())
    assert set(compose["services"]) == set(expected)
    for name, service in compose["services"].items():
      paths = _env_file_paths(service)
      defaults = f"./services/{name}/defaults.env"
      local = f"./services/{name}/local.env"
      assert paths[0] == (defaults, True), (filename, name, paths)
      assert paths[-1] == (local, False), (filename, name, paths)
      assert (ROOT / defaults).is_file()
      assert len(_service_env(compose, name)) == expected[name]


def test_dev_copy() -> None:
  """compose.dev.yml is compose.yml with a daedalus build and the 2 dev variables."""
  main = yaml.safe_load((ROOT / "compose.yml").read_text())
  dev = yaml.safe_load((ROOT / "compose.dev.yml").read_text())
  image = main["services"]["api"].pop("image")
  assert image.partition("/")[0] == "ghcr.io", (
    "the image comes from the project registry"
  )
  built = dev["services"]["api"]
  build = {"context": ".", "args": {"VERSION": "${DAEDALUS_VERSION:-dev}"}}
  assert (built.pop("image"), built.pop("build"), built.pop("pull_policy")) == (
    "${DAEDALUS_DEV_IMAGE:-daedalus:dev}",
    build,
    "${DAEDALUS_DEV_PULL:-build}",
  )
  assert dev == main


def test_host_ports_use_root_env_defaults() -> None:
  """Root env values change host ports without changing container ports."""
  for filename in ("compose.yml", "compose.dev.yml"):
    compose = yaml.safe_load((ROOT / filename).read_text())
    services = compose["services"]
    assert services["api"]["ports"] == ["${DAEDALUS_HOST_PORT:-3357}:3357"]
    assert services["open-webui"]["ports"] == ["${OPENWEBUI_PORT:-3000}:8080"]

  example = (ROOT / ".env.example").read_text()
  assert "# DAEDALUS_HOST_PORT=3357" in example
  assert "# OPENWEBUI_PORT=3000" in example


def test_tailscale_openwebui_has_its_own_profile() -> None:
  """The Open WebUI Tailscale sidecar starts only under its own profile."""
  compose = yaml.safe_load((ROOT / "compose.yml").read_text())
  services = compose["services"]
  assert services["open-webui"]["profiles"] == ["webui"]
  assert services["tailscale-openwebui"]["profiles"] == ["tailscale-openwebui"]


def test_openwebui_embeds_through_a_listed_model() -> None:
  """The embedding line names a catalog row, not the short alias."""
  compose = yaml.safe_load((ROOT / "compose.yml").read_text())
  env = _service_env(compose, "open-webui")
  assert env["RAG_EMBEDDING_MODEL"] == "mistral/mistral-embed-2312"


def test_headroom_pin_allows_its_internal_bind() -> None:
  """Headroom 0.40 acknowledges its token-less bind inside the Compose network."""
  compose = yaml.safe_load((ROOT / "compose.yml").read_text())
  headroom = compose["services"]["headroom"]
  assert headroom["image"] == "ghcr.io/headroomlabs-ai/headroom:0.40.0-slim"
  assert _service_env(compose, "headroom")["HEADROOM_ALLOW_UNAUTHENTICATED_BIND"] == "1"
  assert "ports" not in headroom, "the unauthenticated bind must remain internal"


def test_openwebui_forwards_only_the_chat_id() -> None:
  """A new bundled Open WebUI sends the chat ID without the user's personal fields."""
  compose = yaml.safe_load((ROOT / "compose.yml").read_text())
  env = _service_env(compose, "open-webui")
  assert env["ENABLE_FORWARD_USER_INFO_HEADERS"] == "false"
  configs = json.loads(env["OPENAI_API_CONFIGS"])
  assert configs == {"0": {"headers": {"X-OpenWebUI-Chat-Id": "{{CHAT_ID}}"}}}


def test_openwebui_keeps_the_memory_review_and_the_async_cap() -> None:
  """The Open WebUI memory review and its async embedding, with the cap of the free Mistral key."""
  compose = yaml.safe_load((ROOT / "compose.yml").read_text())
  env = _service_env(compose, "open-webui")
  assert env["ENABLE_MEMORY_BACKGROUND_REVIEW"] == "true"
  assert env["MEMORIES_REVIEW_INTERVAL_TURNS"] == "5"
  assert env["ENABLE_ASYNC_EMBEDDING"] == "true"
  assert env["RAG_EMBEDDING_CONCURRENT_REQUESTS"] == "2"
  assert env["RAG_EMBEDDING_BATCH_SIZE"] == "32"
