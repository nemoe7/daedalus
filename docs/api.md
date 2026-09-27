# API

Daedalus serves the OpenAI API on `http://HOST:3357/v1`.

> Q: Which OpenAI SDK or client do you test with?

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
| `GET /v1/models` | `daedalus/auto`, the 4 pools, the chat models, then the other catalog models | None |
| `POST /v1/chat/completions` | `daedalus/auto`, a pool, or `provider/slug` | Yes, for `daedalus/auto` and pools |
| `POST /v1/embeddings` | `provider/slug` | No |
| `POST /v1/audio/transcriptions` | `provider/slug` | No |
| `POST /v1/audio/speech` | `provider/slug` | No |
| `POST /v1/images/generations` | `provider/slug` | No |

The endpoints for models that do not chat have no fallback. Vectors, voices and images from 2 models are different.

> Q: Do you plan more endpoints, for example `/v1/responses`?

## Model names

| Name | Result |
| --- | --- |
| `daedalus/auto` | The classifier selects the tier. See [Architecture](architecture.md#classification). |
| `daedalus/moros`, `daedalus/koinos`, `daedalus/deinos`, `daedalus/sophos` | The fallback ladder from that tier |
| `provider/slug`, for example `groq/llama-3.3-70b-versatile` | That model only |

## Chat completions

| Field | Rule |
| --- | --- |
| `messages` | Necessary. A list of objects. |
| `stream` | Boolean. |
| `stream_options` | Object. `include_usage` adds a usage chunk. |
| `tools` | Models that cannot call tools leave the chain. |
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
| 400 | Invalid request, unknown model, or `context_length_exceeded` |
| 401 | Missing or wrong key |
| 4xx or 5xx from the provider | The last model failed with this status |
| 502 | No model answered, or the provider answer was not valid |

The dashboard **Requests** page shows the full provider error of each attempt. Daedalus removes the prompt text from provider errors.

> Q: What should a client do when it gets a 502?
