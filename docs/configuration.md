# Configuration

daedalus reads its settings from these sources:

| Source | Contents | Edit from the dashboard |
| --- | --- | --- |
| `.env` | Keys and container settings | No |
| `config/daedalus.yml` | Router settings | Yes, on the **Settings** page |
| `config/providers/free.yml` | Providers, tiers and model limits | Yes, on the **Providers** page |
| `config/providers/{provider}.yml` | 1 provider, on its own | Yes, on the **Providers** page |

`daedalus.yml` contains the main settings for the router and its jobs. `config/providers/` contains the provider configurations (tiers, models, limits, etc).

A key that shows 2 times in 1 map of a config file stops the start. The error gives the key and the 2 line numbers.

At each catalog rebuild, daedalus reads the default provider file of the repository,
`config/providers/free.yml` on `main`, and adds each provider that the local file lacks. The
local file wins whole, so a local provider block, a changed value and a removed provider stay.
The read never writes `config/providers/free.yml`: the copy lands in `.daedalus-state/free.defaults.yml`.
With no network or a bad file, the copy of the last good read stands, and daedalus logs 1 line.

daedalus reads the files at the start and on each save from the dashboard. A hand edit of a file on disk needs a reload. A save from the dashboard reloads the file for the live server and rebuilds the catalog from it. With no save, run `daedalus catalog`: the command reads the files again for its store build.

The next start of the server reads the edited file.

## Files per provider

