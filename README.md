# Notion Gateway (Notion AI → OpenAI-compatible)

Local proxy that exposes your **Notion Business AI** chat models as an
OpenAI-compatible endpoint (`/v1/chat/completions`) so opencode can use
them as a provider.

## Why this exists

A Notion `ntn_*` API key only talks to the Notion **REST API**
(pages/databases) — it has **no LLM endpoint**, so it cannot be used as
an opencode `provider` directly. Notion AI chat is only reachable via the
internal `POST /api/v3/runInferenceTranscript` with a browser `token_v2`
cookie. This gateway bridges that gap.

## How it works

```
opencode  →  http://127.0.0.1:8330/v1/chat/completions  →  notion_proxy.py
        →  app.notion.com/api/v3/runInferenceTranscript (token_v2 cookie)
        →  OpenAI-style JSON / SSE back to opencode
```

Key details (learned by capturing a real browser session):
- Upstream must be `app.notion.com` with a current `notion-client-version`.
- Client model names are mapped to Notion internal IDs
  (e.g. `gpt-6-astra` → `orlando-quinn`), with per-model reasoning effort.
- Requests use Chrome TLS impersonation (`curl_cffi`) — plain `httpx`
  gets `Something went wrong` from Notion/Cloudflare.
- Answers are parsed from the `agent-inference` patch stream.

## Files

| File | Purpose |
|---|---|
| `notion_proxy.py` | The gateway (FastAPI). |
| `credentials.json` | Your session (gitignored in spirit — never share/commit). |
| `credentials.json1` | Reference template — copy to `credentials.json`, then run refresh (see below). |
| `start_gateway.cmd` / `stop_gateway.cmd` | Start/stop on port `8330`. |
| `refresh_credentials.py` / `.cmd` | Renew `token_v2` after expiry (see below). |
| `opencode.snippet.json` | Provider block to merge into `opencode.json`. |
| `requirements.txt` | `fastapi, httpx, uvicorn, curl_cffi` (+ optional `playwright`). |

## Setup & run

```powershell
pip install -r requirements.txt
# double-click start_gateway.cmd, or:
python notion_proxy.py
```

Verify:

```powershell
curl.exe -s http://127.0.0.1:8330/__proxy_health
curl.exe -s http://127.0.0.1:8330/v1/models
```

## Wire into opencode

Merge `opencode.snippet.json` into `~/.config/opencode/opencode.json`
under `provider`, restart opencode, then `/models` →
`notion/claude-sonnet4.6`:

```json
{
  "provider": {
    "notion": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Notion AI (local)",
      "options": {
        "baseURL": "http://127.0.0.1:8330/v1",
        "apiKey": "notion-local-key"
      },
      "models": {
        "claude-sonnet4.6": { "name": "Claude Sonnet 4.6 (Notion)" },
        "claude-opus4.8": { "name": "Claude Opus 4.8 (Notion)" }
      }
    }
  }
}
```

## Refreshing credentials

`token_v2` expires (logout, password change, rotation). Symptoms: HTTP
401 or `Notion rejected token_v2`.

```powershell
# double-click refresh_credentials.cmd, or:
python refresh_credentials.py            # opens browser, you paste one cookie value
python refresh_credentials.py --manual   # same, without opening the browser
python refresh_credentials.py --auto     # full automation (needs pip install playwright)
python refresh_credentials.py --check    # validate current credentials.json only
```

The old file is backed up to `credentials.bak-<timestamp>.json`.
Restart the gateway afterwards (`stop_gateway.cmd` → `start_gateway.cmd`).

New Notion account? Run the refresh flow while logged into it, or use
`--out credentials.<profile>.json` with a second copy of this folder on
another port.

## Models

Live picker captured 2026-09-08 (`getAvailableModels`). Client name →
Notion internal codename in `MODEL_MAP`; per-model `reasoningEffort`
defaults in `MODEL_EFFORT` (Notion rejects unsupported efforts).

| opencode name | Notion backend | Notes |
|---|---|---|
| `gpt-6-astra` | `orlando-quinn` | Verified working, recommended default |
| `claude-opus-5` | `agave-flan` | Hardest-tasks tier |
| `claude-sonnet-5` | `angel-cake-high` | |
| `gpt-5.6-sol` | `orange-mousse` | Hardest-tasks tier |
| `gpt-5.6-terra` | `orchid-muffin` | |
| `gpt-5.6-luna` | `olive-jellyroll` | |
| `kimi-k3` | `fireworks-kimi-k3` | Hardest-tasks tier |
| `claude-opus4.8` | `ambrosia-tart-high` | |
| `claude-opus4.7` | `apricot-sorbet-high` | |
| `claude-opus4.6` | `avocado-froyo-medium` | No `workflow` backend — may fail |
| `claude-sonnet4.6` | `almond-croissant-low` | |
| `claude-haiku4.5` | `anthropic-haiku-4.5` | No `workflow` backend — may fail |
| `gpt-5.5` | `opal-quince-medium` | |
| `gpt-5.4` | `oval-kumquat-medium` | |
| `gpt-5.2` | `oatmeal-cookie` | |
| `gemini-3.1-pro` | `galette-medium-thinking` | |
| `gemini-3.7-flash` | `grapefruit-zeppole` | |
| `grok-4.5` | `strawberry-whoopiepie` | Picker labels newest Grok "4.6" |
| `grok-4.3` | `xigua-mochi-medium` | |
| `kimi-k2.6` | `fireworks-kimi-k2.6` | |
| `deepseek-v4-pro` | `baseten-deepseek-v4-pro` | |
| `deepseek-v4-flash` | `baseten-deepseek-v4-flash` | |
| `glm-5.2` | `baseten-glm-5.2` | |

Skipped: Fable 5 / 5.1 (disabled, `trial_not_allowed`), Auto (no backend
codename), GPT-5.4 Mini/Nano + Gemini 3 Flash (customAgent-only).

Notion rotates internal model IDs — if a model starts failing, capture a
fresh `runInferenceTranscript` request (DevTools → Network) and update
`MODEL_MAP` in `notion_proxy.py`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `401 / token rejected` | Refresh credentials (above). |
| `Something went wrong` in answers | Update `client_version` to the current `assetsVersion`, refresh `token_v2`. |
| Empty/unparseable response | Model ID rotated — see Models table. |
| Port `8330` in use | `stop_gateway.cmd` kills the previous instance. |
| Each chat appears in Notion sidebar | Expected — Notion creates a thread per request. |

## Security

- `token_v2` grants **full access** to your Notion account. Keep this
  folder local, never commit/publish `credentials.json` or backups.
- Unofficial bridge (reverse-engineered internal API): can break without
  notice, subject to your workspace's AI limits/credits.
