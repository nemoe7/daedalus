"""Tests for the vendored tier router."""

import asyncio
import json
import tempfile
from pathlib import Path

import httpx

from daedalus import config
from daedalus.routing import classifier, router
from daedalus.server import upstream


def test_classify() -> None:
  """The ported rules name what a prompt asks for."""
  cases = {
    "write a python function to parse a csv file": classifier.RequestType.CODE_GENERATION,
    "explain what this function does": classifier.RequestType.CODE_UNDERSTANDING,
    "how should i structure the database schema": classifier.RequestType.TECHNICAL_DESIGN,
    "solve the integral equation": classifier.RequestType.ANALYTICAL_REASONING,
    "write a short email to the team": classifier.RequestType.WRITING,
    "what is the capital of france": classifier.RequestType.FACTUAL_LOOKUP,
    "hi": classifier.RequestType.GENERAL,
    "   ": classifier.RequestType.GENERAL,
    "": classifier.RequestType.GENERAL,
  }
  for prompt, expected in cases.items():
    assert classifier.classify_prompt(prompt) is expected, prompt


def test_cohort() -> None:
  """The cohort key is the request type plus five shape flags."""
  assert classifier.similarity_cohort("hi", classifier.RequestType.GENERAL) == (
    "general|short|code=0|math=0|mc=0|intl=0"
  )
  shape = classifier.similarity_cohort(
    "def solve():\n  return 1 = 2",
    classifier.RequestType.CODE_GENERATION,
  )
  assert shape == "code_generation|short|code=1|math=1|mc=0|intl=0", shape
  long_prompt = "x" * 2500
  assert classifier.similarity_cohort(
    long_prompt, classifier.RequestType.GENERAL
  ).startswith("general|very_long|")


def test_artifact() -> None:
  """The vendored table holds every tier, once, with its calibration."""
  artifact = classifier.load_artifact()
  assert sorted(artifact.global_stats) == [1, 2, 3, 4]
  assert len(artifact.domain_stats) == 28, len(artifact.domain_stats)
  assert len(artifact.cohort_stats) == 643, len(artifact.cohort_stats)
  assert artifact.routing_threshold == 0.75
  assert artifact.domain_prior_mass == 200.0
  assert artifact.cohort_prior_mass == 20.0
  assert artifact.global_stats[4].observations > 0


def test_artifact_rejects_a_broken_table() -> None:
  """A table that loses a tier fails at load, not at route time."""
  payload = json.loads(classifier.ARTIFACT_PATH.read_text(encoding="utf-8"))
  payload["global_statistics"] = [
    row for row in payload["global_statistics"] if row["tier"] != 3
  ]
  with tempfile.TemporaryDirectory() as folder:
    broken = Path(folder) / "broken.json"
    broken.write_text(json.dumps(payload), encoding="utf-8")
    try:
      classifier.load_artifact(broken)
    except ValueError as error:
      assert "tiers 1 to 4" in str(error), error
    else:
      msg = "a table missing tier 3 loaded without a complaint"
      raise AssertionError(msg)


def test_predict() -> None:
  """Odds rise with the tier, and the first tier over the bar wins."""
  artifact = classifier.load_artifact()
  prompts = [
    "hi",
    "what is the capital of france",
    "write a python function to parse a csv file",
    "compare the tradeoffs between kafka and redis for a queue",
  ]
  for prompt in prompts:
    prediction = classifier.predict(prompt, artifact)
    values = [prediction.probabilities[tier] for tier in router.TIERS]
    assert values == sorted(values), prompt
    expected = next(
      (tier for tier in router.TIERS if prediction.probabilities[tier] >= 0.75),
      4,
    )
    assert prediction.required_tier == expected, prompt
    assert prediction.tier_name == router.TIER_NAMES[prediction.required_tier]

  # Pinned against the vendored artifact, so a swap shows up here.
  assert classifier.predict("hi", artifact).tier_name == "TIER-D"
  simple = classifier.predict("hi", artifact).probabilities[1]
  assert round(simple, 4) == 0.9011, simple
  coding = classifier.predict("write a python function to parse a csv file", artifact)
  assert coding.tier_name == "TIER-C", coding.tier_name
  assert round(coding.probabilities[1], 4) == 0.7397, coding.probabilities[1]
  assert round(coding.probabilities[2], 4) == 0.8645, coding.probabilities[2]


