"""The on-catalog hooks change the catalog rows of their provider before the store write."""

import pytest

from daedalus import catalog
from daedalus.providers import hooks


def test_with_hooks(caplog: pytest.LogCaptureFixture) -> None:
  """A hook changes the columns but not the id. A provider with no key gets no hook call."""
  folder = hooks.ROOT / "hooks"
  folder.mkdir(parents=True, exist_ok=True)
  (folder / "rows.py").write_text(
    "def on_catalog(row, model, api_base, headers):\n"
    "  row['max_input_tokens'] = 5\n"
    "  row['id'] = 'moved'\n"
    "  row['seen'] = (api_base, headers['Authorization'])\n"
  )
  entry = [{"on-catalog": "hooks/rows.py"}]
  setup = {
    "p": {"api_base": "https://p.test/v1", "api_key": "k", "hooks": entry},
    "nokey": {"api_base": "https://n.test/v1", "hooks": entry},
    "plain": {"api_base": "https://q.test/v1", "api_key": "k"},
  }
  rows = [{"id": "p/a", "max_input_tokens": 1}, {"id": "nokey/b"}, {"id": "plain/c"}]
  found = catalog.with_hooks(setup, rows)
  assert found[0] == {
    "id": "p/a",
    "max_input_tokens": 5,
    "seen": ("https://p.test/v1", "Bearer k"),
  }
  assert found[1:] == rows[1:]
  assert "no on-catalog hooks for nokey/b" in caplog.text
