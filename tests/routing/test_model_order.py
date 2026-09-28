"""Tests for the `order` key of the provider blocks and the models."""

from daedalus.routing import router

CONFIG = {
  "cf": {"api_key": "k", "order": 2, "models": {"big*": {"order": 1}}},
  "groq": {"api_key": "k", "models": {"slow": {"order": 3}, "bad": {"order": 0}}},
  "odd": {"api_key": "k", "order": "2"},
}


def test_model_order() -> None:
  """A models entry wins over the block, and a missing or wrong value is 1."""
  assert router.model_order(CONFIG, "cf/small") == 2, "the block value"
  assert router.model_order(CONFIG, "cf/big-1") == 1, "the model value wins"
  assert router.model_order(CONFIG, "groq/slow") == 3
  assert router.model_order(CONFIG, "groq/fast") == 1, "no order"
  assert router.model_order(CONFIG, "groq/bad") == 1, "0 is not an order"
  assert router.model_order(CONFIG, "odd/x") == 1, "a string is not an order"
  assert router.model_order(CONFIG, "nobody/x") == 1, "no block"


def test_by_order() -> None:
  """Each tier splits into its orders, lowest first, and keeps the model order in each part."""
  groups = [["cf/small", "groq/fast", "groq/slow", "cf/big-1"], [], ["cf/other"]]
  assert router.by_order(CONFIG, groups) == [
    ["groq/fast", "cf/big-1"],
    ["cf/small"],
    ["groq/slow"],
    ["cf/other"],
  ]


def test_new_config() -> None:
  """A new config object clears the cached orders."""
  assert router.by_order(CONFIG, [["cf/small", "groq/fast"]]) == [
    ["groq/fast"],
    ["cf/small"],
  ]
  changed = {**CONFIG, "cf": {"api_key": "k"}}
  assert router.by_order(changed, [["cf/small", "groq/fast"]]) == [
    ["cf/small", "groq/fast"]
  ]
