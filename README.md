# Violetics Solver

Local HTTP service that solves Cloudflare Turnstile widgets and clears
Cloudflare "Just a moment..." JS / interactive challenges. Built on
[Camoufox](https://github.com/daijro/camoufox) (stealth Firefox) for the
Turnstile widget path and [Byparr](https://github.com/ThePhaseless/Byparr)
(Camoufox + playwright-captcha) for the JS-challenge path, behind a single
unified HTTP API.

## Features

- `POST /solve` — solves a Turnstile widget and returns the token. Uses a
  warm Camoufox BrowserContext with a persistent profile.
- `POST /solve-challenge` — clears a Cloudflare JS or interactive challenge
  and returns the final URL, title, cookies (filtered to the target
  domain), user-agent, and full HTML. Delegates to a bundled Byparr
  instance (FlareSolverr-compatible protocol); falls back to the
  in-process Camoufox path if the proxy is unreachable.
- Browser is **warmed at startup** so the first request doesn't pay a
  cold-start penalty (~13s once vs 30s per first-request previously).
- Sanitised error responses with stable `error_code` field — internal
  Playwright/Camoufox stack traces stay in the server log only.
- Bounded request body (default 64 KB) and `siteurl` validation
  (http/https only) to prevent the headless browser from being pointed at
  attacker-controlled `file://` / `chrome://` URLs.
- Structured single-block log per request with per-step progress output.
- Async HTTP server (aiohttp); clients may send requests in parallel,
  solves are serialised internally to avoid CF difficulty escalation.
- `docker compose up -d` brings up both services with health-gated
  startup.

### Why Camoufox + Byparr?

`nodriver` / Patchright / stock Chromium are fingerprinted by current
Cloudflare deployments — the Turnstile iframe simply never mounts on
those builds. Camoufox uses a hardened Firefox build with realistic OS
fingerprints and a built-in human-like mouse model that reliably clears
the widget.

For the heavier JS-challenge path, Byparr ships its own Camoufox-based
worker that actively tracks CF protocol changes; it clears those pages
in 10–20 s on a cold profile. Byparr exposes a FlareSolverr-compatible
API (`POST /v1` with `cmd: request.get`), so swapping to FlareSolverr
later is one env var.

## Requirements

- Docker and Docker Compose, **or**
- Python 3.11+ (tested on 3.13). Camoufox bundles its own Firefox
  binary via `python -m camoufox fetch`.

## Quick start

```bash
git clone git@github.com:cv3inx/turnstile-solver.git
cd turnstile-solver
docker compose up -d
```

This starts two containers:

- `violetics-solver` (this service) on `:9988`
- `violetics-byparr` (Camoufox-based CF challenge worker) on the internal
  compose network only — not exposed on the host.

The solver waits for Byparr to report healthy before starting.

Check it:

```bash
curl http://localhost:9988/health
```

### Disabling the Byparr delegation

Remove the `byparr` service and the `CHALLENGE_PROXY_URL` env from
`docker-compose.yml`, or set `CHALLENGE_PROXY_URL=""`. `/solve-challenge`
will then run the pure-Camoufox path. Expect higher latency and more
timeouts on hosts where Cloudflare fingerprints Firefox-with-Xvfb — see
the section above.

### Plain Docker

```bash
docker build -t violetics-solver .
docker run -d --name byparr --restart unless-stopped \
  ghcr.io/thephaseless/byparr:latest
docker run -d --name solver --shm-size=1gb \
  --link byparr \
  -e CHALLENGE_PROXY_URL=http://byparr:8191 \
  -e CHALLENGE_PROXY_KIND=byparr \
  -p 9988:9988 \
  -v solver-profile:/tmp/ts_profile \
  violetics-solver
```

`--shm-size=1gb` is required — Firefox crashes with the default 64 MB
`/dev/shm`. The volume mount preserves the Cloudflare cookie profile
across container restarts.

### Host install (optional)

```bash
pip install -r requirements.txt
python -m camoufox fetch
# Byparr is optional; without it /solve-challenge runs the in-process
# Camoufox path only.
export CHALLENGE_PROXY_URL=http://localhost:8191   # if running Byparr locally
export CHALLENGE_PROXY_KIND=byparr
python service.py
```

## Configuration

Environment variables:

| Variable                | Default           | Description                                                                                       |
|-------------------------|-------------------|---------------------------------------------------------------------------------------------------|
| `PORT`                  | `9988`            | HTTP port                                                                                         |
| `MAX_WORKERS`           | `8`               | Max concurrent in-flight HTTP requests (solves are serialised internally)                         |
| `MAX_BODY_BYTES`        | `65536`           | Max accepted request body size                                                                    |
| `LOG_LEVEL`             | `INFO`            | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)                                        |
| `CHALLENGE_PROXY_URL`   | _(unset)_         | Base URL of a Byparr/FlareSolverr instance. When set, `/solve-challenge` delegates to it.         |
| `CHALLENGE_PROXY_KIND`  | `byparr`          | `byparr` (timeouts in seconds) or `flaresolverr` (timeouts in ms). Auto-set when only `FLARESOLVERR_URL` is provided. |
| `FLARESOLVERR_URL`      | _(unset)_         | Back-compat alias for `CHALLENGE_PROXY_URL` with `KIND=flaresolverr`                              |
| `TS_PROFILE_DIR`        | `/tmp/ts_profile` | Persistent Camoufox profile directory                                                             |
| `CAMOUFOX_HEADLESS`     | `virtual`         | `virtual` (Camoufox-managed Xvfb), `true`, or `false`                                             |

## API

All endpoints accept and return JSON.

### `POST /solve`

Solves a Turnstile widget.

Request:

```json
{
  "sitekey": "0x4AAAAAAC3x1HiBz5IFyj7s",
  "siteurl": "https://www.example.com/",
  "timeout": 45,
  "action": "login",
  "cdata": "optional-customer-data"
}
```

`action` and `cdata` are optional and forwarded to the widget when present.
`timeout` is clamped to `[5, 180]` seconds; default `45`.

Response (200):

```json
{
  "token": "1.abc...xyz",
  "elapsed": 6.14
}
```

Response (4xx / 5xx):

```json
{
  "error": "solve timeout",
  "error_code": "timeout",
  "elapsed": 45.2
}
```

`error_code` is one of:

| Code            | HTTP | Meaning                                                  |
|-----------------|------|----------------------------------------------------------|
| `bad_request`   | 400  | Validation failure (missing field, bad URL, oversize)    |
| `timeout`       | 504  | Solve did not finish within `timeout` seconds            |
| `browser_error` | 503  | Browser closed / navigation failure — caller should retry |
| `solver_error`  | 500  | Internal failure (sanitised — see server log for detail) |

### `POST /solve-challenge`

Clears a Cloudflare JS or interactive challenge and returns the page state.
When `CHALLENGE_PROXY_URL` is configured, the request is proxied to Byparr
transparently — callers see the same response shape either way.

Request:

```json
{
  "siteurl": "https://api.example.com/docs",
  "timeout": 45
}
```

Response (200):

```json
{
  "url": "https://api.example.com/docs/",
  "title": "Example API",
  "user_agent": "Mozilla/5.0 ...",
  "cookies": [
    {
      "name": "cf_clearance",
      "value": "...",
      "domain": ".example.com",
      "path": "/",
      "expires": 1811226000
    }
  ],
  "html": "<!doctype html>...",
  "elapsed": 15.52
}
```

Use the returned `cf_clearance` cookie together with `user_agent` when
proxying the protected API. Both must match — Cloudflare rejects the
cookie if the user-agent differs from the one that earned it.

The solver auto-retries extensionless paths with a trailing slash, since
Byparr/FlareSolverr is sensitive to that for some sites (e.g. `/docs`
times out, but `/docs/` clears).

### `GET /health`

Returns service status counters.

```json
{
  "status": "ok",
  "mode": "byparr",
  "proxy_url": "http://byparr:8191",
  "in_flight": 0,
  "solved": 30,
  "errors": 1,
  "challenges": 11
}
```

### `GET /stats`

Returns extended counters: uptime, total requests, success rate, latency
percentiles (avg / p50 / p95), and the last 50 request events. Used by
the built-in playground at `/`.

## Log format

Each request produces one block with real-time progress steps:

```
「 NEW REQUEST 」
» ID     : 29241879
» FROM   : 172.20.0.1
» POST   : /solve
» URL    : https://www.example.com/
» KEY    : 0x4AAAAAAC3x1H...
  [29241879] opening tab for https://www.example.com/
  [29241879] route intercepted https://www.example.com/
  [29241879] token obtained (4.3s)
» SPEED  : 4.31s
» STATUS : 200 - token 1.1Tqrqdr...26cb55 (538 chars)
```

For JS-challenge requests routed through Byparr:

```
「 NEW REQUEST 」
» ID     : cdd14513
» FROM   : 172.20.0.1
» POST   : /solve-challenge
» URL    : https://api.example.com/docs
  [cdd14513] delegating to byparr -> http://byparr:8191
  [cdd14513] byparr cleared (15.5s, cookies=1)
» SPEED  : 15.52s
» STATUS : 200 - title='Example API' cookies=1 html=74236b
```

All output is written to stdout. Internal library warnings are suppressed.

## Concurrency

The service accepts many HTTP requests in parallel, but Cloudflare
escalates difficulty when multiple tabs on the same profile request a
token for the same sitekey at once. Solves are therefore serialised
inside the service.

Typical throughput on a warm browser:

- Turnstile (`/solve`), always-pass demo: ~6 s end-to-end
- Turnstile (`/solve`), real sitekey: 8–15 s typical
- JS challenge via Byparr: ~15 s per solve (single Byparr worker)
- JS challenge via in-process Camoufox, warm profile: under 2 s
- JS challenge via in-process Camoufox, cold profile: 8–12 s

Scaling beyond single-browser throughput requires multiple independent
solver instances, each with its own warm profile and IP.

## Production notes

This service is intended to run inside a trusted network. Before exposing
it publicly:

- Put it behind a reverse proxy (Caddy / nginx) for TLS, CORS, and IP
  allow-listing.
- Add an auth layer at the proxy — every `/solve` is a real browser tab
  and is expensive, so unauthenticated public access is an abuse vector.
- Add a per-IP rate limit at the proxy.

The persistent profile volume (`/tmp/ts_profile`) holds Cloudflare
cookies. Treat it as sensitive — anyone with the volume can reuse those
clearances.

## File layout

```
solver.py            Core browser automation + Byparr delegation
service.py           aiohttp HTTP wrapper, request logging, validation
requirements.txt     Python dependencies (camoufox, aiohttp)
Dockerfile           Container image (Python + Camoufox + Xvfb)
docker-compose.yml   Compose stack: solver + byparr
entrypoint.sh        Container entrypoint (clears stale X locks, starts service)
web/                 Built-in playground UI
```

## License

MIT
