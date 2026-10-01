# 5. Parallel queries race for the session pin

## Status

Accepted.

## Context

A session model answers most turns. A slow first token holds the client until that token
comes. The draw of ADR 4 moves some turns to another model of the first tier, but the draw
does not remove the wait of the current turn.

## Decision

A stream request to `daedalus/auto` or to a tier pool can start the next model of its own
chain before the first model answers. The keys are in `parallel`:

| Key | Default | Use |
| --- | --- | --- |
| `parallel.enabled` | `false` | On: the race runs, and the session model starts each request |
| `parallel.chance` | `0.05` | The chance of the second model at the start of the request |
| `parallel.slow` | `30` | The seconds with no content from the first model, then the second model starts |
| `parallel.penalty` | `0.9` | The weight factor for the model that loses the race |

The first model with content wins the session pin. The other model stops at once. Content
from both models at the same time gives the pin to the model that started first. The model
that loses takes the weight factor, and no cooldown.

The race applies to `daedalus/auto` and to the tier pools, on a stream. A direct
`provider/slug` request and a request without a stream do not race. The race needs
`session_affinity.enabled`.

## Consequences

- A slow first token costs the provider 2 requests.
- The 85/15 draw of ADR 4 stops while the race runs. A conversation with no session model
  takes the draw of ADR 4, and then the race of this ADR.
- A fast session model keeps the pin, so its answers hold a conversation. The switch
  keywords and a client retry stay as the moves by hand.
- The request log shows the model that lost as `lost race`, with its own effort.