def test_threshold_moves_the_bar() -> None:
  """The `routing.threshold` setting moves the tier of a prompt, cached or not."""
  assert router.required_tier("hi") == 1, "the shipped bar"
  try:
    router.set_threshold(0.93)
    assert router.required_tier("hi") == 2, "a higher bar, on a cached prompt"
  finally:
    router.set_threshold(None)
  assert router.required_tier("hi") == 1, "the artifact bar returns"


def test_tier_names() -> None:
  """Tier 1 is the weakest, and the config letters run the other way."""
  assert router.TIER_NAMES == {1: "TIER-D", 2: "TIER-C", 3: "TIER-B", 4: "TIER-A"}
  assert sorted(router.TIER_NAMES.values()) != [
    router.TIER_NAMES[tier] for tier in router.TIERS
  ]


def test_tier_models() -> None:
  """A provider block answers with the models it lists under a tier."""
  provider = {"api_key": "k", "tier": {"TIER-A": ["glm-5.3", None], "TIER-C": []}}
  assert router.tier_models(provider, "TIER-A") == ["glm-5.3"]
  assert router.tier_models(provider, "TIER-C") == []
  assert router.tier_models(provider, "TIER-B") == []
  assert router.tier_models({}, "TIER-A") == []


def chain(config: dict, lines: list[str], tier: int) -> list[str]:
  """Every row of a tier chain, in the order that the proxy tries them."""
  groups = router.chain_groups(config, lines, router.fallback_order(tier))
  return [line for group in groups for line in group]


def test_route() -> None:
  """A tier resolves to the provider rows that claim it, and escalates when empty."""
  config = {
    "gemini": {"api_key": "k", "tier": {"TIER-C": ["gemini-3.5-flash"]}},
    "openrouter": {
      "api_key": "k",
      "tier": {"TIER-C": ["*:free"], "TIER-A": ["anthropic/*"]},
    },
  }
  lines = [
    "gemini/gemini-3.5-flash",
    "openrouter/google/gemini-3.5-flash:free",
    "openrouter/anthropic/claude-opus-5",
  ]
  assert router.candidates(config, "TIER-C", lines) == [
    "gemini/gemini-3.5-flash",
    "openrouter/google/gemini-3.5-flash:free",
  ]
  assert router.candidates(config, "TIER-B", lines) == []
  # "hi" needs TIER-D, which no block declares, so the next stronger tier answers.
  tier = router.required_tier("hi")
  assert tier == 1, tier
  assert chain(config, lines, tier) == [
    "gemini/gemini-3.5-flash",
    "openrouter/google/gemini-3.5-flash:free",
    "openrouter/anthropic/claude-opus-5",
  ]
  assert chain({}, lines, tier) == []
  assert chain(config, [], tier) == []


def test_direct_only() -> None:
  """A model with `pool: false` stays out of each tier, and the last matching entry wins."""
  models = {
    "*": {"pool": False},
    "z-ai/*": {"reasoning_effort": "low"},
    "a/keep": {"pool": True},
  }
  config = {"openrouter": {"api_key": "k", "tier": {"TIER-A": ["*"]}, "models": models}}
  lines = ["openrouter/z-ai/glm", "openrouter/a/keep"]
  assert router.candidates(config, "TIER-A", lines) == ["openrouter/a/keep"]
  assert router.claiming_tier(config["openrouter"], "z-ai/glm") == "TIER-A"
  assert router.pooled(config, "openrouter/z-ai/glm") is False, "pool: false"
  assert router.pooled(config, "openrouter/a/keep") is True, "only false turns it off"
  keyless = {"openrouter": {**config["openrouter"], "api_key": ""}}
  assert router.keyed(keyless, "openrouter/a/keep") is False, "an empty key"
  assert router.pooled(keyless, "openrouter/a/keep") is False, "no key, no pools"


