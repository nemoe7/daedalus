"""Permission gates reject invalid preferences without changing supported overrides."""

import asyncio

import pytest
import test_owui_github_tool as github
import test_owui_google_tool as google
import test_owui_manager as manager
from pydantic import ValidationError

TOOLS = (github.tool.Tools, google.tool.Tools, manager.Tools)
MODES = ("Always ask", "Allow reads", "Always allow")
INVALID = ("Always ask ", "unknown", "", True, False, 0, [], {})


@pytest.mark.parametrize("tools", TOOLS)
@pytest.mark.parametrize("mode", INVALID)
def test_user_valves_reject_unknown_modes(tools, mode):
  with pytest.raises(ValidationError):
    tools.UserValves(mode=mode)


@pytest.mark.parametrize("tools", TOOLS)
@pytest.mark.parametrize("mode", INVALID)
@pytest.mark.parametrize("approve", [False, True])
def test_unknown_runtime_mode_requires_confirmation(tools, mode, approve):
  tool = tools()
  tool.valves.permissions = "Always allow"
  calls = []

  async def confirm(event):
    calls.append("ask")
    return approve

  async def read():
    calls.append("read")
    return {"id": "synthetic"}

  asyncio.run(
    tool._read(
      "read",
      "synthetic",
      read,
      __user__={"valves": {"mode": mode}},
      __event_call__=confirm,
    )
  )
  assert calls == (["ask", "read"] if approve else ["ask"])


@pytest.mark.parametrize("tools", TOOLS)
@pytest.mark.parametrize("base", (*MODES, "invalid"))
@pytest.mark.parametrize("mode", ("default", *MODES))
def test_supported_modes_keep_user_precedence_and_inheritance(tools, base, mode):
  tool = tools()
  tool.valves.permissions = base
  expected = base if mode == "default" else mode
  expected = expected if expected in MODES else "Always ask"
  assert tool._permissions({"valves": tool.UserValves(mode=mode)}) == expected
  if mode == "default":
    assert tool._permissions() == expected
