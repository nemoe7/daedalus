# API

Daedalus serves the OpenAI API on `http://HOST:3357/v1`.

The tested clients are Kilo Code and Open WebUI.

Kilo Code: so far, the best agentic harness for Daedalus. It has fine-grained tool access, a plugin system, and more.

Open WebUI: a general chat interface. A custom interface without the unused features is possible, but Open WebUI works well so far.

## Access

| Key | Opens |
| --- | --- |
| `DAEDALUS_MASTER_KEY` | `/v1` and the dashboard |
| API key from the dashboard (`sk-` and 43 characters) | `/v1` only |

Send the key as `Authorization: Bearer KEY`. The log shows `key=NAME` or `key=master`.

Example: the model list.

cmd:

```cmd
curl http://localhost:3357/v1/models -H "Authorization: Bearer %DAEDALUS_KEY%"
```

PowerShell:

```powershell
curl.exe http://localhost:3357/v1/models -H "Authorization: Bearer $env:DAEDALUS_KEY"
```

bash:

```bash
curl http://localhost:3357/v1/models -H "Authorization: Bearer $DAEDALUS_KEY"
```

## Endpoints

| Method and path | Models | Fallback |
| --- | --- | --- |
| `GET /health` | None. No key is necessary. | None |
| `GET /v1/models` | `daedalus/auto`, the 4 pools, the chat models, the media pools that have models, then the other catalog models | None |
| `POST /v1/chat/completions` | `daedalus/auto`, a pool, or `provider/slug` | Yes, for `daedalus/auto` and pools |
| `POST /v1/embeddings` | `provider/slug` | No |
| `POST /v1/audio/transcriptions` | `daedalus/graphos` or `provider/slug` | Yes, for `daedalus/graphos` |
| `POST /v1/audio/speech` | `provider/slug` | No |
| `POST /v1/images/generations` | `daedalus/photos` or `provider/slug` | Yes, for `daedalus/photos` |

