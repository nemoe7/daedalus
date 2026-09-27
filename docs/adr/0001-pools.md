# 1. Pools are the gateways to the tiers

## Status

Accepted.

## Context

A client must be able to ask for a level of capability without naming a provider model.
The config already sorts models into four tiers for each provider, and the router can pick
a tier from a prompt. Some clients already know the level they need, and those should not
pay for a classifier call.

## Decision

Four reserved names act as pools. Each pool is a gateway to one tier.

| Pool | Tier | Config key | From Greek |
| --- | --- | --- | --- |
| `daedalus/moros` | 1 | `TIER-D` | moros, an allotted fate |
| `daedalus/koinos` | 2 | `TIER-C` | koinos, common or shared |
| `daedalus/deinos` | 3 | `TIER-B` | deinos, formidable or mighty |
| `daedalus/sophos` | 4 | `TIER-A` | sophos, wise or skilled |

Each provider block lists patterns under each tier key. A pattern has 1 of 4 types:

- an exact name
- a glob with `*` or `?`
- a regex with a leading `^`
- a negation with a leading `!`

A model goes to 1 tier only. When patterns of 2 tiers match a model, the most specific
pattern sets the tier. An exact name comes first, then a glob, then a regex, then a
negation. Between 2 globs, the glob with more literal characters wins. Between 2 regexes,
the longer regex wins. A tie goes to the higher tier.

Pool names follow one convention. A pool name is a two-syllable Greek adjective that ends
in -os.

A request that names a pool goes to that tier. No classifier runs.

A tier that holds no model falls back. The owner names the rule promote then demote. In
order, the chain starts at the pool's tier, walks up to TIER-A, and then walks down from
the next tier to TIER-D:

- moros: moros, koinos, deinos, sophos
- koinos: koinos, deinos, sophos, moros
- deinos: deinos, sophos, koinos, moros
- sophos: sophos, deinos, koinos, moros

The first tier in the chain that holds a model answers.

`daedalus/auto` stays. It scores the prompt, takes the tier the prompt needs, and then
follows the same chain from that tier.

## Consequences

- A client can pin a level and still get an answer when a provider lacks that tier.
- Promotion comes before demotion, so a request can reach a stronger model before a
  weaker one.
- The proxy reserves the four pool names. No provider model may use them.
- A retry inside a pool comes before the chain moves on.
- `"*"` under a tier key puts all other models of that provider in that tier.
