# 3. Praktos is the pool for tool calls

## Status

Accepted.

## Context

An agent sends `tools` and expects `tool_calls` back. A model that cannot call tools fails
the request or answers with text. The tier pools of ADR 1 sort models by capability level
only, so a tool request can reach a model without tool support.

## Decision

`daedalus/praktos` is the pool for tool calls.

| Pool | Members | From Greek |
| --- | --- | --- |
| `daedalus/praktos` | TIER-A and TIER-B rows that can call tools | praktos, to be done or doable |

The name follows the ADR 1 convention: a two-syllable Greek adjective that ends in -os.

The config key `tools` decides first. `tools: true` on the provider or on a matching
`models` entry adds the row. `tools: false` keeps the row out.

Without a `tools` key, the LiteLLM value `supports_function_calling: true` adds the row.
A row with no config key and no LiteLLM value stays out.

The chain holds the TIER-A members first, then the TIER-B members.

`daedalus/auto` sends a request with `tools` to praktos. Its classifier does not run.

A praktos chain with no member falls back to the `daedalus/auto` chain for the prompt, and
the proxy logs a warning.

## Consequences

- Daedalus reserves the name `daedalus/praktos`. No provider model may use it.
- An agent request reaches only strong models that the owner or the catalog marks as
  tool-capable.
- A model without LiteLLM data does not enter praktos until the config sets `tools: true`.
- TIER-C and TIER-D models do not get tool requests from praktos or from `daedalus/auto`.
