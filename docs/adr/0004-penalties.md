# 4. Penalties and session affinity order each chain

## Status

Accepted.

## Context

The chains of ADR 1 and ADR 2 start at the same model for each request. A model that fails
gets the next request again, and ADR 3 then moves the request to the next model. One
conversation can also move between models from one turn to the next.

## Decision

### Weights

Each model has a weight from 0.01 to 1. All models start at 1. The floor of 0.01 lets a model with many faults recover: from 0.01 to 1 in 24 hours.

| Event | New weight |
| --- | --- |
| Success | weight x 1.5, 1 at most |
| Fault | weight x 0.5, 0.01 at least |
| Slow success: the first token comes after `timeouts.slow`, 30 s by default | weight x 0.75, 0.01 at least |
| Rate limit: HTTP 429 | weight x 0.75, 0.01 at least, and a cooldown |
| Each hour | weight x 1.212, 1 at most, as a continuous rate |

A fault is each failure that ADR 3 reroutes, except HTTP 429. Faults are: no connection,
HTTP 400 or higher, a bad answer, or a failed stream. One weight applies to all clients.

TTFT is the time from the send to the first chunk with text or a tool call. Chunks without
content, for example a role chunk or a keep-alive comment, do not stop the clock. Without a
stream, TTFT is the time to the full answer. A successful request logs `ttft=N.NNNs`, in
seconds with 3 decimals.

### Rate limits

A rate limit is an HTTP 429 answer. A model in a cooldown drops out of each chain, as a model
that is too small for the input does. A session model in a cooldown loses its pin.

The cooldown end comes from the first rule that applies: a daily limit from the provider, a
reset time, or a doubling backoff. The backoff starts at 1 minute, and a success starts it
again. A daily limit comes first, because a Gemini daily 429 can carry a
`retryDelay` of only 1 second. [Architecture](../architecture.md#cooldowns) holds the rules,
the default numbers and the log line.

A `provider/slug` request to a model in a cooldown gets HTTP 429 `rate_limit_exceeded` at
once, with no upstream request. When all models of a chain are in a cooldown, the client also
gets HTTP 429 `rate_limit_exceeded`. The `Retry-After` header is the seconds to the first
cooldown end.

### Pacing

A model with `rpm` or `tpm` in its provider yml drops out of the chains when its requests in
the last 60 seconds reach that limit. A pacing skip is silent and does not change the weight.
The counts stay in memory only.

### Order

The tier order of ADR 1 and ADR 2 stays. The weights sort the models only inside each tier.

1. The first model is a random choice from the first tier. The probability of each model is
   its weight divided by the sum of the weights in that tier.
2. All other models follow by weight, high first, tier by tier. Equal weights keep the usual
   order.

In a session, the session model gets 85% of the draws in the first tier. The other models of
that tier share the remaining 15% by weight. After the first model, the session model goes
first in its tier. With `affinity.mode: race`, the session model starts each request instead,
and the next model of the chain can race it (ADR 5).

### Session affinity

daedalus keeps one session model for each conversation and slot. The conversation key is the
SHA-256 hash of the bearer token and the first user message.

| Request | Slot |
| --- | --- |
| A tier pool | The pool name |
| `daedalus/auto` | `daedalus/auto` and the required tier |
| `provider/slug` | None |

The first model that answers in time becomes the session model. A fault or a slow success of
the session model removes it, and the next model that answers in time becomes the new session
model. A slow success does not become the session model. A session model expires after 1 hour
without a request.

Weights, cooldowns and session models stay in `models.sqlite3`. A restart keeps them, and `daedalus catalog` keeps them
when it rebuilds the model table.

### Loops

A loop is a fault of the model that made it: a repeated tool call, a repeated thinking
passage, or a repeated answer passage. [Architecture](../architecture.md#loops) holds the
counts and the next step of each one.

A passage has 20 to 2,000 characters. A passage made of a shorter part that repeats, for
example a line of `=`, is not a loop. The arguments of 2 calls are the same when their JSON
values are the same, in any key order.

daedalus keeps the model that made each tool call, by tool call id, in `models.sqlite3`. A call id expires
after the session idle time. When the id is not known, the tool loop gives no fault. The request log shows
`loop=N`, where N is the number of the same calls.

A thinking or answer loop shows as an attempt with the result `loop`.
[`config/daedalus.yml`](../../config/daedalus.yml) cannot change the loop numbers.

### Settings

The numbers in this ADR are the defaults. [`config/daedalus.yml`](../../config/daedalus.yml) can change them, and it can
turn off the weights, the session affinity or the pacing. The `affinity` group holds the
session and the race settings.

## Consequences

- A model that fails often gets fewer first attempts, but it is not removed.
- A model with a low weight comes back over time: from 0.5 to 1 in about 4 hours.
- One conversation stays on its session model for most turns. The other models of the first
  tier still get some turns, so a bad session model does not hold a conversation for ever.
- With `affinity.mode: race`, the draws stop and the session model keeps the conversation
  while it answers first (ADR 5).
- The request log shows `pin=new`, `pin=hit`, `pin=moved`, `pin=switched` or `pin=slow`.
- A per-minute limit costs 1 request and about 1 minute, not hours at a low weight.
- A daily limit costs 1 request for each model until the reset.
- The Models page shows the end of each cooldown.
- A model that repeats a tool call or a passage loses weight as a failed model does. The loop
  stops long before the client limit, for example 256 tool rounds in Open WebUI.
