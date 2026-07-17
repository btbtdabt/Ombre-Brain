# Remote Deploy With Cloudflare Tunnel

This repo cannot run directly on Cloudflare Workers or Pages: it is a long-running Python app with Docker services, Markdown buckets, SQLite/runtime state, and model API keys. Cloudflare should sit in front of an always-on origin, usually a small VPS. That gives you HTTPS URLs without keeping your PC on 24/7.

The included `compose.cloudflare.yml` runs:

- `ombre-brain`: MCP, Dashboard, ChatGPT/Claude Connector endpoints.
- `ombre-gateway`: OpenAI/Anthropic-compatible chat gateway.
- `cloudflared`: Cloudflare Tunnel connector in the same Docker network.

## Prerequisites

- A Linux VPS or VM with Docker and Docker Compose.
- A Cloudflare account and a domain on Cloudflare.
- Two hostnames, for example:
  - `brain.example.com`
  - `gateway.example.com`
- Model provider API keys for Ombre itself and for the Gateway upstreams.

Cloudflare's current tunnel docs require a server or VM where `cloudflared` runs, and a Cloudflare-managed domain to publish public applications: <https://developers.cloudflare.com/tunnel/setup/>

## 1. Prepare The VPS

```bash
git clone https://github.com/Yinglianchun/Ombre-Brain.git /opt/Ombre-Brain
cd /opt/Ombre-Brain

mkdir -p /srv/ombre-brain/buckets /srv/ombre-brain/state
cp config.example.yaml /srv/ombre-brain/config.yaml
```

Run the one-click script once to generate `.env`, `config.yaml`, and model routing:

```bash
./ob
```

Choose:

- VPS deployment.
- Deploy all, if you want both MCP/Dashboard and Gateway memory injection.

The script starts the generated `compose.local.yml`. Stop it before switching to the Cloudflare compose file, then move the generated runtime files into the production mount paths:

```bash
docker compose -f compose.local.yml down

cp /opt/Ombre-Brain/config.yaml /srv/ombre-brain/config.yaml
cp -a /opt/Ombre-Brain/buckets/. /srv/ombre-brain/buckets/ 2>/dev/null || true
cp -a /opt/Ombre-Brain/state/. /srv/ombre-brain/state/ 2>/dev/null || true
```

Keep secrets in `/opt/Ombre-Brain/.env`. Do not commit `.env`.

## 2. Create The Cloudflare Tunnel

In Cloudflare Zero Trust:

1. Go to `Networks > Tunnels`.
2. Create a Cloudflared tunnel.
3. Choose Docker as the environment.
4. Copy only the tunnel token value.

Create `cloudflare.env` on the VPS:

```bash
cp cloudflare.env.example cloudflare.env
nano cloudflare.env
```

Set:

```env
TUNNEL_TOKEN=your-cloudflare-tunnel-token
```

Cloudflare documents `TUNNEL_TOKEN` as the environment-variable form of `cloudflared tunnel run --token`: <https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/#token>

## 3. Publish The Two Applications

In the same Cloudflare tunnel, add two public hostname routes:

```text
brain.example.com   -> http://ombre-brain:8000
gateway.example.com -> http://ombre-gateway:8010
```

Use those exact Docker service names in the service URLs. `localhost` from inside the `cloudflared` container means the tunnel container itself, not the Ombre containers.

Cloudflare ingress rules need a final catch-all rule when you manage ingress by configuration; the dashboard-managed tunnel handles that for you. The public hostname still maps each hostname to a local service URL.

## 4. Start

```bash
cd /opt/Ombre-Brain
docker compose -f compose.cloudflare.yml up -d --build
```

Local health checks on the VPS:

```bash
curl -sS http://127.0.0.1:18001/health
curl -sS http://127.0.0.1:18002/health
```

Cloudflare health checks:

```bash
curl -sS https://brain.example.com/health
curl -sS https://gateway.example.com/health
```

Useful logs:

```bash
docker compose -f compose.cloudflare.yml logs --tail=120 ombre-brain
docker compose -f compose.cloudflare.yml logs --tail=120 ombre-gateway
docker compose -f compose.cloudflare.yml logs --tail=120 cloudflared
```

## 5. Client URLs

OpenAI-compatible clients:

```text
Base URL: https://gateway.example.com/v1
API Key: value of OMBRE_GATEWAY_TOKEN from .env
Header: X-Ombre-Session-Id: main
```

Anthropic-compatible clients:

```text
Endpoint: https://gateway.example.com/v1/messages
API Key: value of OMBRE_GATEWAY_TOKEN from .env
Header: X-Ombre-Session-Id: main
```

Dashboard:

```text
https://brain.example.com/dashboard
```

MCP:

```text
https://brain.example.com/mcp
```

ChatGPT/Claude Connector OAuth:

```text
MCP server URL: https://brain.example.com/mcp
Authorization URL: https://brain.example.com/oauth/authorize
Token URL: https://brain.example.com/oauth/token
Token endpoint auth method: client_secret_post
```

Set this in `.env` so OAuth metadata uses the public HTTPS origin:

```env
OMBRE_CHATGPT_OAUTH_PUBLIC_BASE_URL=https://brain.example.com
```

## 6. Security Defaults

- Set a strong `OMBRE_GATEWAY_TOKEN`; this is the password for chat clients using the Gateway.
- Set `OMBRE_DASHBOARD_PASSWORD` before exposing Dashboard publicly, or set it on first login.
- Do not put model API keys, Cloudflare tunnel tokens, or OAuth secrets in `config.yaml`.
- Prefer Cloudflare Access for the Dashboard hostname if only you need browser access. Do not put Cloudflare Access in front of Connector endpoints unless the connector supports that extra login layer.
- The Compose file binds ports to `127.0.0.1` so they are available for local VPS health checks but not exposed on the VPS public interface. The tunnel reaches services over the internal Docker network.

## 7. Updates And Backups

Use the repo update script with the Cloudflare compose file:

```bash
cd /opt/Ombre-Brain
COMPOSE_FILE=compose.cloudflare.yml bash scripts/update_deploy.sh
```

Backup before bulk memory changes:

```bash
./ob
# choose: Backup current deployment
```

Manual Docker backup:

```bash
docker compose -f compose.cloudflare.yml exec -T ombre-brain sh -lc 'mkdir -p /state/backups && tar --exclude=/state/backups -czf "/state/backups/manual-$(date +%Y%m%d_%H%M%S).tar.gz" /data /state /app/config.yaml /app/.env'
```

## Alternative Hosts

Render, Railway, Fly.io, and similar platforms can run Python/Docker services, but this app needs two cooperating processes and durable shared bucket/state storage. A small VPS plus Cloudflare Tunnel is the least surprising setup. If you choose another host, make sure both services can share the same persistent `buckets`, `state`, `config.yaml`, and `.env`.
