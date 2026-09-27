# Architecture

> Q: What is the main idea of the design, in your words?

## Request flow

```mermaid
flowchart TD
  C[Client] -->|POST /v1/chat/completions| A{Master key or API key?}
  A -->|no| E401[401]
  A -->|yes| M{Model name}
  M -->|daedalus/auto| CL[Classifier sets the tier]
  M -->|pool name| P[Tier of the pool]
  M -->|provider/slug| D[That model only]
  CL --> L[Fallback ladder]
  P --> L
  L --> F[Remove models without tools, for a tool request]
  F --> S[Remove models with a too-small context window]
  S --> O[Order: weights and session model]
  D --> H
  O --> H[Headroom compression, when on]
  H --> T[Try the next model]
  T -->|answer| OK[Answer to the client]
  T -->|error| T
  T -->|no model left| E502[Last error to the client]
```

| Step | Rule |
| --- | --- |
| Access | `/v1` needs the master key or an API key from the dashboard. |
| Tool filter | A request with `tools` skips the models that cannot call tools. |
| Context filter | Tokens = characters / 4, input only. A model with a smaller context window leaves the list. No log line shows it. |
| No model fits | The client gets 400 `context_length_exceeded`. |
| Error | The next model gets the request. The client sees only the last error. |
| Stream | When a stream stops, the next model continues the answer. |

The end client only sees the last error to provide a cleaner transition between models in the fallback ladder.

## Classification

Only `daedalus/auto` uses the classifier. A pool name or a `provider/slug` name skips it.

```mermaid
flowchart TD
  U[All user messages, no system prompt] --> R[Rules: request type]
  U --> K[Shape: length, code, math, choices, language]
  R --> AR[Calibrated table from LiteLLM]
  K --> AR
  AR --> T1[Cheapest tier over the threshold]
  T1 --> TF{Tool call in the conversation?}
  TF -->|yes| KF[koinos or higher]
  TF -->|no| HT
  KF --> HT[Highest tier of the conversation so far]
  HT --> T[Tier for this request]
```

| Input | Effect |
| --- | --- |
| Request type | 1 of 7 types, from the first rule that matches. |
| Length | More than 2000 characters moves the text to a higher tier. |
| Tool call | After the first tool call, the tier is koinos or higher. |
| Conversation | The tier does not go down until the session expires (1 h idle). |

The classifier is a copy of the LiteLLM heuristic v2. It is not perfect, but it is a good start.

## Pools and the fallback ladder

| Pool | Tier | Models |
| --- | --- | --- |
| `daedalus/sophos` | A | Big strong thinking models |
| `daedalus/deinos` | B | Weaker thinking models |
| `daedalus/koinos` | C | Stronger models that do not think |
| `daedalus/moros` | D | Small models |

When a tier has no model that answers, the chain goes up to tier A, then down from the start:

```mermaid
flowchart LR
  subgraph moros
    direction LR
    m1[D] --> m2[C] --> m3[B] --> m4[A]
  end
  subgraph koinos
    direction LR
    k1[C] --> k2[B] --> k3[A] --> k4[D]
  end
  subgraph deinos
    direction LR
    d1[B] --> d2[A] --> d3[C] --> d4[D]
  end
  subgraph sophos
    direction LR
    s1[A] --> s2[B] --> s3[C] --> s4[D]
  end
```

The chain goes up first. The next tier up can usually do the same request. The chain goes down only after it gets to the highest tier. A lower tier often fails, because its models can do less.

## Weights

Each model has 1 weight for all pools. The first tier uses a weighted draw. The next tiers use the weight order.

```mermaid
stateDiagram-v2
  direction LR
  [*] --> W: weight 1
  W --> W: success x1.5, 1 at most
  W --> W: fault x0.5
  W --> W: slow success x0.75
  W --> W: each hour x1.212, 1 at most
```

| Event | Factor |
| --- | --- |
| Success | x1.5, 1 at most |
| Fault (the next model got the request) | x0.5 |
| Slow success (first token after 30 s) | x0.75 |
| Recovery | x1.212 for each hour |
| Lowest weight | 0.01 |

## Session affinity

A conversation keeps its model (the session model) in each slot.

```mermaid
sequenceDiagram
  participant C as Client
  participant D as Daedalus
  participant M as Model
  C->>D: Request 1
  D->>M: Weighted draw in the first tier
  M-->>D: Answer in time
  D->>D: Pin the model
  C->>D: Request 2, same conversation
  D->>M: Session model in 85% of draws
  M-->>D: Error or slow answer
  D->>D: Remove the pin
```

| Item | Value |
| --- | --- |
| Conversation key | SHA-256 of the bearer token and the first user message |
| Slot | The pool name, or `daedalus/auto` plus the tier |
| Share of first-tier draws | 85% |
| Pin removed by | A fault or a slow success |
| Expiry | 1 h with no request |
| Storage | `.daedalus-state/models.sqlite3`, kept after a restart |

Session affinity keeps 1 response style in a conversation. It also lets the conversation use the prompt cache of the provider. The cache is not guaranteed, but it helps when the provider has one. Without session affinity, the styles of different models mix in 1 conversation. Tests showed that the result is a mess.

## Try again

A try again in Open WebUI on `daedalus/auto` moves the repeated message 1 tier up.

| Item | Value |
| --- | --- |
| Found by | The same `X-OpenWebUI-Chat-Id` and the same messages as the last request of that chat. System messages do not count. |
| Tier | 1 above the pool that answered the last attempt |
| At tier A | A tier A model that did not answer this message. After all tier A models, the list starts again. |
| Next new message | The classifier and the session tier, as before |
| Session model | The model that answers becomes the session model of its tier slot |
| Weights | No change for the earlier answer |
| Log | `retry=N`. The Requests page shows "try again N". |
| Expiry | 1 h with no request, in memory only |
| Other clients, chat pools, `provider/slug` | No change |

## Media pools

| Pool | Endpoint | Models |
| --- | --- | --- |
| `daedalus/graphos` | `POST /v1/audio/transcriptions` | All catalog models with the mode `audio_transcription` |
| `daedalus/photos` | `POST /v1/images/generations` | All catalog models with the mode `image_generation` |

| Item | Value |
| --- | --- |
| Order | A weighted draw, then the weight order |
| Weights | The same weights as the chat models |
| Skip | A model that cannot do the request leaves the list, for example Flux 1 with `n` above 1. A skip is not a fault. |
| Try again | The same key and the same content as the last request to that pool. For graphos, the content is the audio and the form fields. For photos, the content is the JSON body. |
| Models of a try again | The models that answered this content leave the list. After all models, the list starts again. |
| Log | `retry=N` |
| Expiry | 1 h with no request, in memory only |
| Embeddings and speech | No pool. `provider/slug` only. |

By default, Open WebUI writes a new image prompt for each try. So a try again in Open WebUI chat is a new weighted draw, not a repeat.

> Q: Why do transcription and images get a pool, and embeddings and speech not?
