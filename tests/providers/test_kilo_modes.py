"""Tests for the Kilo catalog rows, which keep only the models that make text."""

import pytest

from daedalus.catalog import discovery

FREE = {"prompt": "0", "completion": "0"}


def listing(url: str, headers: dict[str, str]) -> dict:
  """A Kilo model list with a text model and 2 free models that make no text."""
  return {
    "data": [
      {
        "id": f"some/{name}:free",
        "pricing": FREE,
        "architecture": {"input_modalities": ["text"], "output_modalities": outputs},
      }
      for name, outputs in (
        ("chat", ["text"]),
        ("embed", ["embeddings"]),
        ("image", ["image"]),
      )
    ]
    + [{"id": "some/old:free", "pricing": FREE}]
  }


def test_text_only(monkeypatch: pytest.MonkeyPatch) -> None:
  """The gateway takes only chat requests, so models that make no text stay out."""
  block = {"api_key": "k", "exclude": ["!*:free"]}
  lines, skipped = discovery.build_rows({"kilo": block}, listing)
  assert not skipped, skipped
  assert sorted(lines) == ["kilo/some/chat:free", "kilo/some/old:free"], lines
