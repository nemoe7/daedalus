# 4. Penalties and session affinity order each chain

## Status

Accepted.

## Context

The chains of ADR 1 and ADR 2 start at the same model for each request. A model that fails
gets the next request again, and ADR 3 then moves the request to the next model. One
conversation can also move between models from one turn to the next.

## Decision

### Weights

Each model has a weight from 0.01 to 1. All models start at 1. The floor of 0.01 lets a model with many faults recover: from 0.01 to 0.5 in about 22 hours.

| Event | New weight |
| --- | --- |
| Success | weight x 1.5, 1 at most |
| Fault | weight x 0.5, 0.01 at least |
| Slow success: the first token comes after half the wait limit | weight x 0.75, 0.01 at least |
| Each hour | weight x 1.2, 1 at most, as a continuous rate |

A fault is each failure that ADR 3 reroutes: no connection, HTTP 400 or higher, a bad answer,
or a failed stream. One weight applies to all clients.

TTFT is the time from the send to the first chunk with text or a tool call. Chunks without
content, for example a role chunk or a keep-alive comment, do not stop the clock. Without a
stream, TTFT is the time to the full answer. A successful request logs `ttft=N.NNNs`, in
seconds with 3 decimals.

### Order

The tier order of ADR 1 and ADR 2 stays. The weights sort the models only inside each tier.

1. The first model is a random choice from the first tier. The probability of each model is
   its weight divided by the sum of the weights in that tier.
2. All other models follow by weight, high first, tier by tier. Equal weights keep the usual
   order.

In a session, the session model gets 85% of the draws in the first tier. The other models of
that tier share the remaining 15% by weight. After the first model, the session model goes
first in its tier.

### Session affinity

Daedalus keeps one session model for each conversation and slot. The conversation key is the
SHA-256 hash of the bearer token and the first user message.

| Request | Slot |
| --- | --- |
| A tier pool | The pool name |
| `daedalus/praktos`, or `daedalus/auto` with `tools` | `daedalus/praktos` |
| `daedalus/auto` | `daedalus/auto` and the required tier |
| `provider/slug` | None |

The first model that answers in time becomes the session model. A fault or a slow success of
the session model removes it, and the next model that answers in time becomes the new session
model. A slow success does not become the session model. A session model expires after 1 hour
without a request.

Weights and session models stay in `models.sqlite3`. A restart keeps them, and `daedalus catalog` keeps them
when it rebuilds the model table.

### Settings

The numbers in this ADR are the defaults. `config/daedalus.yml` can change them, and it can
turn off the weights or the session affinity.

## Consequences

- A model that fails often gets fewer first attempts, but it is not removed.
- A model with a low weight comes back over time: from 0.5 to 1 in about 4 hours.
- One conversation stays on its session model for most turns. The other models of the first
  tier still get some turns, so a bad session model does not hold a conversation for ever.
- The request log shows `pin=new`, `pin=hit`, `pin=moved` or `pin=slow`.
