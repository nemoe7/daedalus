# Daedalus

One of them is bound to hit. Daedalus puts your personal API keys behind 1 endpoint and uses a classifier to select a model for each task.

The name must have the same impact as [Odysseus](https://odysseusai.dev/). It must be Greek and easy to recognize. The idea comes from the Terraria weapon [Daedalus Stormbow](https://terraria.wiki.gg/wiki/Daedalus_Stormbow). Each arrow is a request to a provider, and the bow shoots until 1 arrow hits.

## Features

- 1 OpenAI-compatible endpoint for 8 providers: Cloudflare, Gemini, Groq, Kilo, Mistral, OpenRouter, Pollinations and Z.ai.
- Each provider gets its native API. Gemini gets the native Gemini API.
- 4 pools (tiers) and `daedalus/auto`, which selects a tier from the prompt. A keyword, such as "think hard", moves the tier 1 step up.
- A fallback ladder: when a model fails, the next model gets the request.
- Model weights, session affinity, a time-to-first-token penalty and a loop penalty.
- A model catalog from provider discovery and LiteLLM data, rebuilt on a schedule.
- Endpoints for embeddings, transcriptions, speech and images.
- A dashboard for pools, requests, models, API keys, providers and settings.
- Docker Compose, with optional Open WebUI, Tika, SearXNG, Headroom and Tailscale.

Daedalus is for personal use only. Do not share it with other users, because the provider terms of service can forbid it.

## Quick start

1. Install Docker with Compose v2.
2. Copy `.env.example` to `.env`.

   cmd:

   ```cmd
   copy .env.example .env
   ```

   PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

   bash:

   ```bash
   cp .env.example .env
   ```

3. In `.env`, set `DAEDALUS_MASTER_KEY` (16 or more characters, no spaces) and the provider keys that you have.
4. Run the install script. It pulls the image, or builds it when the pull fails, and starts the container.

   cmd:

   ```cmd
   install.cmd
   ```

   PowerShell:

   ```powershell
   .\install.ps1
   ```

   bash:

   ```bash
   ./install.sh
   ```

5. Open the dashboard at `http://localhost:3357/`. The user is `DAEDALUS_USERNAME`, else `admin`. The password is `DAEDALUS_PASSWORD`, else `DAEDALUS_MASTER_KEY`.
6. On the **API keys** page, make a key for each client.

| Client setting | Value |
| --- | --- |
| Base URL | `http://localhost:3357/v1` |
| API key | A key from the **API keys** page |
| Model | `daedalus/auto`, a pool name, or `provider/slug` |

The tested clients are Kilo Code and Open WebUI.

## Run without Docker

Install [uv](https://docs.astral.sh/uv/getting-started/installation/).

cmd or PowerShell:

```cmd
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

bash:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install Daedalus with the versions in `uv.lock`, then start the router. The commands are the same in all 3 shells:

```sh
uv sync
uv run daedalus serve
```

Put `uv run` before each command in the table. After `git pull`, run `uv sync` again. After a dependency change in `pyproject.toml`, run `uv lock`.

| Command | What it does |
| --- | --- |
| `daedalus serve [PORT] [--catalog]` | Starts the router on `0.0.0.0:PORT` (default 3357). `--catalog` rebuilds the model store first. |
| `daedalus catalog` | Discovers the provider models and rebuilds the model store. |
| `daedalus dump` | Writes the raw model list of each provider to `.daedalus-state/dump`. |

## Development

`uv sync` also installs the development tools: pytest, pytest-xdist and Ruff. CI runs these commands for each code change:

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
```

- `uv run pytest` runs the test files in parallel, with 1 worker for each CPU. `uv run pytest -n 0` runs them in 1 process.
- Each test file gets a temporary state folder. The tests do not change `.daedalus-state`.

## Documentation

| Page | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Request flow, classification, fallback ladder, weights, session affinity and loops |
| [API](docs/api.md) | Endpoints, model names, access and errors |
| [Configuration](docs/configuration.md) | Environment variables, `config/daedalus.yml` and provider files |
| [Providers](docs/providers.md) | Supported providers and what each one can do |
| [Deployment](docs/deployment.md) | Docker Compose, profiles, Open WebUI, Tika, SearXNG, Headroom and Tailscale |
| [Dashboard](docs/dashboard.md) | Pages and API keys |
| [Decisions](docs/adr/) | Architecture decision records |

## License

Daedalus has no license for now.

The classifier data comes from LiteLLM. Its license is in `daedalus/routing/artifacts/LITELLM-LICENSE.txt`.
