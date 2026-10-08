# 5. Parallel queries race for the session pin

## Status

Accepted.

## Context

A session model answers most turns. A slow first token holds the client until that token
comes. The draw of ADR 4 moves some turns to another model of the first tier. It does not remove
the wait of the current turn.

## Decision

A stream request to `daedalus/auto` or to a tier pool can start the next models of its own
chain before the first model answers. The keys are in `affinity`:

| Key | Default | Use |
| --- | --- | --- |
| `affinity.mode` | `session` | `race`: the race runs, and the session model starts each request |
| `affinity.count` | `1` | The models that race the original one, from 1 to 10 |
| `affinity.chance` | `0.05` | The chance of the racing models at the start of the request |
| `affinity.slow` | `30` | The seconds with no content from the first model, then the racing models start |
| `affinity.penalty` | `0.9` | The weight factor for each model that loses the race |

The first model with content wins the session pin. Each other model stops at once. Content
from 2 models at the same time gives the pin to the model that started first. A model that
loses takes the weight factor, and no cooldown.

The race applies to `daedalus/auto` and to the tier pools, on a stream. A direct
`provider/slug` request and a request without a stream do not race. The race runs
when `affinity.mode` is `race`.

## Consequences

- A slow first token costs the provider 1 request for each racing model.
- The 85/15 draw of ADR 4 stops while the race runs. A conversation with no session model
  takes the draw of ADR 4, and then the race of this ADR.
- A fast session model keeps the pin, so its answers hold a conversation. The switch
  keywords and a client retry stay as the moves by hand.
- The request log shows each model that lost as `lost race`, with its own effort.
