# How to Run — Notion Gateway

From zero to chatting via Notion AI in opencode. (~5 minutes first time,
~30 seconds after that.)

## 1. Prerequisites (once)

- Python 3.11+ (`python --version`)
- Logged into Notion in your browser (Business plan with AI access)
- Install dependencies:

```powershell
cd "C:\Users\m21si\OneDrive\Desktop\AI Playground\Gateways\Notion gateway"
pip install -r requirements.txt
```

## 2. First run — get credentials

```powershell
# double-click refresh_credentials.cmd, or:
python refresh_credentials.py
```

1. Your browser opens at Notion — sign in if asked.
2. Press **F12 → Application → Cookies**, copy the `token_v2` Value
   (or copy the whole cookie string — both work).
3. Paste it into the terminal (input is hidden) → press Enter.
4. Pick your workspace if asked.
5. You should see `Wrote credentials.json`. The old file is backed up
   automatically (`credentials.bak-<timestamp>.json`).

Sanity check (no changes made):

```powershell
python refresh_credentials.py --check
# expect: VALID  user=you@...  spaces=[(...)]  token=v03%3A...len:537
```

## 3. Start the gateway

```powershell
# double-click start_gateway.cmd, or:
python notion_proxy.py
```

Leave that window open. Verify in a **second** terminal:

```powershell
curl.exe -s http://127.0.0.1:8330/__proxy_health
curl.exe -s http://127.0.0.1:8330/v1/models
```

Expect `"status":"ok"` and the 6-model list.

## 4. Connect opencode (once)

1. Open `Notion gateway\opencode.snippet.json`, copy the `notion` block.
2. Paste it under `provider` in `~/.config/opencode/opencode.json`.
3. Restart opencode → `/models` → pick `notion/claude-sonnet4.6`.
4. Send a test message. First token takes ~3s (Notion-side latency).

## 5. Daily use

```powershell
# terminal 1: start
start_gateway.cmd
# use opencode normally
# terminal 1: Ctrl+C when done, or run stop_gateway.cmd
```

## 6. When it stops working (token expired)

Symptom: `401`, `token rejected`, or `Something went wrong` on every reply.

```powershell
python refresh_credentials.py     # paste a fresh token_v2
stop_gateway.cmd
start_gateway.cmd
```

## Quick command reference

| Task | Command |
|---|---|
| Start | `start_gateway.cmd` or `python notion_proxy.py` |
| Stop | `stop_gateway.cmd` |
| Health | `curl.exe -s http://127.0.0.1:8330/__proxy_health` |
| Models | `curl.exe -s http://127.0.0.1:8330/v1/models` |
| Validate creds | `python refresh_credentials.py --check` |
| Renew creds | `python refresh_credentials.py` |
| Renew, no browser open | `python refresh_credentials.py --manual` |
| Second account | `python refresh_credentials.py --out credentials.work.json` (+ folder copy on another port — see README) |
