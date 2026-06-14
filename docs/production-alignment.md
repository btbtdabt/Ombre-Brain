# Production Alignment

This deployment has three state surfaces:

- Git-tracked code/docs/scripts.
- Local/VPS runtime files: `config.yaml`, `.env`, `cloudflare.env`.
- Cloudflare Worker secrets for `nuojiji-relay`.

Secrets are never committed. Runtime files must be kept aligned by copying the same
canonical values to local and VPS, then running the alignment check.

## Canonical Ombre Gateway

Public Gateway base:

```text
https://gateway.btombre.men
```

OpenAI-compatible final chat base:

```text
https://gateway.btombre.men/v1
```

Native Gemini coordinator base:

```text
https://gateway.btombre.men/v1beta
```

Expected models:

```text
claude-opus-4-6
claude-opus-4-8
claude-fable-5
gemini-3.5-flash
```

Default final model:

```text
claude-opus-4-8
```

## Ombre Runtime Files

`config.yaml` must include:

```yaml
gateway:
  upstream_default_model: "claude-opus-4-8"
  token_routes:
    - name: "gemini"
      token_env: "OMBRE_GATEWAY_GEMINI_TOKEN"
      default_model: "gemini-3.5-flash"
  upstreams:
    - name: "anthropic"
      base_url: "https://claude-proxy.amydong.workers.dev/v1"
      api_key_env: "OMBRE_GATEWAY_ANTHROPIC_API_KEY"
      default_model: "claude-opus-4-8"
      models:
        - "claude-opus-4-6"
        - "claude-opus-4-8"
        - "claude-fable-5"
    - name: "gemini"
      base_url: "https://gemini.amydong.workers.dev/v1"
      gemini_base_url: "https://gemini.amydong.workers.dev/v1beta"
      gemini_auth: "bearer"
      api_key_env: "OMBRE_GATEWAY_GEMINI_API_KEY"
      default_model: "gemini-3.5-flash"
      models:
        - "gemini-3.5-flash"
```

`.env` must include matching secrets:

```text
OMBRE_GATEWAY_TOKEN=<normal Claude gateway token>
OMBRE_GATEWAY_GEMINI_TOKEN=<Gemini coordinator gateway token>
OMBRE_GATEWAY_ANTHROPIC_API_KEY=<Claude upstream access token>
OMBRE_GATEWAY_GEMINI_API_KEY=<Gemini upstream/proxy token>
```

## Relay Worker Secrets

`nuojiji-relay` Cloudflare Worker secrets must map to the same Gateway:

```text
AGENT_FINAL_API_URL=https://gateway.btombre.men/v1
AGENT_FINAL_API_KEY=<same value as OMBRE_GATEWAY_TOKEN>
AGENT_FINAL_MODEL=claude-opus-4-8
AGENT_FINAL_API_TYPE=openai
AGENT_FINAL_OMBRE_SESSION_ID=main

AGENT_COORDINATOR_BASE_URL=https://gateway.btombre.men/v1beta
AGENT_COORDINATOR_API_KEY=<same value as OMBRE_GATEWAY_GEMINI_TOKEN>
AGENT_COORDINATOR_AUTH_TYPE=bearer
AGENT_COORDINATOR_MODEL=gemini-3.5-flash
AGENT_COORDINATOR_SESSION_ID=relay-coordinator

AGENT_MCP_URL=https://brain.btombre.men/mcp
AGENT_MCP_BEARER_TOKEN=<same value as OMBRE_CHATGPT_OAUTH_ACCESS_TOKEN or configured MCP bearer>
```

## Required Check

Run after any config, env, VPS, or Cloudflare secret change:

```powershell
cd C:\Users\Amy98\Projects\Ombre-Brain
python scripts\check_production_alignment.py
```

The check verifies:

- local `config.yaml` has the expected upstreams and Gemini token route;
- local `.env` has the required secret names populated;
- production Gateway exposes the expected models;
- production Gateway Claude final route works;
- production Gateway native Gemini route works with the Gemini token;
- relay final route works;
- relay coordinator reaches Ombre MCP tools without 404.

If the check fails, do not trust the deployment until the failed line is fixed.
