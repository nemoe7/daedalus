# Configuration

Daedalus reads 3 sources:

| Source | Contents | Edit from the dashboard |
| --- | --- | --- |
| `.env` | Keys and container settings | No |
| `config/daedalus.yml` | Router settings | Yes, on the **Settings** page |
| `config/providers/free.yml` | Providers, tiers and model limits | Yes, on the **Providers** page |

`daedalus.yml` contains the main settings for the router and its jobs. `config/providers/` contains the provider configurations (tiers, models, limits, etc).

## Environment variables

| Name | Default | Use |
| --- | --- | --- |
| `DAEDALUS_MASTER_KEY` | None. Necessary. | Dashboard password and `/v1` key. 16 or more characters, no spaces. |
| `DAEDALUS_HOST` | `0.0.0.0` | Address of the server |
| `DAEDALUS_PORT` | `3357` | Port of the server. `daedalus serve PORT` has priority. |
| `TZ` | UTC | Clock for the catalog schedule, for example `Asia/Manila` |
| `DAEDALUS_UID`, `DAEDALUS_GID` | `1000` | Container user and group |
| `HEADROOM_URL` | `http://headroom:8787` in Compose | Headroom compression. An empty value stops it. |
| `COMPOSE_PROFILES` | Empty | Optional services: `webui`, `headroom`, `tailscale` |
| `OPENWEBUI_API_KEY` | `DAEDALUS_MASTER_KEY` | The key that Open WebUI sends to Daedalus |
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
| `ZAI_API_KEY` | Z.ai |

Daedalus skips each provider that has no key.

## Router settings

`config/daedalus.yml`. Each key is optional. A missing key uses the default. An unknown key stops the start.

| Key | Default | Use |
| --- | --- | --- |
| `timeouts.request` | `600` | Seconds for 1 full request |
| `timeouts.wait` | `60` | Seconds with no bytes from the provider |
| `timeouts.slow` | Half of `timeouts.wait` | A first token after this time is slow |
| `session_affinity.enabled` | `true` | Session models and the highest tier of a conversation |
| `session_affinity.idle` | `3600` | Seconds with no request, then the session expires |
| `session_affinity.stay` | `0.85` | Share of first-tier draws for the session model |
| `weights.enabled` | `true` | `false` keeps all weights at 1 |
| `weights.success` | `1.5` | Factor for a success |
| `weights.fault` | `0.5` | Factor for a fault |
| `weights.slow` | `0.75` | Factor for a slow success |
| `weights.hourly` | `1.212` | Recovery factor for each hour |
| `catalog.every` | `6` | Hours between catalog rebuilds. `0` stops them. |
| `catalog.anchor` | `6` | Local hour that the rebuild times start from. A whole hour from 0 to 23. |
| `headroom.timeout` | `5` | Seconds for Headroom. Then the original messages go to the provider. |

## Provider files

Each top-level key is 1 provider. A value `os.environ/NAME` reads the environment variable `NAME`.

```yaml
groq:
  api_key: os.environ/GROQ_API_KEY
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
| `api_key` | Necessary |
| `api_base` | Optional. Each provider has a default. |
| `api_type` | Optional. `openai` or `gemini`. |
| `discovery_url` | Optional. The model list URL. Each provider has a default. |
| `discovery_match` | Discovery properties that a model must have. A missing property is a match. |
| `exclude` | Patterns of models that never go into the catalog |
| `tier` | Patterns for each tier key: `TIER-A`, `TIER-B`, `TIER-C`, `TIER-D` |
| `models` | Values for each model, for example `max_input_tokens`. These have priority over discovery and LiteLLM. |

A model with a `mode` other than `chat` never goes into a chat chain. Use `embedding`, `audio_transcription`, `audio_speech` or `image_generation`. A model with no mode goes into the chat chains. The `audio_transcription` models make the `daedalus/graphos` pool, and the `image_generation` models make the `daedalus/photos` pool.

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
