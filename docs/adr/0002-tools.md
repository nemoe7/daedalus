# 2. A tool request skips models that cannot call tools

## Status

Accepted.

## Context

An agent sends `tools` and expects `tool_calls` back. A model that cannot call tools fails
the request or answers with text. The tier pools of ADR 1 sort models by capability level
only, so a tool request can reach a model without tool support.

## Decision

A request with `tools` skips the models that cannot call tools, in the chains of the pools
and of `daedalus/auto`. The skip is silent: no log line, no chain entry in Requests, and
no fallback count.

The config key `tools` decides first. `tools: true` on the provider or on a matching
`models` entry marks the row as tool-capable. `tools: false` marks it as not tool-capable.
Without a `tools` key, the LiteLLM value `supports_function_calling: true` marks the row.
A row with no config key and no LiteLLM value is not tool-capable.

`daedalus/auto` with `tools` runs the classifier and follows the chain of the required
tier.

A request for one `provider/slug` goes to that model, with no skip.

## Consequences

- A tool request from a pool or from `daedalus/auto` never reaches a model that cannot
  call tools.
- A model without LiteLLM data gets no tool request from a chain until the config sets
  `tools: true`.
- A tool request can reach TIER-C and TIER-D models.
- When no model in the chain can call tools, the client gets 502 `upstream_error`.
