"""Effort matching rounds up to the next supported rung of the canonical ladder."""

from daedalus.server import upstream


def test_exact_match_and_unknown_asked() -> None:
  """An exact listed name stays, and an unknown asked name maps to nothing."""
  assert upstream.nearest_effort("high", ["low", "high"]) == "high"
  assert upstream.nearest_effort("stray", ["low", "high"]) is None


def test_round_up_past_a_gap() -> None:
  """A level the list skips takes the next listed rung above it."""
  assert upstream.nearest_effort("high", ["low", "max"]) == "max"
  assert upstream.nearest_effort("medium", ["low", "high"]) == "high"
  assert upstream.nearest_effort("minimal", ["low", "high"]) == "low"


def test_cap_at_the_top_of_the_ladder() -> None:
  """A level above every listed rung takes the highest listed rung."""
  assert upstream.nearest_effort("xhigh", ["low", "medium", "high"]) == "high"
  assert upstream.nearest_effort("max", ["none", "low"]) == "low"


def test_none_asked_takes_the_lowest_real_rung() -> None:
  """A `none` request skips an off switch that is not the only entry."""
  assert upstream.nearest_effort("none", ["low", "high"]) == "low"
  assert upstream.nearest_effort("none", ["none", "low"]) == "none"


def test_none_only_ladder_still_answers_none() -> None:
  """A list of `none` alone answers `none` at every level."""
  assert upstream.nearest_effort("high", ["none"]) == "none"


def test_a_name_outside_the_ladder_stays_out_of_it() -> None:
  """A list that holds no canonical name maps to nothing."""
  assert upstream.nearest_effort("medium", ["default"]) is None
  assert upstream.nearest_effort("high", []) is None