def test_headroom_allowed() -> None:
  """The Headroom compression turns off at 4 levels, and the narrow level wins."""
  plain = {"p": {"api_key": "k"}}
  assert router.headroom_allowed(plain, "p/x") is True, "on by default"
  router.set_headroom(False)
  assert router.headroom_allowed(plain, "p/x") is False, "the settings switch"
  router.set_headroom(True)
  block = {
    "p": {"api_key": "k", "headroom": False, "models": {"x": {"headroom": True}}}
  }
  assert router.headroom_allowed(block, "p/x") is True, "the model entry wins"
  assert router.headroom_allowed(block, "p/y") is False, "the provider block"
  file_only = {"p": {"api_key": "k", config.FILE_KEY: {"headroom": False}}}
  assert router.headroom_allowed(file_only, "p/x") is False, "the file block"
  both = {
    "p": {
      "api_key": "k",
      "headroom": True,
      config.FILE_KEY: {"api_key": "k", "headroom": False},
    }
  }
  assert router.headroom_allowed(both, "p/x") is False, "the file block beats the block"
  router.set_headroom(False)
  assert router.headroom_allowed(both, "p/x") is False, "only a true block returns true"
  assert (
    router.headroom_allowed({"p": {"api_key": "k", "headroom": True}}, "p/x") is True
  )
  router.set_headroom(True)


def test_provider_files() -> None:
  """A {provider}.yml takes its own models, and its values win over the main file."""
  main = {
    "api_key": "k",
    "tier": {"TIER-B": ["*"]},
    "models": {"*": {"reasoning_effort": "low", "timeout": 60}},
  }
  file = {
    "api_key": "k",
    "tier": {"TIER-A": ["z-ai/*"]},
    "models": {"z-ai/glm": {"reasoning_effort": "high", "pool": False}},
  }
  blocks = {"openrouter": {**main, config.FILE_KEY: file}}
  lines = ["openrouter/z-ai/glm", "openrouter/other/model"]
  assert router.candidates(blocks, "TIER-A", lines) == [], (
    "the file takes it, pool false"
  )
  assert router.candidates(blocks, "TIER-B", lines) == ["openrouter/other/model"]
  where = router.model_setting(blocks, "openrouter/z-ai/glm", "reasoning_effort")
  assert where == "high", where
  other = router.model_setting(blocks, "openrouter/other/model", "reasoning_effort")
  assert other == "low", other
  assert router.model_wait(blocks, "openrouter/z-ai/glm", 5.0) == 5.0, (
    "the main file does not apply"
  )
  file["models"]["z-ai/glm"]["timeout"] = 15
  assert router.model_wait(blocks, "openrouter/z-ai/glm", 5.0) == 15.0, "the file wins"
  assert router.block_for(blocks, "openrouter", "z-ai/glm") is file
  assert router.block_for(blocks, "openrouter", "other/model") == main
  assert router.block_for({}, "openrouter", "z-ai/glm") is None
  file["models"]["*"] = {}
  blocks = {**blocks}
  assert router.candidates(blocks, "TIER-B", lines) == [], "a * key takes each model"


def test_tier_cache() -> None:
  """The tier rows come from a cache until the config object or the lines change."""
  blocks = {"p": {"api_key": "k", "tier": {"TIER-A": ["a*"], "TIER-D": ["d*"]}}}
  lines = ["p/a1", "p/d1"]
  assert router.candidates(blocks, "TIER-A", lines) == ["p/a1"]
  found = router.candidates(blocks, "TIER-A", lines)
  found.append("x")
  assert router.candidates(blocks, "TIER-A", lines) == ["p/a1"], "a copy"
  assert router.candidates(blocks, "TIER-A", [*lines, "p/a2"]) == ["p/a1", "p/a2"]
  changed = {"p": {**blocks["p"], "tier": {"TIER-A": ["d*"]}}}
  assert router.candidates(changed, "TIER-A", lines) == ["p/d1"], "a new config"


