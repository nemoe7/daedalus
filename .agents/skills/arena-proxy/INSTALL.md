# Arena Proxy Install

This page carries the install detail that the agent does not need to use the endpoint.

## Container

The repository holds the source. `scripts/` is the server, and the [`Dockerfile`](https://github.com/nemoe7/clankers/blob/main/skills/arena-proxy/Dockerfile) copies it into a `python:3.12-alpine` image. The image runs as a non-root user and needs no build step.

```
docker build -t arena-proxy .
docker run --rm -p 8787:8787 \
  -e GITHUB_TOKEN=<pat> -e ARENA_PROXY_REPO=<owner>/<repo> \
  -e ARENA_PROXY_LLM_BASE=<url> -e ARENA_PROXY_LLM_KEY=<key> -e ARENA_PROXY_LLM_MODEL=<model> \
  -v arena-proxy-state:/state -e ARENA_PROXY_STATE_DIR=/state \
  arena-proxy
```

- The published image is `ghcr.io/nemoe7/arena-proxy:latest`, one mutable tag that follows the branch head.
- Images are multi-arch, so an arm64 host pulls and builds natively.
- The image holds no secrets. Pass them as environment variables, and mount a volume when staged bytes should outlive the container.
- Every start makes a new agent key and prints `agent key (new on every start): ...`. The key is never read from the environment, so a restart rotates it.
- The server holds no state beyond the state directory, so `--rm` costs nothing but staged bytes.

## Compose and the funnel

[`docker-compose.yml`](docker-compose.yml) runs the image behind a Tailscale sidecar. [`tailscale-serve.json`](tailscale-serve.json) carries the funnel route.

- The funnel needs three tailnet settings: an auth key for `TS_AUTHKEY`, a `tagOwners` entry for the node tag, and the `funnel` node attribute on that tag. Give the key the tag when you make it.
- A tagged auth key carries its tag to the node, so the compose file requests no tag of its own.
- Funnel accepts connections from anywhere, so treat the URL as public and keep the token read-only.

## Host notes

- The backend is one Python package with no dependencies beyond the standard library, about 30 KB of source. It idles at a few megabytes of memory.
- Environment variables: `ARENA_PROXY_HOST`, `ARENA_PROXY_PORT`, `ARENA_PROXY_STATE_DIR`, `ARENA_PROXY_FETCH_CAP`, `ARENA_PROXY_STAGE_CAP`, and `ARENA_PROXY_MASTER_KEY`, plus the GitHub and model groups.
- Prune the state directory if staged bytes accumulate. The server drops entries older than one hour on its own requests.
- On a small host, cap the process (`--memory 128m`) and keep one replica.

## Exposure

The tool needs one public HTTPS base URL. Any of these works, and the key stays the only gate:

| Option | Command or step | Notes |
| --- | --- | --- |
| Cloudflare Tunnel | `cloudflared tunnel --url http://localhost:8787` | A free quick tunnel gives a random hostname that changes per run |
| Tailscale Funnel | `tailscale funnel --bg --https=443 http://127.0.0.1:8787` | A stable `https://<machine>.<tailnet>.ts.net` URL, and Funnel serves the public internet |
| Reverse proxy | nginx or Caddy in front | Use an existing certificate and hostname |

- A tunnel host that sleeps needs a retry. The first request to a cold tunnel can answer an HTML error page.

A distributed copy of this skill carries the documents. The image holds the code.
