"""The vendored tier classifier: the prompt rules, the cohort key and the odds."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from daedalus.routing import classifier

CODE = "Write a python function to sort a list."
EXPLAIN = "Explain this code to me."
DESIGN = "Design a database schema for a shop."
MATH = "Solve this equation for x."
WRITE = "Draft an email to my team."
LOOKUP = "Who is the president of France?"
GENERAL = "Hello there, how are you?"


def artifact(path: Path, **payload: object) -> classifier.Artifact:
  path.write_text(json.dumps(payload), encoding="utf-8")
  return classifier.load_artifact(path)


def rows() -> list[dict[str, float]]:
  return [
    {"tier": tier, "successes": 1.0, "observations": 2.0} for tier in classifier.TIERS
  ]


def test_tier_names_cover_every_tier() -> None:
  """Every tier number maps to a config key."""
  assert sorted(classifier.TIER_NAMES) == list(classifier.TIERS)


@pytest.mark.parametrize(
  ("prompt", "kind"),
  [
    (CODE, classifier.RequestType.CODE_GENERATION),
    (EXPLAIN, classifier.RequestType.CODE_UNDERSTANDING),
    (DESIGN, classifier.RequestType.TECHNICAL_DESIGN),
    (MATH, classifier.RequestType.ANALYTICAL_REASONING),
    (WRITE, classifier.RequestType.WRITING),
    (LOOKUP, classifier.RequestType.FACTUAL_LOOKUP),
    (GENERAL, classifier.RequestType.GENERAL),
  ],
)
def test_classify_prompt_names_what_the_prompt_asks_for(
  prompt: str, kind: classifier.RequestType
) -> None:
  """One prompt per request type, and a greeting falls through to general."""
  assert classifier.classify_prompt(prompt) == kind


@pytest.mark.parametrize("prompt", ["", "   ", "\n\t"])
def test_classify_prompt_calls_a_blank_prompt_general(prompt: str) -> None:
  """No words means nothing to classify."""
  assert classifier.classify_prompt(prompt) is classifier.RequestType.GENERAL


def test_classify_prompt_reads_only_the_first_2000_characters() -> None:
  """A rule that starts past the cut does not fire."""
  hidden = "x" * 1999 + " Who is the president of France?"
  assert classifier.classify_prompt(hidden) is not classifier.RequestType.FACTUAL_LOOKUP
  assert classifier.classify_prompt("Who is the president of France?") is (
    classifier.RequestType.FACTUAL_LOOKUP
  )


@pytest.mark.parametrize(
  ("length", "bucket"),
  [
    (199, "short"),
    (200, "medium"),
    (799, "medium"),
    (800, "long"),
    (1999, "long"),
    (2000, "very_long"),
  ],
)
def test_similarity_cohort_buckets_the_length(length: int, bucket: str) -> None:
  """The bucket changes at 200, 800 and 2000 characters."""
  prompt = "a" * length
  assert classifier.similarity_cohort(prompt, classifier.RequestType.GENERAL) == (
    f"general|{bucket}|code=0|math=0|mc=0|intl=0"
  )


def test_similarity_cohort_flags_a_prompt_that_holds_code() -> None:
  """A fenced block or the word `def` sets the code flag."""
  for prompt in ("def foo():", "```\nprint(1)\n```"):
    assert "|code=1|" in classifier.similarity_cohort(
      prompt, classifier.RequestType.GENERAL
    )


def test_similarity_cohort_flags_a_prompt_that_holds_math() -> None:
  """A math word, or a `$` or `=` sign, sets the math flag."""
  for prompt in ("What is the probability of rain?", "What is 2 = 2?"):
    assert "|math=1|" in classifier.similarity_cohort(
      prompt, classifier.RequestType.GENERAL
    )


def test_similarity_cohort_flags_a_multiple_choice_prompt() -> None:
  """An option marker such as `A.` sets the choice flag."""
  assert "|mc=1|" in classifier.similarity_cohort(
    "A. Paris", classifier.RequestType.GENERAL
  )


def test_similarity_cohort_flags_a_prompt_that_is_mostly_not_ascii() -> None:
  """Over a tenth of the characters above 127 sets the international flag."""
  assert classifier.similarity_cohort(
    "αααααα", classifier.RequestType.GENERAL
  ).endswith("intl=1")
  assert classifier.similarity_cohort(
    "hello world α", classifier.RequestType.GENERAL
  ).endswith("intl=0")


def test_the_vendored_artifact_loads() -> None:
  """The artifact beside the module holds all four tiers."""
  assert sorted(classifier.load_artifact().global_stats) == list(classifier.TIERS)


def test_load_artifact_uses_the_documented_defaults(tmp_path: Path) -> None:
  """A payload that names no priors gets the documented ones."""
  found = artifact(tmp_path / "tiers.json", global_statistics=rows())
  assert found.domain_prior_mass == 200.0
  assert found.cohort_prior_mass == 20.0
  assert found.routing_threshold == 0.75


def test_load_artifact_needs_statistics_for_all_four_tiers(tmp_path: Path) -> None:
  """A table with one tier is a broken table, and fails loudly."""
  with pytest.raises(ValueError, match="global statistics for tiers 1 to 4"):
    artifact(tmp_path / "tiers.json", global_statistics=rows()[:1])


def test_load_artifact_tolerates_a_payload_with_no_domain_rows(tmp_path: Path) -> None:
  """No domain or cohort rows means no pull, not an error."""
  found = artifact(tmp_path / "tiers.json", global_statistics=rows())
  assert found.domain_stats == {}
  assert found.cohort_stats == {}


def test_posterior_mean_pulls_a_row_toward_the_tier_above() -> None:
  """No row gives the prior back. A row blends into it by its own mass."""
  assert classifier._posterior_mean(None, 200.0, 0.5) == 0.5
  stat = classifier.TierStatistic(1, successes=1.0, observations=1.0)
  assert classifier._posterior_mean(stat, 200.0, 0.5) == pytest.approx(101.0 / 201.0)


def test_signal_reports_the_heuristics_read() -> None:
  """The signal names the tier, the odds per tier, the type and the cohort."""
  found = classifier.signal(CODE)
  assert found["tier_name"] == classifier.TIER_NAMES[found["required_tier"]]
  assert found["request_type"] == classifier.RequestType.CODE_GENERATION.value
  assert found["cohort"].startswith("code_generation|short|")
  assert set(found["probabilities"]) == set(classifier.TIER_NAMES.values())
  assert all(0 <= value <= 1 for value in found["probabilities"].values())


def test_predict_gives_odds_that_never_fall() -> None:
  """The odds per tier are monotonic, so a higher tier is never worse."""
  found = classifier.predict(CODE, classifier.load_artifact())
  odds = [found.probabilities[tier] for tier in classifier.TIERS]
  assert len(odds) == len(classifier.TIERS)
  assert odds == sorted(odds)


def test_predict_names_a_tier_in_the_config_map() -> None:
  """The tier it picks is one the config names."""
  found = classifier.predict(LOOKUP, classifier.load_artifact())
  assert found.required_tier in classifier.TIERS
  assert found.tier_name == classifier.TIER_NAMES[found.required_tier]


def test_predict_falls_back_to_the_top_tier_when_nothing_clears() -> None:
  """A threshold no tier reaches gives the strongest tier."""
  hard = replace(classifier.load_artifact(), routing_threshold=1.01)
  assert classifier.predict(GENERAL, hard).required_tier == classifier.TIERS[-1]


def test_predict_uses_the_request_type_it_is_given(tmp_path: Path) -> None:
  """A given type picks its own domain row, and the rules never run."""
  found = artifact(
    tmp_path / "tiers.json",
    global_statistics=rows(),
    domain_statistics=[
      {
        "request_type": "code_generation",
        "tier": 1,
        "successes": 1000.0,
        "observations": 1000.0,
      },
      {"request_type": "general", "tier": 1, "successes": 0.0, "observations": 1000.0},
    ],
    routing_threshold=0.9,
  )
  assert (
    classifier.predict(
      "hi", found, classifier.RequestType.CODE_GENERATION
    ).required_tier
    == 1
  )
  assert classifier.predict("hi", found).required_tier == classifier.TIERS[-1]
