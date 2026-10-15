"""
title: Auto reasoning effort
author: nemo
description: Requires daedalus and its owui_auto_reasoning_effort.py hook to select reasoning effort.
required_open_webui_version: 0.10.0
version: 1.0.0
licence: nemoe7 Noncommercial and Public-Interest License 1.2.0
"""

# fmt: off

from typing import Literal

from pydantic import BaseModel, Field


class Filter:
  """Send per-request controls to the daedalus auto-effort hook."""

  class Valves(BaseModel):
    sensitivity: float = Field(
      default=0.75,
      ge=0,
      le=1,
      description="The confidence threshold, from 0 to 1.",
    )
    minimum_effort: Literal["none", "low", "medium", "high"] = Field(
      default="none",
      description="The lowest automatic effort, within the supported efforts of the model.",
    )

  def __init__(self) -> None:
    self.valves = self.Valves()

  async def inlet(self, body: dict) -> dict:
    """Add controls without changing explicit effort or messages."""
    body["_daedalus_auto_effort"] = self.valves.model_dump()
    return body
