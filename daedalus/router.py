"""Pick a tier for a request with the vendored litellm heuristic v2."""

# Vendored from litellm 1.102.1, MIT licence: classify_prompt, TierSuccessPredictor,
# and the bundled ultrafeedback_tiers.json artifact.

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from re import Pattern
from typing import Any, Final

from daedalus.catalog import any_match

ARTIFACT_PATH: Final = (
  Path(__file__).with_name("artifacts") / "ultrafeedback_tiers.json"
)
# The one model name the proxy resolves itself. Every other name goes upstream as written.
RESERVED_MODEL: Final = "daedalus/auto"
# Four pools, each a gateway to one tier. ADR 1.
POOLS: Final[Mapping[str, int]] = {
  "daedalus/moros": 1,
  "daedalus/koinos": 2,
  "daedalus/deinos": 3,
  "daedalus/sophos": 4,
}
# The pool for tool calls. It has no tier and holds TIER-A, then TIER-B members. ADR 3.
PRAKTOS: Final = "daedalus/praktos"
PRAKTOS_TIERS: Final = (4, 3)
TIERS: Final = (1, 2, 3, 4)
# litellm numbers its tiers 1 to 4 as SIMPLE, MEDIUM, COMPLEX, REASONING, and the config
# names them TIER-D to TIER-A, so the map is explicit. Sorting the names inverts it.
TIER_NAMES: Final[Mapping[int, str]] = {
  1: "TIER-D",
  2: "TIER-C",
  3: "TIER-B",
  4: "TIER-A",
}


class RequestType(str, Enum):
  """What a prompt asks for. The artifact keys its rows by these seven values."""

  CODE_GENERATION = "code_generation"
  CODE_UNDERSTANDING = "code_understanding"
  TECHNICAL_DESIGN = "technical_design"
  ANALYTICAL_REASONING = "analytical_reasoning"
  WRITING = "writing"
  FACTUAL_LOOKUP = "factual_lookup"
  GENERAL = "general"


_RULES: Final[list[tuple[Pattern[str], RequestType]]] = [
  (
    re.compile(
      r"\b(write|create|generate|implement|build)\s+(?:a |an |the |me )?"
      r"(?:python|javascript|typescript|java|rust|go|c\+\+|sql|bash|shell)\b",
      re.IGNORECASE,
    ),
    RequestType.CODE_GENERATION,
  ),
  (
    re.compile(
      r"\b(write|create|implement|build)\b(?:\s+\w+){0,4}?\s+"
      r"(function|class|method|script|program|api|endpoint|microservice)\b",
      re.IGNORECASE,
    ),
    RequestType.CODE_GENERATION,
  ),
  (
    re.compile(
      r"\b(explain|describe|understand|walk me through|what does)\b.*"
      r"\b(code|function|method|class|algorithm|snippet)\b",
      re.IGNORECASE,
    ),
    RequestType.CODE_UNDERSTANDING,
  ),
  (
    re.compile(
      r"\b(debug|fix|why (?:is|does|isn't)|what.s wrong|trace)\b.*"
      r"\b(error|bug|exception|stacktrace|stack trace|traceback)\b",
      re.IGNORECASE,
    ),
    RequestType.CODE_UNDERSTANDING,
  ),
  (
    re.compile(
      r"\b(review|critique)\s+(?:this |my |the )?(?:code|pr|pull request|diff|patch)\b",
      re.IGNORECASE,
    ),
    RequestType.CODE_UNDERSTANDING,
  ),
  (
    re.compile(
      r"\b(design|architect|plan|architecture)\b.*"
      r"\b(system|service|api|database|schema|module|microservice)\b",
      re.IGNORECASE,
    ),
    RequestType.TECHNICAL_DESIGN,
  ),
  (
    re.compile(
      r"\b(should i (?:use|choose|pick)|tradeoffs? between|compare)\b.*"
      r"\b(library|framework|language|database|protocol|postgres|postgresql|mongodb|"
      r"dynamodb|mysql|redis|kafka|sql|nosql)\b",
      re.IGNORECASE,
    ),
    RequestType.TECHNICAL_DESIGN,
  ),
  (
    re.compile(
      r"\bhow (?:should|do) i (?:design|structure|organize|model)\b", re.IGNORECASE
    ),
    RequestType.TECHNICAL_DESIGN,
  ),
  (
    re.compile(
      r"\b(solve|compute|calculate|prove|derive)\b.*"
      r"\b(equation|integral|derivative|theorem|proof|problem)\b",
      re.IGNORECASE,
    ),
    RequestType.ANALYTICAL_REASONING,
  ),
  (
    re.compile(r"\b(if .+ then|given .+ find|suppose|assume)\b", re.IGNORECASE),
    RequestType.ANALYTICAL_REASONING,
  ),
  (
    re.compile(
      r"\b(probability|statistics|combinatorics|optimization problem)\b", re.IGNORECASE
    ),
    RequestType.ANALYTICAL_REASONING,
  ),
  (
    re.compile(
      r"\b(write|draft|compose|rewrite|edit|proofread|polish)\b.*"
      r"\b(email|essay|blog|post|article|letter|memo|copy|paragraph|sentence)\b",
      re.IGNORECASE,
    ),
    RequestType.WRITING,
  ),
  (
    re.compile(
      r"\b(make (?:this|it)|help me)\s+(?:more |less )?"
      r"(?:concise|formal|casual|professional|persuasive)\b",
      re.IGNORECASE,
    ),
    RequestType.WRITING,
  ),
  (
    re.compile(
      r"^\s*(who|what|when|where|which)\s+(?:is|was|were|are)\b", re.IGNORECASE
    ),
    RequestType.FACTUAL_LOOKUP,
  ),
  (
    re.compile(r"^\s*(define|definition of|meaning of)\b", re.IGNORECASE),
    RequestType.FACTUAL_LOOKUP,
  ),
  (
    re.compile(
      r"^\s*how (?:do you spell|to spell|many .* are there|tall is)\b", re.IGNORECASE
    ),
    RequestType.FACTUAL_LOOKUP,
  ),
]

