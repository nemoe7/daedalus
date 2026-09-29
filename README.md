# Daedalus

<!-- markdownlint-disable MD033 -->
<p align="center"><img src="daedalus/dashboard/ui/logo.svg" alt="Daedalus: a D-shaped bow that shoots 3 arrows" width="176"></p>
<p align="center"><em>“One of them is bound to hit.”</em></p>
<!-- markdownlint-enable MD033 -->

Daedalus puts your personal API keys behind 1 endpoint and uses a classifier to select a model for each task.

The name must have the same impact as [Odysseus](https://odysseusai.dev/). It must be Greek and easy to recognize. The idea comes from the Terraria weapon [Daedalus Stormbow](https://terraria.wiki.gg/wiki/Daedalus_Stormbow). Each arrow is a request to a provider, and the bow shoots until 1 arrow hits.

## Why Daedalus

[LiteLLM](https://github.com/BerriAI/litellm) is a good router, but it is heavy for a Raspberry Pi, and it has many features that 1 user does not need. Personal API keys are not for sharing, so Daedalus is for 1 user. Daedalus keeps the LiteLLM classifier and model data, and adds these routing features:

- [Loop fallback](docs/architecture.md#loops): a model that sends the same tool call 3 times gets a fault. The same thinking passage 4 times also gives a fault. Then the next model continues.
- A [fallback ladder](docs/architecture.md#pools-and-the-fallback-ladder) through the tiers: up first, then down.
- [Weights](docs/architecture.md#weights) that change after each request. A weight goes up after an answer and down after a fault, a slow answer or a rate limit. It also goes up with time.
- [Session affinity](docs/architecture.md#session-affinity): a conversation keeps its model, and a failure removes the pin.
- A conversation tier that does not go down, and keywords, such as "think hard", that move it 1 tier up.
- A [try again](docs/architecture.md#try-again) in Open WebUI moves the message 1 tier up.
- Before a request, Daedalus skips the models without tools, without vision or with a too-small context window.
- A model catalog from provider discovery, rebuilt on a schedule, in place of a hand-written model list.

## Features

- 1 OpenAI-compatible endpoint for 8 providers: Cloudflare, Gemini, Groq, Kilo, Mistral, OpenRouter, Pollinations and Z.ai.
- Each provider gets its native API. Gemini gets the native Gemini API.
- 4 pools (tiers) and `daedalus/auto`, which selects a tier from the prompt with the [LiteLLM](https://github.com/BerriAI/litellm) [AutoRouter heuristic v2](https://docs.litellm.ai/blog/heuristic-v2) classifier. A keyword, such as "think hard", moves the tier 1 step up.
- A fallback ladder: when a model fails, the next model gets the request.
- Weighted selection: each model has a weight that goes up when the model answers and down when it fails.
- An [order](docs/architecture.md#order) for each provider or model. Inside a tier, the models of order 1 go first. A model of order 2 gets a request only when no model of order 1 answers.
- Session affinity, a time-to-first-token penalty and a loop penalty.
- A model catalog from provider discovery and LiteLLM data, rebuilt on a schedule.
- Endpoints for embeddings, transcriptions, speech and images.
- A dashboard for pools, requests, models, API keys, providers and settings.
- Docker Compose, with optional Open WebUI, Tika, SearXNG, Headroom and Tailscale.

Daedalus is for personal use only. Do not share it with other users, because the provider terms of service can forbid it.

## Quick start

1. Run the install command. First, it shows what it installs or downloads and asks `[y/N]`. Then it installs Docker if Docker is missing. It downloads the Daedalus files to the `daedalus` folder in your home folder and makes `.env` with a new master key. Then it starts Daedalus and shows the master key.

   cmd or PowerShell:

   ```cmd
   powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/nemoe7/daedalus/main/install.ps1 | iex"
   ```

   bash (Linux or Raspberry Pi):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/nemoe7/daedalus/main/install.sh | bash
   ```

   On Windows, winget installs Docker Desktop. Restart Windows, start Docker Desktop once, then run the command again. On Linux, the official script `get.docker.com` installs Docker.

2. Add the provider keys that you have to `.env` in the `daedalus` folder. Then run the install script in that folder again: `install.cmd`, `.\install.ps1` or `./install.sh`.

   In a git checkout, the install scripts use the checkout folder. To build the image from the source, add `--dev`, for example `install.cmd --dev`. The script then uses `compose.dev.yml`.

3. Open the dashboard at `http://localhost:3357/`. The user is `DAEDALUS_USERNAME`, else `admin`. The password is `DAEDALUS_PASSWORD`, else `DAEDALUS_MASTER_KEY`.
4. On the **API keys** page, make a key for each client.

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
| `daedalus dump` | Writes the raw model list of each provider to `.daedalus-state/dump/PROVIDER.json`. |

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

Daedalus uses the [Daedalus Noncommercial License 1.0.0](LICENSE.md). You can use, change and share it for personal and other noncommercial purposes. Shared changes use the same terms and come with their source code. Commercial use needs a separate license from the owner.

The classifier and its data come from [LiteLLM](https://github.com/BerriAI/litellm) (MIT). Its license is in `daedalus/routing/artifacts/LITELLM-LICENSE.txt`.