`free.yml` holds each provider. A second file, `{provider}.yml`, holds 1 provider with its own settings. `openrouter.yml` and the `openrouter` block of `free.yml` are independent: the tiers, the excludes and the models of 1 file do not apply to the other. `pollinations.yml` holds Pollinations, with no block in `free.yml`, because Pollinations spends a balance that does not refill ([pollinations#11580](https://github.com/pollinations/pollinations/issues/11580)).

| Item | Rule |
| --- | --- |
| Name | The file name is the provider name: `openrouter.yml` holds `openrouter`. |
| Provider key | A file with no provider key, like `api_key` or `models`, is not a provider file. |
| Discovery | Each file discovers the models of its provider with its own settings. |
| Models | A file keeps only the models of its `models` keys. `"*"` keeps each model. |
| Values | A kept model takes its values from its file only, not `free.yml`. |
| New file | The **Providers** page makes a file. An unknown name also gets an empty `api_base`. |

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
| `COMPOSE_PROFILES` | Empty | Optional services: `webui`, `tika`, `search`, `headroom`, `tailscale`, `tailscale-openwebui` |
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

The 2 old groups, `session_affinity` and `parallel`, stop it too. The error names the `affinity.mode` that replaces each one.

The shipped file holds the changes from the defaults only. The **Settings** page writes the changed keys only. A cleared field loses its line, and the default applies. The table below lists each key, its default and its use.

| Key | Default | Use |
| --- | --- | --- |
| `timeouts.request` | `600` | Seconds to wait for an answer, for all attempts. A started stream does not stop. |
| `timeouts.wait` | `60` | Seconds with no provider data, keep-alive bytes excluded. Then the next model starts. Media: none. |
| `timeouts.slow` | `30` | A first token after this time is slow |
| `affinity.mode` | `session` | `none`: no pin and no race. `session`: each conversation stays on 1 model. `race`: the next models also race the first content |
| `affinity.change_on_draw` | `true` | Replace an eligible session pin after a different weighted first-tier draw. Under `race` the pinned model leads, so no draw happens |
| `affinity.idle` | `3600` | Seconds with no request, then the session expires |
| `affinity.stay` | `0.85` | Share of first-tier draws for the session model. Under `race` the pinned model leads the tier, so the share does not apply |
| `affinity.count` | `1` | Models that race the original one. Integer from 1 to 10 |
| `affinity.chance` | `0.05` | Chance to start the racing models with the original one. Number from 0 to 1 |
| `affinity.slow` | `30` | Seconds with no content from the first model. Then the racing models start |
| `affinity.penalty` | `0.9` | Weight factor for the model that loses the race. At most 1 |
| `weights.enabled` | `true` | `false` keeps all weights at 1 |
| `weights.success` | `1.5` | Factor for a success |
| `weights.fault` | `0.5` | Factor for a fault |
| `weights.slow` | `0.75` | Factor for a slow success |
| `weights.hourly` | `1.212` | Recovery factor for each hour |
| `weights.rate_limit` | `0.75` | Factor for an HTTP 429 |
| `cooldown.first` | `60` | Seconds of the first cooldown of a 429 with no reset time |
| `cooldown.longest` | `21600` | Each next 429 doubles the cooldown, up to these seconds |
| `loops.calls` | `3` | Repeated identical tool calls since the last user message. Integer from 2 to 100. |
| `loops.repeats` | `4` | Consecutive copies of a repeated text passage. Integer from 2 to 16. |
| `loops.shortest` | `20` | Shortest passage period, in characters. Integer from 1 to 1,000 and no greater than `loops.longest`. |
| `loops.longest` | `2000` | Longest passage period, in characters. Integer from 1 to 10,000. |
| `pacing.enabled` | `true` | `false`: the `rpm` and `tpm` of the provider files do not skip models |
| `catalog.every` | `6` | Hours between catalog rebuilds. `0` stops them. |
| `catalog.anchor` | `6` | Local hour that the rebuild times start from. A whole hour from 0 to 23. |
| `headroom.enabled` | `true` | `false`: the messages of each model go to the provider unchanged. |
| `headroom.timeout` | `5` | Seconds for the full Headroom answer. Then the original messages go to the provider. |
| `escalation.keywords` | `ultrathink`, `think hard`, `think harder`, `think deeply`, `think longer`, `root cause`, `race condition`, `memory leak`, `deadlock`, `security review`, `performance regression`, `audit`, `refactor`, `investigate`, `diagnose`, `code review`, `system design`, `optimize`, `debug`, `design`, `architecture` | Whole words or phrases. A match moves the `daedalus/auto` tier 1 step up, session-kept. |
| `switch.keywords` | `clanker` | Words or phrases. A match gives the pool session another model of the same tier. |
| `dashboard.theme` | `system` | `system`, `light` or `dark`. The Settings page sets it. Login always follows the device. |
| `dashboard.time_format` | `24h` | `24h` or `12h` for the hour of each shown time. Every time carries its date: `2026-10-04 12:30:46`. The Settings page sets it. |
| `request_hooks.on-request` | `[hooks/openwebui_retry.py]` | The request hook files of the `config` folder, in list order. An empty list: no hook. See [Hooks](hooks.md). |
| `request_hooks.on-chunk` | `[hooks/pick.py]` | The stream chunk hook files of the `config` folder, called on each streamed chunk of a chat request, in list order. An empty list: no hook. See [Hooks](hooks.md). |
| `pools.tier-a`, `pools.tier-b`, `pools.tier-c`, `pools.tier-d`, `pools.audio`, `pools.images` | `sophos`, `deinos`, `koinos`, `moros`, `graphos`, `photos` | The client name after `daedalus/`: 1-40 characters, no `auto`. A change renames that pool, and the old name breaks with HTTP 400. |

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
| `api_key` | Necessary. With no key, the provider models leave the chains and pools. |
| `client_keys` | Optional. A provider key for each daedalus key name. See [Client keys](#client-keys). |
| `account_id` | Optional. The Cloudflare account ID. The shipped provider file reads `CLOUDFLARE_ACCOUNT_ID`. |
| `api_base` | Optional. Each provider has a default. |
| `api_type` | Optional. `openai` or `gemini`. |
| `discovery_url` | Optional. The model list URL. Each provider has a default. |
| `discovery_match` | Discovery properties that a model must have. A missing property is a match. |
| `exclude` | Patterns of models that never enter the catalog. The stealth exception: [Catalog](providers.md#catalog). |
| `tier` | Patterns for each tier key: `TIER-A`, `TIER-B`, `TIER-C`, `TIER-D` |
| `models` | Values for each model, for example `max_input_tokens`. These have priority over discovery and LiteLLM. |
| `order` | Optional. 1 or more, default 1. Runs after the lower orders of its tier. |
| `hooks` | Optional. Hook points and file paths in `config`, like `- on-upstream: hooks/x.py`. |
| `streams` | Optional. `false`: the block answers no stream. The client waits for the whole body. Absent: true. |
| `headroom` | Optional. `false`: no Headroom compression for the models of the block. Absent: `headroom.enabled`. |
| `hourly_requests` | Optional. The provider requests per hour. At the limit, the provider leaves the chains. |

`rpm` and `tpm` limit the requests and the input tokens of a model in 60 s. At a limit, the model leaves the chains. See [Pacing](architecture.md#pacing).

Other keys of a `models` entry:

| Key | Use |
| --- | --- |
| `pool` | `false`: out of the pools and `daedalus/auto`. A direct `provider/slug` request still uses it. |
| `timeout` | Seconds with no provider data, in place of `timeouts.wait`. Direct requests retry until `timeouts.request`. |
| `reasoning_effort` | The effort for a request with no `reasoning_effort`. Only a model that reasons gets it. |
| `max_output_tokens` | The output limit of the model. A larger `max_tokens` or `max_completion_tokens` drops to this value. |
| `supports_function_calling`, or its short name `tools` | `true` or `false`. Pool and `daedalus/auto` tool requests skip a model without it. |
| `supports_vision` | `true` or `false`. Pool and `daedalus/auto` image requests skip a model without it. |
| `streams` | `false`: the model answers no stream. A request that wanted one retries without it. Absent: true. |
| `headroom` | `false`: the model keeps its messages. Absent: the value of the block, then `headroom.enabled`. |

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
| Client in the map | Its requests use its key, its cooldowns, `rpm` and `tpm`. See [Client lanes](architecture.md#client-lanes). |
| Other clients | They use `api_key`, and they share 1 set of cooldowns and counts. |
| Weights, pins, discovery | 1 set for all clients |

To set up 2 clients:

1. On the dashboard **API keys** page, make the keys `kilo` and `owui`. Give each client its key.
2. In `.env`, set `GEMINI_API_KEY_KILO` and `GEMINI_API_KEY_OWUI`. Or save them in **Keys and values** on the Providers page, after step 3.
3. Add `client_keys` to the provider file, or add it in the **Client keys** field of the Providers page.

4. After a `.env` change, run `docker compose up -d`. It makes the container again with the new `.env` values. `docker compose restart` keeps the old values.

A saved value needs no restart.

A model with a `mode` other than `chat` never goes into a chat chain. Use `embedding`, `audio_transcription`, `audio_speech` or `image_generation`. A model with no mode goes into the chat chains. The `audio_transcription` models make the transcription pool, and the `image_generation` models make the image pool.

Set `supports_vision: true` on an `image_generation` model that edits images: only these models take `POST /v1/images/edits`.

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