def test_model_wait() -> None:
  """The `timeout` of a model replaces the wait, and the upstream request carries it."""
  models = {"*": {"timeout": 15}, "slow": {"timeout": True}, "off": {"timeout": 0}}
  config = {"p": {"api_key": "k", "api_base": "https://p.test/v1", "models": models}}
  assert router.model_wait(config, "p/fast", 60.0) == 15.0
  assert router.model_wait(config, "p/slow", 60.0) == 60.0, "a bool is not seconds"
  assert router.model_wait(config, "p/off", 60.0) == 60.0
  assert router.model_wait(config, "q/other", 60.0) == 60.0
  seen: list[dict] = []

  def answer(request: httpx.Request) -> httpx.Response:
    seen.append(request.extensions["timeout"])
    return httpx.Response(200, json={"choices": []})

  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  try:
    body = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    for model in ("p/fast", "p/off"):
      _, response = asyncio.run(upstream.attempt(model, body, config))
      asyncio.run(response.aclose())
  finally:
    upstream.set_client(None)
  assert [t["read"] for t in seen] == [15.0, upstream.WAIT_SECONDS], seen


def test_most_specific_tier() -> None:
  """A slug that 2 tiers claim goes only to the tier with the most specific pattern."""
  config = {
    "kilo": {
      "api_key": "k",
      "tier": {
        "TIER-A": ["nvidia/nemotron-3-ultra-*", "^qwen/"],
        "TIER-B": ["*", "qwen/qwen3-32b"],
        "TIER-C": ["nvidia/*"],
        "TIER-D": ["*:free"],
      },
    },
  }
  lines = [
    "kilo/nvidia/nemotron-3-ultra-550b:free",
    "kilo/nvidia/nemotron-3-nano:free",
    "kilo/qwen/qwen3-32b",
    "kilo/qwen/qwen3-8b",
    "kilo/liquid/lfm:free",
    "kilo/other/model",
  ]
  # A glob beats a regex, and an exact name beats a glob.
  assert router.candidates(config, "TIER-A", lines) == [
    "kilo/nvidia/nemotron-3-ultra-550b:free"
  ]
  assert router.candidates(config, "TIER-B", lines) == [
    "kilo/qwen/qwen3-32b",
    "kilo/qwen/qwen3-8b",
    "kilo/other/model",
  ]
  # Between 2 globs, the one with more literal characters wins.
  assert router.candidates(config, "TIER-C", lines) == [
    "kilo/nvidia/nemotron-3-nano:free"
  ]
  assert router.candidates(config, "TIER-D", lines) == ["kilo/liquid/lfm:free"]
  # Equal patterns go to the higher tier.
  tie = {"groq": {"api_key": "k", "tier": {"TIER-A": ["gpt-*"], "TIER-B": ["gpt-*"]}}}
  assert router.candidates(tie, "TIER-A", ["groq/gpt-oss"]) == ["groq/gpt-oss"]
  assert router.candidates(tie, "TIER-B", ["groq/gpt-oss"]) == []


def test_pools() -> None:
  """Four pools, and the fallback chain each one walks."""
  assert router.POOLS == {
    "daedalus/moros": 1,
    "daedalus/koinos": 2,
    "daedalus/deinos": 3,
    "daedalus/sophos": 4,
  }
  chains = {1: (1, 2, 3, 4), 2: (2, 3, 4, 1), 3: (3, 4, 2, 1), 4: (4, 3, 2, 1)}
  for tier, expected in chains.items():
    assert router.fallback_order(tier) == expected, tier

  config = {
    "gemini": {"api_key": "k", "tier": {"TIER-B": ["gemini-3.5-flash"]}},
    "openrouter": {"api_key": "k", "tier": {"TIER-A": ["anthropic/*"]}},
  }
  lines = ["gemini/gemini-3.5-flash", "openrouter/anthropic/claude-opus-5"]
  # sophos is tier 4, so TIER-A leads and TIER-B follows.
  assert chain(config, lines, router.POOLS["daedalus/sophos"]) == [
    "openrouter/anthropic/claude-opus-5",
    "gemini/gemini-3.5-flash",
  ]
  # moros is tier 1, so the chain promotes to TIER-B before it reaches TIER-A.
  assert chain(config, lines, router.POOLS["daedalus/moros"]) == [
    "gemini/gemini-3.5-flash",
    "openrouter/anthropic/claude-opus-5",
  ]
  # koinos is tier 2, which is empty, so the chain promotes to TIER-B.
  assert chain(config, lines, router.POOLS["daedalus/koinos"]) == [
    "gemini/gemini-3.5-flash",
    "openrouter/anthropic/claude-opus-5",
  ]