_CODE_PATTERN: Final = re.compile(
  r"```|\b(def|class|function|python|javascript|typescript|sql|code)\b",
  re.IGNORECASE,
)
_MATH_PATTERN: Final = re.compile(
  r"\b(solve|calculate|equation|probability|theorem|proof|integral)\b|[$=]",
  re.IGNORECASE,
)
_MULTIPLE_CHOICE_PATTERN: Final = re.compile(r"(?:^|\s)[A-D][.)]\s")
_PROMPT_LIMIT: Final = 2000


def classify_prompt(text: str) -> RequestType:
  """Name what a prompt asks for. The first rule that matches wins, else `GENERAL`."""
  if not text or not text.strip():
    return RequestType.GENERAL
  truncated: Final = text[:_PROMPT_LIMIT]
  for pattern, request_type in _RULES:
    if pattern.search(truncated):
      return request_type
  return RequestType.GENERAL


def similarity_cohort(prompt: str, request_type: RequestType) -> str:
  """Build the artifact key for a prompt: its type plus five shape flags."""
  length: Final = len(prompt)
  if length < 200:
    bucket = "short"
  elif length < 800:
    bucket = "medium"
  elif length < 2000:
    bucket = "long"
  else:
    bucket = "very_long"
  code: Final = int(bool(_CODE_PATTERN.search(prompt)))
  math: Final = int(bool(_MATH_PATTERN.search(prompt)))
  choice: Final = int(bool(_MULTIPLE_CHOICE_PATTERN.search(prompt)))
  foreign: Final = int(sum(ord(c) > 127 for c in prompt) / max(1, length) > 0.1)
  return (
    f"{request_type.value}|{bucket}|code={code}|math={math}|mc={choice}|intl={foreign}"
  )


@dataclass(frozen=True, slots=True)
class TierStatistic:
  """One artifact row: successes out of observations, for one tier."""

  tier: int
  successes: float
  observations: float


@dataclass(frozen=True, slots=True)
class Artifact:
  """The calibrated tier table, loaded from the vendored JSON."""

  global_stats: Mapping[int, TierStatistic]
  domain_stats: Mapping[tuple[str, int], TierStatistic]
  cohort_stats: Mapping[tuple[str, int], TierStatistic]
  domain_prior_mass: float
  cohort_prior_mass: float
  routing_threshold: float


@dataclass(frozen=True, slots=True)
class Prediction:
  """The odds per tier, and the cheapest tier that clears the threshold."""

  probabilities: Mapping[int, float]
  required_tier: int

  @property
  def tier_name(self) -> str:
    """The config key for the tier this request needs."""
    return TIER_NAMES[self.required_tier]


def _rows(payload: list[dict[str, Any]], key: str | None) -> dict[Any, TierStatistic]:
  stats: dict[Any, TierStatistic] = {}
  for row in payload:
    tier = row["tier"]
    stat = TierStatistic(tier, float(row["successes"]), float(row["observations"]))
    stats[stat.tier if key is None else (row[key], stat.tier)] = stat
  return stats


