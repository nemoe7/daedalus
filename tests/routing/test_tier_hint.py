"""Three-layer tier precedence: provider claim, `_default` hint, provider catch-all."""

from daedalus import config
from daedalus.routing import router


def hinted(tier: str = "TIER-D") -> dict:
  """One `_default` block that claims every `zz*` model."""
  return {"tier": {tier: ["zz*"]}}


def main_config(tier: str = "TIER-D", provider_tier: dict | None = None) -> dict:
  return {
    "_default": hinted(tier),
    "p": {
      "api_key": "k",
      "tier": provider_tier if provider_tier is not None else {"TIER-B": ["*"]},
    },
  }


def test_claiming_tier_ignores_catch_all_when_asked() -> None:
  """`claiming_tier` skips bare catch-all pattern in its no-catch-all pass."""
  block = {"tier": {"TIER-B": ["*"], "TIER-D": ["zz*"]}}
  assert router.claiming_tier(block, "zz1") == "TIER-D", "glob beats catch-all"
  assert router.claiming_tier(block, "zz1", catch_all=False) == "TIER-D"
  tie = {"tier": {"TIER-B": ["zz*"], "TIER-D": ["zz*"]}}
  assert router.claiming_tier(tie, "zz1") == "TIER-B", "tie keeps higher tier"
  assert router.claiming_tier(tie, "zz1", catch_all=False) == "TIER-B"
  broad = {"tier": {"TIER-A": ["^qwe"], "TIER-B": ["*"]}}
  assert router.claiming_tier(broad, "qwe1") == "TIER-B", "regex stays behind"
  assert router.claiming_tier(broad, "qwe1", catch_all=False) is None


def test_default_loses_to_regex_like_catch_all_tiers() -> None:
  """A regex claim stays behind the catch-all tier, as before `_default`."""
  loaded = main_config(provider_tier={"TIER-A": ["^qwe"], "TIER-B": ["*"]})
  assert router.tier_of(loaded, "p", "qwe1") == "TIER-B"
  assert router.tier_of(loaded, "p", "zz1") == "TIER-D"


def test_specific_provider_claim_beats_default() -> None:
  """A specific provider pattern claims before `_default` reads."""
  loaded = main_config(provider_tier={"TIER-B": ["zz1"]})
  assert router.tier_of(loaded, "p", "zz1") == "TIER-B"


def test_default_beats_provider_catch_all() -> None:
  """`_default` claims a model that only the provider catch-all claims."""
  loaded = main_config()
  assert router.tier_of(loaded, "p", "zz1") == "TIER-D"


def test_catch_all_claims_when_default_stays_silent() -> None:
  """The provider catch-all claims a model that `_default` patterns miss."""
  loaded = main_config()
  assert router.tier_of(loaded, "p", "other") == "TIER-B"


def test_default_missing_gives_no_tier() -> None:
  """No provider claim and no `_default` claim leaves the model without tier."""
  loaded = {"p": {"api_key": "k"}}
  assert router.tier_of(loaded, "p", "zz1") is None


def test_default_claim_of_provider_without_tier_key() -> None:
  """`_default` assigns tiers for provider block that holds no tier patterns."""
  loaded = {"_default": hinted(), "p": {"api_key": "k"}}
  assert router.tier_of(loaded, "p", "zz1") == "TIER-D"


def test_own_file_default_applies_to_its_provider() -> None:
  """A provider file's own `_default` claims for provider of that file."""
  loaded = {
    "_default": hinted("TIER-D"),
    "own": {
      config.FILE_KEY: {
        "api_key": "f",
        "_default": hinted("TIER-C"),
      }
    },
  }
  assert router.tier_of(loaded, "own", "zz1") == "TIER-C"


def test_main_default_skips_file_only_provider() -> None:
  """Main-file `_default` never reaches provider that only own file holds."""
  loaded = {
    "_default": hinted("TIER-D"),
    "own": {config.FILE_KEY: {"api_key": "f", "models": {"zz1": {}}}},
  }
  assert router.tier_of(loaded, "own", "zz1") is None


def test_file_block_default_wins_when_file_takes() -> None:
  """A shadowed provider reads its file `_default` when file block owns model."""
  loaded = {
    "_default": hinted("TIER-D"),
    "p": {
      "api_key": "main",
      config.FILE_KEY: {
        "api_key": "file",
        "models": {"zz1": {}},
        "_default": hinted("TIER-C"),
      },
    },
  }
  assert router.tier_of(loaded, "p", "zz1") == "TIER-C"


def test_main_default_wins_when_main_block_owns() -> None:
  """A shadowed provider reads main-file `_default` when main block owns model."""
  loaded = {
    "_default": hinted("TIER-D"),
    "p": {
      "api_key": "main",
      "tier": {"TIER-B": ["*"]},
      config.FILE_KEY: {"api_key": "file", "_default": hinted("TIER-C")},
    },
  }
  assert router.tier_of(loaded, "p", "zz1") == "TIER-D"


def test_candidates_let_default_claim_beat_catch_all() -> None:
  """Tier pools route a catch-all model into its `_default` tier."""
  loaded = main_config()
  assert router.candidates(loaded, "TIER-B", ["p/zz1"]) == []
  assert router.candidates(loaded, "TIER-D", ["p/zz1"]) == ["p/zz1"]
