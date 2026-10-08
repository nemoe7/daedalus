# 2. A tool request or an image request skips models that cannot take it

## Status

Accepted.

## Context

An agent sends `tools` and expects `tool_calls` back. A model that cannot call tools fails
the request or answers with text. The tier pools of ADR 1 sort models by capability level
only, so a tool request can reach a model without tool support.

An image request has the same problem. A model without vision refuses the request, and the
model gets a fault.

## Decision

A request with `tools` skips the models that cannot call tools, in the chains of the pools
and of `daedalus/auto`. The skip is silent: no log line, no chain entry in Requests, and
no fallback count.

The config key `tools` decides first. `tools: true` on the provider or on a matching `models` entry marks the
row as tool-capable. `tools: false` marks it as not tool-capable. Without a `tools` key, the discovery value,
then the LiteLLM value, marks the row.

A row with no config key, no discovery value and no LiteLLM value is not tool-capable.

A request with an `image_url` part in any message skips the models without vision, in the
same chains and with the same silent skip. The value `supports_vision` decides, in the same
order: config, then discovery, then LiteLLM. A row with no value has no vision.

`daedalus/auto` with `tools` runs the classifier and follows the chain of the required
tier.

A request for one `provider/slug` goes to that model, with no skip.

## Consequences

- A tool request from a pool or from `daedalus/auto` never reaches a model that cannot
  call tools.
- An image request from a pool or from `daedalus/auto` never reaches a model without
  vision.
- A model without LiteLLM data or a discovery value gets no tool request from a chain
  until the config sets `tools: true`.
- A model with no vision value gets no image request from a chain until the config sets
  `supports_vision: true`. Cloudflare text models and Gemini models without LiteLLM data
  have no value.
- A tool request can reach TIER-C and TIER-D models.
- When no model in the chain can call tools or take the image, the client gets 502
  `upstream_error`.