def test_the_catalog_efforts_sit_under_the_config_keys() -> None:
  """A catalog list fills the gap of a missing config key, and the config key keeps the last word."""
  config = {
    "kilo": {"models": {"a": {"supported_reasoning_efforts": ["none", "high"]}}},
    "openrouter": {},
    "mistral": {},
  }
  listed = ["max", "high", "low"]
  assert router.efforts(config, "openrouter/x", listed) == listed
  assert router.efforts(config, "mistral/m", listed) == listed, "over the coded default"
  assert router.efforts(config, "kilo/a", listed) == ["none", "high"], (
    "the config key wins"
  )
  assert router.efforts(config, "kilo/b", listed) == listed, "the block holds no key"
  assert router.efforts(config, "openrouter/x") == router.efforts(
    config, "openrouter/x", None
  ), "no catalog row keeps the coded set"
  for bad in ([], ["high", 2], "high", None):
    assert router.efforts(config, "openrouter/x", bad) == router.efforts(
      config, "openrouter/x"
    ), bad


def test_the_efforts_key_follows_the_block_hierarchy() -> None:
  """The supported_reasoning_efforts key reads from the model entry, then the block, then the code."""
  config = {
    "kilo": {
      "supported_reasoning_efforts": ["none", "low", "high"],
      "models": {"a": {"supported_reasoning_efforts": ["none", "medium"]}},
    },
    "mistral": {},
  }
  assert router.efforts(config, "kilo/a") == ["none", "medium"]
  assert router.efforts(config, "kilo/b") == ["none", "low", "high"]
  assert router.efforts(config, "mistral/m") == ["none", "high"], (
    "a coded default per provider"
  )
  assert router.efforts(config, "openrouter/x") == [
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
  ]
  assert router.efforts(config, "z-ai/glm-5.3") == ["low", "high", "max"], (
    "the levels that GLM-5.2 and GLM-5.3 both take"
  )
  assert router.efforts(config, "groq/openai/gpt-oss-120b") == [
    "low",
    "medium",
    "high",
  ], "the gpt-oss models of the block refuse none"
  assert router.efforts(config, "cloudflare/@cf/qwen/qwen3.8-27b") == [
    "low",
    "medium",
  ], "the reasoning models of the block agree on these 2 levels"
  assert router.efforts(config, "cloudflare/@cf/a/b") == ["low", "medium"]
  assert router.efforts(config, "unknown/x") == ["none", "low", "medium", "high"]


def test_the_shipped_config_names_the_documented_efforts() -> None:
  """The shipped provider files name the effort ladder each model takes, per its docs."""
  found = config.load_config(Path("config/providers/free.yml"))
  documented = {
    "cloudflare/@cf/openai/gpt-oss-120b": ["low", "medium", "high"],
    "cloudflare/@cf/openai/gpt-oss-20b": ["low", "medium", "high"],
    "cloudflare/@cf/qwen/qwen3.8-27b": ["low", "medium", "xhigh"],
    "gemini/gemini-3.8-flash": ["low", "medium", "high"],
    "gemini/gemini-3.6-flash": ["minimal", "low", "medium", "high"],
    "gemini/gemini-3.5-flash-lite": ["minimal", "low", "medium", "high"],
    "gemini/gemini-3.1-flash-lite": ["minimal", "low", "medium", "high"],
    "gemini/gemini-3.1-flash-lite-preview": ["minimal", "low", "medium", "high"],
    "gemini/gemma-4-26b-a4b-it": ["minimal", "low", "medium", "high"],
    "gemini/gemma-4-31b-it": ["minimal", "low", "medium", "high"],
  }
  for model, ladder in documented.items():
    assert router.efforts(found, model, None) == ladder, model


def test_the_effort_ladder_caps_at_five_and_at_the_list() -> None:
  """Ladder step 0 is the lowest effort, and a step caps at 5 and at the list end."""
  efforts = ["none", "low", "medium", "high", "xhigh"]
  assert router.effort_at(efforts, 0) == "none"
  assert router.effort_at(efforts, 1) == "low"
  assert router.effort_at(efforts, 4) == "xhigh"
  assert router.effort_at(efforts, 7) == "xhigh"
  assert router.effort_at(["low", "medium"], 0) == "low", "no none starts at the lowest"
  assert router.effort_at(["low", "medium"], 5) == "medium"
