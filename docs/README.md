# Docs

Every page, in the order of a first read.

## Start here

| Page | Contents |
| --- | --- |
| [README](../README.md) | What daedalus is, the install, the CLI and the license |
| [Architecture](architecture.md) | Request flow, classification, pools, weights, cooldowns, pacing, session affinity, parallel queries, loops, media pools, the state database and performance |
| [API](api.md) | Access, endpoints, model names, the model list fields, the chat fields and the errors |
| [Configuration](configuration.md) | The environment variables, `config/daedalus.yml` key by key, and the provider files |
| [Providers](providers.md) | The 8 providers, the catalog rules and the media limits |
| [Provider keys](provider-keys.md) | The steps that make a key for each provider |
| [Deployment](deployment.md) | Docker Compose, the image release, the optional services, Open WebUI, Tika, SearXNG, Headroom and Tailscale |
| [Dashboard](dashboard.md) | The 7 pages, the requests view and the API keys |

## Hooks

| Page | Contents |
| --- | --- |
| [Hooks](hooks.md) | The 8 hook points, the HTTP surface, the errors, the logs, the remote files and the shipped files |
| [Auto reasoning effort](hooks/owui_auto_reasoning_effort.md) | The reasoning level of a turn, and the try-again rule |
| [The served model line](hooks/served_model.md) | The model that served a chat pool request |
| [The OpenRouter endpoint order](hooks/or_cheapest_output.md) | The endpoint order of 1 OpenRouter model, by output price |

## Integrations

| Page | Contents |
| --- | --- |
| [Open WebUI](integrations/owui.md) | The filters, the tools and the skill of Open WebUI |
| [Kilo Code](integrations/kilo.md) | The Kilo Code plugin and its model fields |
| [Deep research](integrations/owui/deep-research.md) | The research skill over `search_web` and `fetch_url` |
| [Served model](integrations/owui/served_model.md) | The filter that draws the served model line |
| [Chat metadata](integrations/owui/chat_metadata.md) | The filter that adds the date, the clock, the timezone, the place and the language |
| [GitHub](integrations/owui/github.md) | The GitHub reads and writes, behind the permission gate |
| [Google](integrations/owui/google.md) | Gmail, Calendar, Drive and Docs, behind the permission gate |
| [Open WebUI manager](integrations/owui/owui_manager.md) | The workspace: knowledge, skills, files, tools and functions |

## Decisions

| Page | Contents |
| --- | --- |
| [1. Pools are the gateways to the tiers](adr/0001-pools.md) | The 4 pool names, the tier patterns and the order |
| [2. A tool request or an image request skips models that cannot take it](adr/0002-tools.md) | The capability skip and its silent fault |
| [3. Errors reroute to the next fallback](adr/0003-errors.md) | The reroute rules and the wait limits |
| [4. Penalties and session affinity order each chain](adr/0004-penalties.md) | The weights, the rate limits, the pacing, the order, the pin and the loops |
| [5. Parallel queries race for the session pin](adr/0005-parallel-queries.md) | The race keys and the winner |
