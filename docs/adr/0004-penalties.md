# 4. Penalties and session affinity order each chain

## Status

Accepted.

## Context

The chains of ADR 1 and ADR 2 start at the same model for each request. A model that fails
gets the next request again, and ADR 3 then moves the request to the next model. One
conversation can also move between models from one turn to the next.

## Decision

### Weights

Each model has a weight from 0 to 1. All models start at 1.

| Event | New weight |
| --- | --- |
| Success | weight x 1.5, 1 at most |
| Fault | weight x 0.5 |
| Slow success: the first token comes after half the wait limit | weight x 0.75 |
| Each hour | weight x 1.2, 1 at most, as a continuous rate |

A fault is each failure that ADR 3 reroutes: no connection, HTTP 400 or higher, a bad answer,
or a failed stream. One weight applies to all clients.

TTFT is the time from the send to the first chunk with text or a tool call. Chunks without
content, for example a role chunk or a keep-alive comment, do not stop the clock. Without a
stream, TTFT is the time to the full answer. A successful request logs `ttft=Nms`.

### Order

The tier order of ADR 1 and ADR 2 stays. The weights sort the models only inside each tier.

1. The pin goes first, if the chain has it.
2. Without a pin, the first model is a random choice from the first tier. The probability of
   each model is its weight divided by the sum of the weights in that tier.
3. All other models follow by weight, high first, tier by tier. Equal weights keep the usual
   order.

### Session affinity

Daedalus keeps one pin for each pair of API key and slot. The API key is the SHA-256 hash of
the bearer token. Clients without a token share one key.

| Request | Slot |
| --- | --- |
| A tier pool | The pool name |
| `daedalus/praktos`, or `daedalus/auto` with `tools` | `daedalus/praktos` |
| `daedalus/auto` | `daedalus/auto` and the required tier |
| `provider/slug` | None |

The first model that answers becomes the pin. A fault of the pinned model removes the pin,
and the next model that answers becomes the new pin. A pin expires after 1 hour without a
request.

Weights and pins stay in `models.sqlite3`. A restart keeps them, and `daedalus catalog` keeps them
when it rebuilds the model table.

## Consequences

- A model that fails often gets fewer first attempts, but it is not removed.
- A model with a low weight comes back over time: from 0.5 to 1 in about 4 hours.
- One client stays on one model for each slot until that model fails.
- A pin can be in a lower tier than the other models, because it goes first.
- The request log shows `pin=new`, `pin=hit` or `pin=moved`.
