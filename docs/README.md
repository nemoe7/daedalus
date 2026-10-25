# Docs

Every page, in the order of a first read.

## Start here

| Page | Contents |
| --- | --- |
| [README](../README.md) | What daedalus is, the install, CLI, and license |
| [Architecture](architecture.md) | Request flow, classification, pools, weights, cooldowns, pacing, session affinity, parallel queries, loops, media pools, the state database, and performance |
| [API](api.md) | Access, endpoints, model names, the model list fields, chat fields, and errors |
| [Configuration](configuration.md) | The environment variables, `config/daedalus.yml` key by key, and the provider files |
| [Providers](providers.md) | The 8 providers, catalog rules, and media limits |
| [Provider keys](provider-keys.md) | The steps that make a key for each provider |
| [Deployment](deployment.md) | Docker Compose, the image release, the optional services, Open WebUI, Tika, SearXNG, Headroom, and Tailscale |
| [Dashboard](dashboard.md) | The 7 pages, requests view, and API keys |
| [Design](design.md) | The dashboard design system, color tokens, typography, layout grids, components, and visual reference boards |

## Hooks

| Page | Contents |
| --- | --- |
| [Hooks](hooks.md) | The 8 hook surfaces, HTTP surface, errors, logs, remote files, and shipped files |
| [Auto reasoning effort](hooks/owui_auto_reasoning_effort.md) | The reasoning level of a turn, and the try-again rule |
| [The served model line](hooks/served_model.md) | The model that served a chat pool request |
| [The OpenRouter endpoint order](hooks/or_cheapest_output.md) | The endpoint order of 1 OpenRouter model, by output price |

## Integrations

| Page | Contents |
| --- | --- |
| [Open WebUI](integrations/owui.md) | The filters, tools, and skill of Open WebUI |
| [Kilo Code](integrations/kilo.md) | The Kilo Code plugin and its model fields |
| [Deep research](integrations/owui/deep-research.md) | A portable research plan with source links |
| [Served model](integrations/owui/served_model.md) | The filter that draws the served model line |
| [Chat metadata](integrations/owui/chat_metadata.md) | The filter that adds the date, clock, timezone, place, and language |
| [GitHub](integrations/owui/github.md) | The GitHub reads and writes, behind the permission gate |
| [Google](integrations/owui/google.md) | Gmail, Calendar, Drive, and Docs, behind the permission gate |
| [Open WebUI manager](integrations/owui/owui_manager.md) | The workspace: knowledge, skills, files, tools, and functions |

## Decisions

| Page | Contents |
| --- | --- |
| [1. Pools are the gateways to the tiers](adr/0001-pools.md) | The 4 pool names, tier patterns, and order |
| [2. A tool request or an image request skips models that cannot take it](adr/0002-tools.md) | The capability skip and its silent fault |
| [3. Errors reroute to the next fallback](adr/0003-errors.md) | The reroute rules and the wait limits |
| [4. Penalties and session affinity order each chain](adr/0004-penalties.md) | The weights, rate limits, pacing, order, pin, and loops |
| [5. Parallel queries race for the session pin](adr/0005-parallel-queries.md) | The race keys and the winner |