def load_artifact(path: Path | None = None) -> Artifact:
  """Read the tier table. Fails loudly if a tier is missing or a row repeats."""
  source: Final = ARTIFACT_PATH if path is None else path
  payload: Final = json.loads(source.read_text(encoding="utf-8"))
  global_stats = _rows(payload.get("global_statistics") or [], None)
  if sorted(global_stats) != list(TIERS):
    msg = f"{source} must hold global statistics for tiers 1 to 4"
    raise ValueError(msg)
  domain = payload.get("domain_statistics") or []
  cohort = payload.get("cohort_statistics") or []
  return Artifact(
    global_stats=global_stats,
    domain_stats=_rows(domain, "request_type"),
    cohort_stats=_rows(cohort, "cohort"),
    domain_prior_mass=float(payload.get("domain_prior_mass", 200.0)),
    cohort_prior_mass=float(payload.get("cohort_prior_mass", 20.0)),
    routing_threshold=float(payload.get("routing_threshold", 0.75)),
  )


def _posterior_mean(
  stat: TierStatistic | None,
  prior_mass: float,
  prior_mean: float,
) -> float:
  """Pull a row's rate toward the tier above it. No row means no pull."""
  if stat is None:
    return prior_mean
  return (stat.successes + prior_mass * prior_mean) / (stat.observations + prior_mass)


def _probability(
  artifact: Artifact,
  tier: int,
  request_type: RequestType,
  cohort: str,
) -> float:
  """Success odds for one tier: global, then domain, then cohort."""
  global_stat: Final = artifact.global_stats[tier]
  global_mean: Final = (global_stat.successes + 1.0) / (global_stat.observations + 2.0)
  domain_stat: Final = artifact.domain_stats.get((request_type.value, tier))
  domain_mean: Final = _posterior_mean(
    domain_stat, artifact.domain_prior_mass, global_mean
  )
  cohort_stat: Final = artifact.cohort_stats.get((cohort, tier))
  return _posterior_mean(cohort_stat, artifact.cohort_prior_mass, domain_mean)


def predict(
  prompt: str, artifact: Artifact, request_type: RequestType | None = None
) -> Prediction:
  """Score a prompt against every tier, and name the cheapest tier that will do."""
  kind: Final = classify_prompt(prompt) if request_type is None else request_type
  cohort: Final = similarity_cohort(prompt, kind)
  raw: Final = tuple(_probability(artifact, tier, kind, cohort) for tier in TIERS)
  monotonic: Final = tuple(max(raw[:index]) for index in range(1, len(raw) + 1))
  probabilities: Final[dict[int, float]] = dict(zip(TIERS, monotonic, strict=True))
  required: Final = next(
    (tier for tier in TIERS if probabilities[tier] >= artifact.routing_threshold),
    TIERS[-1],
  )
  return Prediction(probabilities=probabilities, required_tier=required)


def tier_models(provider: Mapping[str, Any], tier_name: str) -> list[str]:
  """The models one provider block lists under a tier name."""
  tiers = provider.get("tier") or {}
  names = tiers.get(tier_name) or []
  return [name for name in names if isinstance(name, str) and name]


def candidates(
  config: Mapping[str, Any],
  tier_name: str,
  lines: list[str],
) -> list[str]:
  """Return the provider/slug rows that one tier claims."""
  wanted: list[str] = []
  for provider_name, provider in config.items():
    if not isinstance(provider, dict):
      continue
    patterns = tier_models(provider, tier_name)
    if not patterns:
      continue
    head = f"{provider_name}/"
    for line in lines:
      if line.startswith(head) and any_match(patterns, line[len(head) :]):
        wanted.append(line)
  return wanted


def fallback_order(tier: int) -> tuple[int, ...]:
  """List the tiers to try, this one, up to tier 4, then down to tier 1."""
  above: Final = tuple(range(tier, TIERS[-1] + 1))
  below: Final = tuple(range(tier - 1, 0, -1))
  return above + below


def chain_models(
  config: Mapping[str, Any],
  lines: list[str],
  order: tuple[int, ...],
) -> list[str]:
  """Every `provider/slug` in a tier chain, in the order the proxy tries them."""
  found: list[str] = []
  for tier in order:
    found.extend(candidates(config, TIER_NAMES[tier], lines))
  return found


def route(
  prompt: str,
  config: Mapping[str, Any],
  lines: list[str],
  artifact: Artifact | None = None,
) -> list[str]:
  """The models for a prompt, in the order the proxy tries them."""
  return chain_models(config, lines, fallback_order(required_tier(prompt, artifact)))


def required_tier(prompt: str, artifact: Artifact | None = None) -> int:
  """The cheapest tier that will do for one prompt."""
  table: Final = load_artifact() if artifact is None else artifact
  return predict(prompt, table).required_tier


def route_pool(
  pool: str,
  config: Mapping[str, Any],
  lines: list[str],
) -> list[str]:
  """The models for a named pool, with no classifier call."""
  tier = POOLS.get(pool)
  if tier is None:
    return []
  return chain_models(config, lines, fallback_order(tier))


def route_praktos(config: Mapping[str, Any], lines: list[str]) -> list[str]:
  """The tool-capable models, TIER-A first, then TIER-B."""
  return chain_models(config, lines, PRAKTOS_TIERS)