Embeddings and speech have no pool and no fallback. Vectors and voices from 2 models are different. See [Architecture](architecture.md#media-pools) for the media pools.

> Q: Do you plan more endpoints, for example `/v1/responses`?

## Model names

| Name | Result |
| --- | --- |
| `daedalus/auto` | The classifier selects the tier. See [Architecture](architecture.md#classification). |
| `daedalus/moros`, `daedalus/koinos`, `daedalus/deinos`, `daedalus/sophos` | The fallback ladder from that tier |
| `daedalus/graphos` | The transcription pool, for `POST /v1/audio/transcriptions` only |
| `daedalus/photos` | The image pool, for `POST /v1/images/generations` only |
| `provider/slug`, for example `groq/llama-3.3-70b-versatile` | That model only |

## Model list fields

Each chat model and pool model in `GET /v1/models` has these catalog fields, when the catalog has a value:

| Field | Chat model | Pool model |
| --- | --- | --- |
| `max_input_tokens` | Catalog value | Highest value in the pool |
| `max_output_tokens` | Catalog value | Highest value in the pool |
| `supports_function_calling` | Catalog value | `true` if 1 model in the pool is `true` |
| `supports_reasoning` | Catalog value | `true` if 1 model in the pool is `true` |
| `supports_vision` | Catalog value | `true` if 1 model in the pool is `true` |

`daedalus/auto` has the values of `daedalus/sophos`. A request that is too large for a model skips that model.

## Kilo Code plugin

Kilo Code reads token limits only from its config. The plugin `integrations/kilo/daedalus.js` copies the model list fields into the Kilo config in memory. It runs when Kilo starts and after each config change. The config file does not change.

| Kilo model field | Value |
| --- | --- |
| `limit.context` | `max_input_tokens` |
| `limit.output` | `0`: Kilo uses its default |
| `tool_call` | `supports_function_calling` |
| `reasoning` | `supports_reasoning` |
| `modalities.input`, `attachment` | `["text", "image"]` and `true`, when `supports_vision` is `true` |

The plugin changes each provider that has the id `daedalus` or a `daedalus/` model. It uses the `baseURL` of the provider. The key comes from the first of these:

1. `options.apiKey` of the provider
2. The Kilo auth store: the key from the custom provider dialog
3. The `DAEDALUS_API_KEY` variable

When Daedalus does not answer in 3 s, the plugin logs `[daedalus] ... fail open` and changes nothing.

Install: copy the file into the Kilo plugin folder, then restart Kilo.

cmd:

```cmd
mkdir "%USERPROFILE%\.config\kilo\plugin"
copy integrations\kilo\daedalus.js "%USERPROFILE%\.config\kilo\plugin\"
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.config\kilo\plugin"
Copy-Item integrations\kilo\daedalus.js "$HOME\.config\kilo\plugin\"
```

bash:

```bash
mkdir -p ~/.config/kilo/plugin
cp integrations/kilo/daedalus.js ~/.config/kilo/plugin/
```

Kilo cannot show the routed model for a custom provider. The **Requests** page and the log show it.

## Chat completions

| Field | Rule |
| --- | --- |
| `messages` | Necessary. A list of objects. |
| `stream` | Boolean. |
| `stream_options` | Object. `include_usage` adds a usage chunk. Daedalus asks Groq and OpenRouter for the usage chunk in each stream, and removes it when the client did not ask for it. |
| `tools` | Models that cannot call tools leave the chain. |
| `messages` with an `image_url` part | Models with `supports_vision` false or with no value leave the chain of a pool or `daedalus/auto`. A provider file can set `supports_vision: true` for a model. An image in an older message also counts. A direct `provider/slug` request does not change. |
| `max_tokens`, `max_completion_tokens` | A value above the `max_output_tokens` of the model in the catalog drops to that value. |
| `reasoning_effort` | Goes only to a model with a true `supports_reasoning` value in the catalog. A model that is not in the catalog also gets it. When a request has no value, a model with a `reasoning_effort` in the catalog gets that value. |
| Other fields | Go to the provider. Mistral and Groq get only the message fields that they accept. |

## Endpoints for models that do not chat

| Endpoint | Input | What Daedalus changes |
| --- | --- | --- |
| Embeddings | JSON: `input`, `dimensions`, `encoding_format` | The provider always sends float vectors. Daedalus makes the base64 form. |
| Transcriptions | Multipart form: `file`, `language`, `prompt`, `response_format`, `temperature`, `timestamp_granularities[]` | Each provider gets only the fields that it accepts. |
| Speech | JSON: `input`, `voice`, `instructions`, `response_format`, `speed` | The audio comes back with the provider media type. |
| Images | JSON: `prompt`, `n`, `size`, `quality`, `style`, `response_format` | A native provider image comes back as a `data:` URL, or as `b64_json`. |

See [Providers](providers.md#endpoints-for-models-that-do-not-chat) for the providers of each endpoint.

## Errors

Errors use the OpenAI shape:

```json
{"error": {"message": "No model answered the request", "type": "upstream_error", "code": 502}}
```

| Status | Cause |
| --- | --- |
| 400 | Invalid request, unknown model, `context_length_exceeded`, or a direct request to a provider with no `api_key` |
| 401 | Missing or wrong key |
| 429 `rate_limit_exceeded` | The model, or each model of the chain, is in a cooldown or at its `rpm` or `tpm`. `Retry-After` gives the seconds. See [Architecture](architecture.md#cooldowns) and [Pacing](architecture.md#pacing). |
| 4xx or 5xx from the provider | The last model failed with this status |
| 502 | No model answered, or the provider answer was not valid |
| 504 | A direct `provider/slug` request to a model with a `timeout` value got no answer until `timeouts.request`. See [Configuration](configuration.md#provider-files). |

The dashboard **Requests** page shows the full provider error of each attempt. Daedalus removes the prompt text from provider errors.

A 502 means that the last model in the chain failed with a network error or a timeout. The client can only send the request again later. When the last model answers with a status such as 429, the client gets that status. Then wait until your provider quotas reset.
