"""Tests of the Docker Compose files."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


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


def test_tailscale_openwebui_has_its_own_profile() -> None:
  """The Open WebUI Tailscale sidecar starts only under its own profile."""
  compose = yaml.safe_load((ROOT / "compose.yml").read_text())
  services = compose["services"]
  assert services["open-webui"]["profiles"] == ["webui"]
  assert services["tailscale-openwebui"]["profiles"] == ["tailscale-openwebui"]


def test_openwebui_embeds_through_a_listed_model() -> None:
  """The embedding line names a catalog row, not the short alias."""
  compose = yaml.safe_load((ROOT / "compose.yml").read_text())
  env = compose["services"]["open-webui"]["environment"]
  assert env["RAG_EMBEDDING_MODEL"] == "mistral/mistral-embed-2312"


def test_openwebui_keeps_the_memory_review_and_the_async_cap() -> None:
  """The Open WebUI memory review and its async embedding, with the cap of the free Mistral key."""
  compose = yaml.safe_load((ROOT / "compose.yml").read_text())
  env = compose["services"]["open-webui"]["environment"]
  assert env["ENABLE_MEMORY_BACKGROUND_REVIEW"] == "true"
  assert env["MEMORIES_REVIEW_INTERVAL_TURNS"] == "5"
  assert env["ENABLE_ASYNC_EMBEDDING"] == "true"
  assert env["RAG_EMBEDDING_CONCURRENT_REQUESTS"] == "2"
  assert env["RAG_EMBEDDING_BATCH_SIZE"] == "32"
