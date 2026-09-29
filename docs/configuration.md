# Configuration

daedalus reads 3 sources:

| Source | Contents | Edit from the dashboard |
| --- | --- | --- |
| `.env` | Keys and container settings | No |
| `config/daedalus.yml` | Router settings | Yes, on the **Settings** page |
| `config/providers/free.yml` | Providers, tiers and model limits | Yes, on the **Providers** page |
| `config/providers/{provider}.yml` | 1 provider, on its own | Yes, on the **Providers** page |

`daedalus.yml` contains the main settings for the router and its jobs. `config/providers/` contains the provider configurations (tiers, models, limits, etc).

A key that shows 2 times in 1 map of a config file stops the start. The error gives the key and the 2 line numbers.

## Files per provider

`free.yml` holds each provider. A second file, `{provider}.yml`, holds 1 provider with its own settings. `openrouter.yml` and the `openrouter` block of `free.yml` are independent: the tiers, the excludes and the models of 1 file do not apply to the other. A catalog rebuild reads each `discovery_url` 1 time, also when the 2 files have different API keys. It reads each LiteLLM catalog 1 time: Kilo and OpenRouter share the OpenRouter one. `pollinations.yml` holds Pollinations, with no block in `free.yml`, because Pollinations spends a balance that does not refill ([pollinations#11580](https://github.com/pollinations/pollinations/issues/11580)).

| Item | Rule |
| --- | --- |
| Name | The file name is the provider name: `openrouter.yml` holds `openrouter`. |
| Provider key | A file with no provider key (`api_key`, `client_keys`, `api_base`, `tier`, `models`, `exclude`, `discovery_url`, `discovery_match`, `api_type`) is not a provider file. |
| Discovery | Each file discovers the models of its provider with its own settings. |
| Models | A file keeps only the models that match a key of its `models` block. A key of `"*"` keeps each model of that provider. |
| Values | For a model that a file keeps, only that file sets the values. The `free.yml` block does not apply to it. |
| New file | The **Providers** page makes a file, and a name that daedalus does not know also gets an empty `api_base`. |

## Environment variables

| Name | Default | Use |
| --- | --- | --- |
| `DAEDALUS_MASTER_KEY` | None. Necessary. | `/v1` key. 16 or more characters, no spaces. |
| `DAEDALUS_USERNAME` | `admin` | Dashboard user |
| `DAEDALUS_PASSWORD` | `DAEDALUS_MASTER_KEY` | Dashboard password. It does not open `/v1`. |
| `DAEDALUS_HOST` | `0.0.0.0` | Address of the server |
| `DAEDALUS_PORT` | `3357` | Port of the server. `daedalus serve PORT` has priority. |
| `TZ` | UTC | Clock for the catalog schedule, for example `Asia/Manila` |
| `DAEDALUS_UID`, `DAEDALUS_GID` | `1000` | Container user and group |
| `HEADROOM_URL` | `http://headroom:8787` in Compose | Headroom compression. An empty value stops it. |
| `COMPOSE_PROFILES` | Empty | Optional services: `webui`, `tika`, `search`, `headroom`, `tailscale` |
| `OPENWEBUI_API_KEY` | `DAEDALUS_MASTER_KEY` | The key that Open WebUI sends to daedalus |
| `OPENWEBUI_DB_PASSWORD` | `openwebui` | The password of the Open WebUI vector database. The database has no host port. |
| `SEARXNG_SECRET` | Empty | The SearXNG secret. SearXNG has no host port. |
| `TS_AUTHKEY`, `TS_HOSTNAME` | Empty, `daedalus` | Tailscale auth key and device name |

Provider keys:

| Name | Provider |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_KEY` | Cloudflare Workers AI |
| `GEMINI_API_KEY` | Gemini |
| `GROQ_API_KEY` | Groq |
| `KILO_API_KEY` | Kilo |
| `MISTRAL_API_KEY` | Mistral |
| `OPENROUTER_API_KEY` | OpenRouter |
| `POLLINATIONS_API_KEY` | Pollinations: the secret `sk_` key from <https://enter.pollinations.ai> |
| `ZAI_API_KEY` | Z.ai |

daedalus skips each provider that has no key. A `client_keys` value can read other names, for example `GEMINI_API_KEY_KILO`.

## Router settings

`config/daedalus.yml`. Each key is optional. A missing key uses the default. An unknown key stops the start.

| Key | Default | Use |
| --- | --- | --- |
| `timeouts.request` | `600` | Seconds to wait for an answer, for all attempts. A stream that started does not stop at this limit. |
| `timeouts.wait` | `60` | Seconds with no data from the provider. Keep-alive bytes do not count: SSE comments and blank lines. After this time, the next model starts. Media requests have no wait limit, only `timeouts.request`. |
| `timeouts.slow` | Half of `timeouts.wait` | A first token after this time is slow |
| `session_affinity.enabled` | `true` | Session models and the highest tier of a conversation |
| `session_affinity.idle` | `3600` | Seconds with no request, then the session expires |
| `session_affinity.stay` | `0.85` | Share of first-tier draws for the session model |
| `weights.enabled` | `true` | `false` keeps all weights at 1 |
| `weights.success` | `1.5` | Factor for a success |
| `weights.fault` | `0.5` | Factor for a fault |
| `weights.slow` | `0.75` | Factor for a slow success |
| `weights.hourly` | `1.212` | Recovery factor for each hour |
| `weights.rate_limit` | `0.75` | Factor for an HTTP 429 |
| `cooldown.first` | `60` | Seconds of the first cooldown of a 429 with no reset time |
| `cooldown.longest` | `21600` | Each next 429 doubles the cooldown, up to these seconds |
| `pacing.enabled` | `true` | `false`: the `rpm` and `tpm` of the provider files do not skip models |
| `catalog.every` | `6` | Hours between catalog rebuilds. `0` stops them. |
| `catalog.anchor` | `6` | Local hour that the rebuild times start from. A whole hour from 0 to 23. |
| `headroom.timeout` | `5` | Seconds for the full Headroom answer. Then the original messages go to the provider. |
| `escalation.keywords` | `[]` | Words or phrases. A match in the last user message moves the `daedalus/auto` tier 1 step above the session tier, and the session keeps it. A match is a whole word or phrase, in uppercase or lowercase. The message does not change. The shipped file has a list. |
| `switch.keywords` | `[]` | Words or phrases. A match in the last user message removes the session model of the pool. Another model of the same tier answers, and it becomes the session model. The old model is the last fallback. A match is a whole word or phrase, in uppercase or lowercase. The shipped file has `clanker`. |
| `dashboard.theme` | `system` | `system`, `light` or `dark`. `system` follows the device. The Settings page has a Theme field. The login page always follows the device. |
| `dashboard.time_format` | `24h` | `24h` or `12h`. The clock of each time on the dashboard. The Settings page has a Time format field. |
| `pools.moros`, `pools.koinos`, `pools.deinos`, `pools.sophos`, `pools.graphos`, `pools.photos` | The key | The name after `daedalus/` that clients use for the pool. 1 to 40 letters, digits, dots, dashes or underscores, but not `auto`. Each pool has its own name. After a change, the old name gets HTTP 400 `Unknown provider or pool`. `daedalus/auto` does not change. `/v1/models` and the dashboard show the new names. |

## Provider key values

The shipped provider files use `env:NAME` values. They read environment variables. The dashboard saves pasted values in state and writes `db:NAME` to the provider file.

| Value | Source |
| --- | --- |
| `env:NAME` | Environment variable `NAME` |
| `db:NAME` | Value saved in the dashboard |

Keep the `env:NAME` values in `config/providers/free.yml` to use provider keys from `.env`. Paste a key into **Providers** to save it in the dashboard instead. See [Keys and values](dashboard.md#keys-and-values).

## Provider files

Each top-level key is 1 provider.

```yaml
groq:
  api_key: env:GROQ_API_KEY
  exclude:
    - "*prompt-guard*"
  tier:
    TIER-A:
      - openai/gpt-oss-120b
    TIER-B:
      - openai/gpt-oss-20b
  models:
    openai/gpt-oss-20b: { max_input_tokens: 7000, tpm: 8000 }
    "whisper-*": { mode: audio_transcription }
```

| Key | Use |
| --- | --- |
| `api_key` | Necessary. With no key, the models of the provider leave each chain and pool, and a direct request gets HTTP 400. |
| `client_keys` | Optional. A provider key for each daedalus key name. See [Client keys](#client-keys). |
| `account_id` | Optional. The Cloudflare account ID. The shipped provider file reads `CLOUDFLARE_ACCOUNT_ID`. |
| `api_base` | Optional. Each provider has a default. |
| `api_type` | Optional. `openai` or `gemini`. |
| `discovery_url` | Optional. The model list URL. Each provider has a default. |
| `discovery_match` | Discovery properties that a model must have. A missing property is a match. |
| `exclude` | Patterns of models that never go into the catalog. For the Kilo and OpenRouter exception, see [stealth models](providers.md#catalog). |
| `tier` | Patterns for each tier key: `TIER-A`, `TIER-B`, `TIER-C`, `TIER-D` |
| `models` | Values for each model, for example `max_input_tokens`. These have priority over discovery and LiteLLM. |
| `order` | Optional. A whole number, 1 or more. Empty or not set: 1. A model of a higher order gets a request only when no model of a lower order in its tier answers. The `order` of a `models` entry has priority. See [Order](architecture.md#order). |
| `hooks` | Optional. A list of Python files that change the catalog rows, chat requests and answers of the provider. Each item is a hook point and a file path in the `config` folder, for example `- on-upstream: hooks/x.py`. A `models` entry can also set `hooks`, and it has priority. See [Hooks](hooks.md). |
| `hourly_requests` | Optional. A whole number above 0: the requests to all models of the provider in 1 hour. daedalus counts them, because the provider has no API for the count. At the limit, the provider leaves the chains. See [Pacing](architecture.md#pacing). |

`rpm` and `tpm` limit the requests and the input tokens of a model in 60 s. At a limit, the model leaves the chains. See [Pacing](architecture.md#pacing).

Other keys of a `models` entry:

| Key | Use |
| --- | --- |
| `pool` | `false` keeps the model out of the pools and `daedalus/auto`. Only a direct `provider/slug` request uses it. |
| `timeout` | Seconds with no data from the provider. For this model, it replaces `timeouts.wait`. A direct request tries the same model again after this time, until `timeouts.request`, and then the client gets HTTP 504. |
| `reasoning_effort` | The effort for a request with no `reasoning_effort`. Only a model that reasons gets it. |
| `max_output_tokens` | The output limit of the model. A larger `max_tokens` or `max_completion_tokens` drops to this value. |
| `supports_function_calling`, or its short name `tools` | `true` or `false`. A tool request from a pool or `daedalus/auto` skips each model without a true value. |
| `supports_vision` | `true` or `false`. An image request from a pool or `daedalus/auto` skips each model without a true value. |

When 2 entries match 1 model, the last entry in the file sets the key. A model key at the provider level, for example `reasoning_effort: high` next to `api_key`, sets the value for each model of the provider.

### Client keys

`client_keys` gives a client its own provider key, for example its own Gemini project. The map key is the name of a daedalus key from the [API keys](dashboard.md#api-keys) page.

```yaml
gemini:
  api_key: env:GEMINI_API_KEY # discovery, the master key and the other clients
  client_keys:
    kilo: env:GEMINI_API_KEY_KILO
    owui: env:GEMINI_API_KEY_OWUI
```

| Item | Value |
| --- | --- |
| Client in the map | Its requests to the provider use its key. It has its own cooldowns and its own `rpm` and `tpm` counts. See [Client lanes](architecture.md#client-lanes). |
| Other clients | They use `api_key`, and they share 1 set of cooldowns and counts. |
| Weights, pins, discovery | 1 set for all clients |

To set up 2 clients:

1. On the dashboard **API keys** page, make the keys `kilo` and `owui`. Give each client its key.
2. In `.env`, set `GEMINI_API_KEY_KILO` and `GEMINI_API_KEY_OWUI`. Or save them in **Keys and values** on the Providers page, after step 3.
3. Add `client_keys` to the provider file, or add it in the **Client keys** field of the Providers page.
4. After a `.env` change, run `docker compose up -d`. It makes the container again with the new `.env` values. `docker compose restart` keeps the old values. A saved value needs no restart.

A model with a `mode` other than `chat` never goes into a chat chain. Use `embedding`, `audio_transcription`, `audio_speech` or `image_generation`. A model with no mode goes into the chat chains. The `audio_transcription` models make the transcription pool, and the `image_generation` models make the image pool. Set `supports_vision: true` on an `image_generation` model that edits images: only these models take `POST /v1/images/edits`.

Pattern types:

| Pattern | Example |
| --- | --- |
| Exact name | `openai/gpt-oss-120b` |
| Glob with `*` or `?` | `@cf/meta/llama-3.2-*` |
| Regex with a leading `^` | `^gemini-2\.5-.*` |
| Negation with a leading `!` | `!*-lora` |
| All other models | `"*"` |

When patterns of 2 tiers match 1 model, the most specific pattern sets the tier.

Order of the model values: provider file, then discovery, then LiteLLM.

The tier comes from the model size, the model capabilities, and rankings from benchmarks and from other users. Kilo and OpenRouter models go to `TIER-B` through the `"*"` pattern, because their free models are usually for agentic tasks.
