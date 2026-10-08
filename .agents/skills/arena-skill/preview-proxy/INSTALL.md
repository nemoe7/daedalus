# Arena preview proxy install

This page carries the install detail for the tailnet preview proxy, the installed web app that opens an Arena sandbox preview on Android in the page, without the mobile redirect to a new window.

## Container

The repository holds the source. `server.js` is the proxy, and `assets/` holds the installed-app files. The [`Dockerfile`](Dockerfile) copies both into a `node:22-alpine` image. The image runs as a non-root user and needs no build step.

- The published image is `ghcr.io/nemoe7/arena-preview-proxy:latest`, one mutable tag that follows the branch head. Images are multi-arch, so an arm64 host pulls natively.
- Build locally with `docker build -t arena-preview-proxy .`, or let compose pull the published image.
- The image holds no secrets. `PROXY_COOKIE_SECRET` is required, at least 32 characters; generate one with `openssl rand -hex 32`.
- `PORT` defaults to 8080, and the server binds `127.0.0.1` only: the Tailscale sidecar is the single way in.
- `/healthz` answers `ok`, for the container health check.

## Compose and Serve

[`docker-compose.yml`](docker-compose.yml) runs the image behind a Tailscale sidecar. [`tailscale-serve.json`](tailscale-serve.json) carries the Serve route and keeps Funnel off.

- The tailnet needs an auth key for `TS_AUTHKEY`, with HTTPS certificates and MagicDNS enabled.
- Serve publishes HTTPS inside the tailnet only. `AllowFunnel` stays false, so the route never reaches the public internet.
- The sidecar uses userspace networking: no host `/dev/net/tun`, no added capabilities.
- `docker compose down` keeps the Tailscale identity; `docker compose down -v` deletes it and needs a new enrollment.

## The installed app

- The first request carries the preview: `https://arena-preview.<tailnet>.ts.net/?url=https%3A%2F%2Fsbx-xxxx.arena.site%2F`.
- The proxy answers with the preview itself, in-page. It sets a signed, HttpOnly cookie for the selected origin, so reloads and the preview's later `/api/...` requests stay on the same sandbox without the parameter.
- The proxy injects a manifest link and a worker registration into the preview HTML, so Android Chrome offers to install the page. The installed app reopens the same preview.
- Only HTTPS roots on `sbx-*.arena.site` are accepted: no other host, no credentials, no port, no subpath. Write methods reach the selected preview only, and the browser's `Origin` is never forged.
- A strict `Content-Security-Policy` on the preview page can suppress the manifest; the preview keeps working either way.

## Links and the share sheet

- A web app cannot capture `https://` links on another host, so tapping a raw `sbx-*.arena.site` link never opens this app directly. That dialog belongs to native apps.
- A share does reach the app: the manifest registers `share_target`, so the Android share sheet lists the installed app, and it opens the shared preview at once.
- A link inside the preview keeps its own behavior: the proxy adds no click interception.

## Tailnet notes

- The preview page embeds a client-side API token. Anyone who can load the page can read it, and write methods reach the preview; check the tailnet policy before sharing the node.
- The proxy strips the browser's navigation-context headers (`Sec-Fetch-*` and `Referer`) and forwards the rest. That is the whole mechanism: no TLS bypass, no browser spoofing.
- Cloudflare can still refuse the upstream request for its own reasons, and then the preview shows its gate again. The proxy cannot fix that.
