# Daedalus

One of them is bound to hit. Aggregates personal API keys and utilizes a classifier to select a model that suits the task.

I got the name "Daedalus" since I wanted something that had the same impact as "Odyssey" [link to odyssey-dev]. It has to be Greek and recognizable. I took inspiration from the Terraria weapon "Daedalus Stormbow" and thought of each arrow being a request sent to a provider that just keeps trying to hit until one lands.

## Features

- 1 OpenAI-compatible endpoint for 7 providers: Cloudflare, Gemini, Groq, Kilo, Mistral, OpenRouter and Z.ai.
- Each provider gets its native API. Gemini gets the native Gemini API.
- 4 pools (tiers) and `daedalus/auto`, which selects a tier from the prompt.
- A fallback ladder: when a model fails, the next model gets the request.
- Model weights, session affinity and a time-to-first-token penalty.
- A model catalog from provider discovery and LiteLLM data, rebuilt on a schedule.
- Endpoints for embeddings, transcriptions, speech and images.
- A dashboard for pools, requests, models, API keys, providers and settings.
- Docker Compose, with optional Open WebUI, Headroom and Tailscale.

Daedalus is meant for personal use only. It is not meant to be shared to other users due to providers' terms of service.

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
4. Run the install script. It builds the image and starts the container.

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

5. Open the dashboard at `http://localhost:3357/`. The user is `admin`, and the password is `DAEDALUS_MASTER_KEY`.
6. On the **API keys** page, make a key for each client.

| Client setting | Value |
| --- | --- |
| Base URL | `http://localhost:3357/v1` |
| API key | A key from the **API keys** page |
| Model | `daedalus/auto`, a pool name, or `provider/slug` |

> Q: Which clients do you use with Daedalus, and do you want a setup example for each?

## Run without Docker

cmd or PowerShell:

```cmd
py -m pip install -e .
```

bash:

```bash
python3 -m pip install -e .
```

Then start the router. The command is the same in all 3 shells:

```sh
daedalus serve
```

| Command | What it does |
| --- | --- |
| `daedalus serve [PORT] [--catalog]` | Starts the router on `0.0.0.0:PORT` (default 3357). `--catalog` rebuilds the model store first. |
| `daedalus catalog` | Discovers the provider models and rebuilds the model store. |
| `daedalus dump` | Writes the raw model list of each provider to `.daedalus-state/dump`. |

## Documentation

| Page | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Request flow, classification, fallback ladder, weights and session affinity |
| [API](docs/api.md) | Endpoints, model names, access and errors |
| [Configuration](docs/configuration.md) | Environment variables, `config/daedalus.yml` and provider files |
| [Providers](docs/providers.md) | Supported providers and what each one can do |
| [Deployment](docs/deployment.md) | Docker Compose, profiles, Open WebUI, Headroom and Tailscale |
| [Dashboard](docs/dashboard.md) | Pages and API keys |
| [Decisions](docs/adr/) | Architecture decision records |

## License

> Q: Which license does Daedalus use?

The classifier data comes from LiteLLM. Its license is in `daedalus/routing/artifacts/LITELLM-LICENSE.txt`.
