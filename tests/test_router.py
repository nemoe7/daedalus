"""Runnable check for the vendored tier router. Run: python tests/test_router.py"""

import json
import sys
import tempfile
from pathlib import Path

from daedalus import router


def check_classify() -> None:
  """The ported rules name what a prompt asks for."""
  cases = {
    "write a python function to parse a csv file": router.RequestType.CODE_GENERATION,
    "explain what this function does": router.RequestType.CODE_UNDERSTANDING,
    "how should i structure the database schema": router.RequestType.TECHNICAL_DESIGN,
    "solve the integral equation": router.RequestType.ANALYTICAL_REASONING,
    "write a short email to the team": router.RequestType.WRITING,
    "what is the capital of france": router.RequestType.FACTUAL_LOOKUP,
    "hi": router.RequestType.GENERAL,
    "   ": router.RequestType.GENERAL,
    "": router.RequestType.GENERAL,
  }
  for prompt, expected in cases.items():
    assert router.classify_prompt(prompt) is expected, prompt


def check_cohort() -> None:
  """The cohort key is the request type plus five shape flags."""
  assert router.similarity_cohort("hi", router.RequestType.GENERAL) == (
    "general|short|code=0|math=0|mc=0|intl=0"
  )
  shape = router.similarity_cohort(
    "def solve():\n  return 1 = 2",
    router.RequestType.CODE_GENERATION,
  )
  assert shape == "code_generation|short|code=1|math=1|mc=0|intl=0", shape
  long_prompt = "x" * 2500
  assert router.similarity_cohort(long_prompt, router.RequestType.GENERAL).startswith(
    "general|very_long|"
  )


def check_artifact() -> None:
  """The vendored table holds every tier, once, with its calibration."""
  artifact = router.load_artifact()
  assert sorted(artifact.global_stats) == [1, 2, 3, 4]
  assert len(artifact.domain_stats) == 28, len(artifact.domain_stats)
  assert len(artifact.cohort_stats) == 643, len(artifact.cohort_stats)
  assert artifact.routing_threshold == 0.75
  assert artifact.domain_prior_mass == 200.0
  assert artifact.cohort_prior_mass == 20.0
  assert artifact.global_stats[4].observations > 0


def check_artifact_rejects_a_broken_table() -> None:
  """A table that loses a tier fails at load, not at route time."""
  payload = json.loads(router.ARTIFACT_PATH.read_text(encoding="utf-8"))
  payload["global_statistics"] = [
    row for row in payload["global_statistics"] if row["tier"] != 3
  ]
  with tempfile.TemporaryDirectory() as folder:
    broken = Path(folder) / "broken.json"
    broken.write_text(json.dumps(payload), encoding="utf-8")
    try:
      router.load_artifact(broken)
    except ValueError as error:
      assert "tiers 1 to 4" in str(error), error
    else:
      msg = "a table missing tier 3 loaded without a complaint"
      raise AssertionError(msg)


def check_predict() -> None:
  """Odds rise with the tier, and the first tier over the bar wins."""
  artifact = router.load_artifact()
  prompts = [
    "hi",
    "what is the capital of france",
    "write a python function to parse a csv file",
    "compare the tradeoffs between kafka and redis for a queue",
  ]
  for prompt in prompts:
    prediction = router.predict(prompt, artifact)
    values = [prediction.probabilities[tier] for tier in router.TIERS]
    assert values == sorted(values), prompt
    expected = next(
      (tier for tier in router.TIERS if prediction.probabilities[tier] >= 0.75),
      4,
    )
    assert prediction.required_tier == expected, prompt
    assert prediction.tier_name == router.TIER_NAMES[prediction.required_tier]

  # Pinned against the vendored artifact, so a swap shows up here.
  assert router.predict("hi", artifact).tier_name == "TIER-D"
  simple = router.predict("hi", artifact).probabilities[1]
  assert round(simple, 4) == 0.9011, simple
  coding = router.predict("write a python function to parse a csv file", artifact)
  assert coding.tier_name == "TIER-C", coding.tier_name
  assert round(coding.probabilities[1], 4) == 0.7397, coding.probabilities[1]
  assert round(coding.probabilities[2], 4) == 0.8645, coding.probabilities[2]


def check_tier_names() -> None:
  """Tier 1 is the weakest, and the config letters run the other way."""
  assert router.TIER_NAMES == {1: "TIER-D", 2: "TIER-C", 3: "TIER-B", 4: "TIER-A"}
  assert sorted(router.TIER_NAMES.values()) != [
    router.TIER_NAMES[tier] for tier in router.TIERS
  ]


def check_tier_models() -> None:
  """A provider block answers with the models it lists under a tier."""
  provider = {"tier": {"TIER-A": ["glm-5.3", None], "TIER-C": []}}
  assert router.tier_models(provider, "TIER-A") == ["glm-5.3"]
  assert router.tier_models(provider, "TIER-C") == []
  assert router.tier_models(provider, "TIER-B") == []
  assert router.tier_models({}, "TIER-A") == []


def check_route() -> None:
  """A tier resolves to the provider rows that claim it, and escalates when empty."""
  config = {
    "gemini": {"tier": {"TIER-C": ["gemini-3.5-flash"]}},
    "openrouter": {"tier": {"TIER-C": ["*:free"], "TIER-A": ["anthropic/*"]}},
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
  assert router.route("hi", config, lines) == "gemini/gemini-3.5-flash"
  assert router.route("hi", {}, lines) is None
  assert router.route("hi", config, []) is None


def main() -> int:
  """Run every check."""
  check_classify()
  check_cohort()
  check_artifact()
  check_artifact_rejects_a_broken_table()
  check_predict()
  check_tier_names()
  check_tier_models()
  check_route()
  print("ok: router checks passed")
  return 0


if __name__ == "__main__":
  sys.exit(main())
