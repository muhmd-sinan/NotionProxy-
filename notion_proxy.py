#!/usr/bin/env python3
"""Local OpenAI-compatible proxy for Notion AI (Business plan, token_v2).

Why this exists:
- Notion's `ntn_*` API key (REST: pages/databases) has NO LLM endpoint.
  It cannot be used as an opencode `provider` directly.
- Notion AI (chat models) is only reachable via the internal
  POST https://app.notion.com/api/v3/runInferenceTranscript with a
  browser `token_v2` cookie. This proxy exposes that as OpenAI shapes
  opencode understands. Uses curl_cffi Chrome impersonation (plain httpx
  gets "Something went wrong" from Notion/Cloudflare).

Credentials: extracted 2026-09-08 via Playwright from app.notion.com/ai
  (user grok, space "grok's Space"). Stored in credentials.json next to this file.

Run:
    python notion_proxy.py
Config:
    credentials.json (see credentials.example.json)
Endpoints:
    GET  /v1/models
    POST /v1/chat/completions (stream SSE-emulated + non-stream)
    GET  /__proxy_health
"""
from __future__ import annotations

import datetime
import json as _json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from curl_cffi.requests import Session as CurlSession
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

_CREDS_PATH = Path(__file__).parent / "credentials.json"
_cfg: dict = {}
if _CREDS_PATH.exists():
    _cfg = _json.loads(_CREDS_PATH.read_text(encoding="utf-8"))


def _c(key: str, default=None):
    return _cfg.get(key, default)


TOKEN_V2 = _c("token_v2", "")
SPACE_ID = _c("space_id", "")
USER_ID = _c("user_id", "")
SPACE_VIEW_ID = _c("space_view_id", "")
USER_NAME = _c("user_name", "user")
USER_EMAIL = _c("user_email", "")
BROWSER_ID = _c("notion_browser_id", "")
NOTION_BASE = _c("notion_base", "https://app.notion.com").rstrip("/")
CLIENT_VERSION = _c("client_version", "23.13.20260907.1809")
SPACE_NAME = _c("space_name", "grok's Space")
PROXY_API_KEY = _c("proxy_api_key", "notion-local-key")
LISTEN_HOST = _c("host", "127.0.0.1")
LISTEN_PORT = int(_c("port", 8330))

RUN_URL = f"{NOTION_BASE}/api/v3/runInferenceTranscript"

HTTPX_TIMEOUT = httpx.Timeout(connect=30.0, write=60.0, read=None, pool=None)

logging.basicConfig(level=_c("log_level", "INFO"), format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("notion-proxy")

# Client-facing IDs stay stable for opencode; mapped to Notion internal IDs.
# Source of truth: POST app.notion.com/api/v3/getAvailableModels {"spaceId":...}
# captured live 2026-09-08 (workflow.finalModelName per model). Notion rotates
# these codenames - if a model starts failing, re-capture and update here.
MODEL_MAP = {
    # Anthropic
    "claude-sonnet4.6": "almond-croissant-low",
    "claude-sonnet-5": "angel-cake-high",
    "claude-opus4.6": "avocado-froyo-medium",  # customAgent-only upstream; may fail in workflow
    "claude-opus4.7": "apricot-sorbet-high",
    "claude-opus4.8": "ambrosia-tart-high",
    "claude-opus-5": "agave-flan",
    "claude-haiku4.5": "anthropic-haiku-4.5",  # customAgent-only upstream; may fail in workflow
    # OpenAI
    "gpt-5.2": "oatmeal-cookie",
    "gpt-5.4": "oval-kumquat-medium",
    "gpt-5.5": "opal-quince-medium",
    "gpt-5.6-luna": "olive-jellyroll",
    "gpt-5.6-terra": "orchid-muffin",
    "gpt-5.6-sol": "orange-mousse",
    "gpt-6-astra": "orlando-quinn",  # verified working 2026-09-08
    # Gemini
    "gemini-3.1-pro": "galette-medium-thinking",
    "gemini-3.7-flash": "grapefruit-zeppole",
    # xAI
    "grok-4.3": "xigua-mochi-medium",
    "grok-4.5": "strawberry-whoopiepie",
    # Open models
    "kimi-k2.6": "fireworks-kimi-k2.6",
    "kimi-k3": "fireworks-kimi-k3",
    "deepseek-v4-pro": "baseten-deepseek-v4-pro",
    "deepseek-v4-flash": "baseten-deepseek-v4-flash",
    "glm-5.2": "baseten-glm-5.2",
}
# Per-model default reasoning effort from modelConfiguration.defaultReasoningEffort.
MODEL_EFFORT = {
    "claude-sonnet4.6": "low", "claude-sonnet-5": "high",
    "claude-opus4.6": "medium", "claude-opus4.7": "high",
    "claude-opus4.8": "high", "claude-opus-5": "medium",
    "claude-haiku4.5": "low",
    "gpt-5.2": "medium", "gpt-5.4": "medium", "gpt-5.5": "medium",
    "gpt-5.6-luna": "medium", "gpt-5.6-terra": "medium",
    "gpt-5.6-sol": "medium", "gpt-6-astra": "medium",
    "gemini-3.1-pro": "medium", "gemini-3.7-flash": "medium",
    "grok-4.3": "medium", "grok-4.5": "medium",
    "kimi-k2.6": "medium", "kimi-k3": "max",
    "deepseek-v4-pro": "high", "deepseek-v4-flash": "high", "glm-5.2": "high",
}
AVAILABLE_MODELS = [
    {"id": "gpt-6-astra", "owned_by": "notion", "context": 400000, "output": 128000},
    {"id": "claude-opus-5", "owned_by": "notion", "context": 1000000, "output": 128000},
    {"id": "claude-sonnet-5", "owned_by": "notion", "context": 1000000, "output": 128000},
    {"id": "claude-opus4.8", "owned_by": "notion", "context": 1000000, "output": 128000},
    {"id": "claude-opus4.7", "owned_by": "notion", "context": 1000000, "output": 128000},
    {"id": "claude-opus4.6", "owned_by": "notion", "context": 1000000, "output": 128000},
    {"id": "claude-sonnet4.6", "owned_by": "notion", "context": 1000000, "output": 128000},
    {"id": "claude-haiku4.5", "owned_by": "notion", "context": 500000, "output": 64000},
    {"id": "gpt-5.6-sol", "owned_by": "notion", "context": 400000, "output": 128000},
    {"id": "gpt-5.6-terra", "owned_by": "notion", "context": 400000, "output": 128000},
    {"id": "gpt-5.6-luna", "owned_by": "notion", "context": 400000, "output": 128000},
    {"id": "gpt-5.5", "owned_by": "notion", "context": 400000, "output": 128000},
    {"id": "gpt-5.4", "owned_by": "notion", "context": 400000, "output": 128000},
    {"id": "gpt-5.2", "owned_by": "notion", "context": 400000, "output": 128000},
    {"id": "gemini-3.1-pro", "owned_by": "notion", "context": 1000000, "output": 65536},
    {"id": "gemini-3.7-flash", "owned_by": "notion", "context": 1000000, "output": 65536},
    {"id": "grok-4.5", "owned_by": "notion", "context": 400000, "output": 128000},
    {"id": "grok-4.3", "owned_by": "notion", "context": 400000, "output": 128000},
    {"id": "kimi-k3", "owned_by": "notion", "context": 256000, "output": 65536},
    {"id": "kimi-k2.6", "owned_by": "notion", "context": 256000, "output": 65536},
    {"id": "deepseek-v4-pro", "owned_by": "notion", "context": 256000, "output": 65536},
    {"id": "deepseek-v4-flash", "owned_by": "notion", "context": 256000, "output": 65536},
    {"id": "glm-5.2", "owned_by": "notion", "context": 256000, "output": 65536},
]
_ALLOWED = {m["id"] for m in AVAILABLE_MODELS}


def resolve_notion_model(client_model: str) -> str:
    return MODEL_MAP.get(client_model, client_model)

app = FastAPI(title="Notion AI Local Proxy", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"], expose_headers=["*"],
)

_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup():
    global _client
    _client = httpx.AsyncClient(timeout=HTTPX_TIMEOUT, follow_redirects=True)
    log.info("Notion proxy on http://%s:%s -> %s", LISTEN_HOST, LISTEN_PORT, RUN_URL)
    if not TOKEN_V2:
        log.warning("token_v2 empty in credentials.json - upstream calls will 401 until set")
    if not SPACE_ID or not USER_ID:
        log.warning("space_id/user_id empty - check credentials.json")


@app.on_event("shutdown")
async def _shutdown():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _check_auth(request: Request) -> bool:
    if not PROXY_API_KEY:
        return True
    auth = request.headers.get("authorization", "")
    if auth == f"Bearer {PROXY_API_KEY}":
        return True
    return request.headers.get("x-api-key", "") == PROXY_API_KEY


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": "invalid_request_error"}})


def _msg_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                # OpenAI content part {type:text, text:...}
                if p.get("type") in ("text", "input_text"):
                    parts.append(p["text"])
                elif "text" in p:
                    parts.append(p["text"] if isinstance(p["text"], str) else "")
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join([x for x in parts if x])
    return str(content)


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def _build_transcript(messages: list[dict], model: str, client_model: str = "") -> list[dict]:
    # Mirrors browser payload captured 2026-09-08 on app.notion.com/ai.
    # reasoningEffort must be one the model supports (see MODEL_EFFORT).
    effort = MODEL_EFFORT.get(client_model, "medium")
    transcript: list[dict] = [
        {"id": str(uuid.uuid4()), "type": "config",
         "value": {"type": "workflow", "enableAgentAutomations": True,
                   "enableAgentIntegrations": True, "enableCustomAgents": True,
                   "enableExperimentalIntegrations": False, "enableScriptAgent": True,
                   "enableScriptAgentSlack": True, "enableScriptAgentMcpServers": True,
                   "enableAgentDiffs": True, "enableCsvAttachmentSupport": True,
                   "enableComputer": True, "enableAgentGenerateImage": True,
                   "enableMailExplicitToolCalls": True, "enableWebResearch": False,
                   "useRulePrioritization": True, "availableConnectors": [],
                   "searchScopes": [{"type": "everything"}], "useWebSearch": True,
                   "isHipaa": False, "internetAccess": False, "manageWorkers": False,
                   "writerMode": False, "model": model, "reasoningEffort": effort,
                   "modelFromUser": True, "isCustomAgent": False,
                   "isCustomAgentBuilder": False, "isAgentResearchRequest": False,
                   "enableMarkdownVNext": True, "enableAgentSkillsV2": True,
                   "isMobile": False}},
        {"id": str(uuid.uuid4()), "type": "context",
         "value": {"timezone": "Asia/Calcutta", "userName": USER_NAME,
                   "userId": USER_ID, "userEmail": USER_EMAIL,
                   "spaceName": SPACE_NAME, "spaceId": SPACE_ID,
                   "spaceViewId": SPACE_VIEW_ID,
                   "currentDatetime": _now_iso(), "surface": "ai_module",
                   "agentAccessory": "pencil"}},
    ]
    for m in messages:
        role = m.get("role", "user")
        text = _msg_text(m.get("content")).strip()
        if not text:
            continue
        if role in ("user", "system", "developer", "tool"):
            # system/developer folded into user turn (Notion has no system role)
            prefix = "" if role == "user" else f"[{role.upper()}] "
            transcript.append({"id": str(uuid.uuid4()), "type": "user",
                               "value": [[prefix + text]],
                               "userId": USER_ID, "createdAt": _now_iso()})
        elif role == "assistant":
            transcript.append({"id": str(uuid.uuid4()), "type": "agent-inference",
                               "value": [{"type": "text", "content": text}]})
    return transcript


def _notion_headers() -> dict[str, str]:
    # token_v2 from Playwright is URL-encoded (v03%3A...); send as-is.
    cookie = f"token_v2={TOKEN_V2}; notion_user_id={USER_ID};"
    if BROWSER_ID:
        cookie += f" notion_browser_id={BROWSER_ID};"
    return {
        "Content-Type": "application/json",
        "Accept": "application/x-ndjson",
        "Cookie": cookie,
        "x-notion-space-id": SPACE_ID,
        "x-notion-active-user-header": USER_ID,
        "notion-audit-log-platform": "web",
        "notion-client-version": CLIENT_VERSION,
        "Origin": "https://app.notion.com",
        "Referer": "https://app.notion.com/ai",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    }


def _is_noise(s: str) -> bool:
    import re
    s = s.strip()
    if len(s) < 4:
        return True
    # UUIDs, hex blobs, ids, emails, timestamps, single-word keys
    if re.fullmatch(r"[0-9a-fA-F-]{32,}", s):
        return True
    if re.fullmatch(r"[A-Za-z0-9_\-]{100,}", s):
        return True
    if "@" in s and " " not in s:
        return True
    if s.startswith(("v03", "eyJ", "http", "uuid", "trace", "thread")):
        return True
    if s in ("workflow", "workflows", "config", "context", "record-map",
             "patch-start", "UTC", USER_ID, SPACE_ID, USER_EMAIL, USER_NAME):
        return True
    # must look like natural language (contains space) or sentence punct
    if " " not in s and len(s) < 40:
        return True
    return False


def _collect_texts(obj: Any, out: list[str]) -> None:
    """Heuristic NDJSON -> text. Collects human-readable strings only."""
    if isinstance(obj, str):
        s = obj.strip()
        if s and not _is_noise(s):
            out.append(s)
        return
    if isinstance(obj, list):
        for v in obj:
            _collect_texts(v, out)
        return
    if isinstance(obj, dict):
        # Prefer known content keys first for ordering
        for k in ("content", "text", "answer", "completion", "output", "message"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip() and not _is_noise(v.strip()):
                out.append(v.strip())
        for k, v in obj.items():
            if k in ("id", "traceId", "threadId", "spaceId", "userId", "pointer",
                      "table", "spaceViewId", "userEmail", "currentDatetime",
                      "createdAt", "timezone", "surface"):
                continue
            _collect_texts(v, out)


def _parse_ndjson(raw: str) -> str:
    """Extract agent answer from Notion patch stream.

    Success shape (captured 2026-09-08):
      {"type":"patch","v":[{... "v":{"type":"agent-inference",
        "value":[{"type":"text","content":"proxy"}]}}]}
      {"type":"patch","v":[{"o":"x","p":"/s/7/value/0/content","v":" ok"}]}
    Final answer = base content + appended fragments in order.
    """
    base = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "patch":
            continue
        for op in obj.get("v", []) or []:
            if not isinstance(op, dict):
                continue
            v = op.get("v")
            # New agent-inference block -> (re)start base (take last one)
            if isinstance(v, dict) and v.get("type") == "agent-inference":
                vals = v.get("value") or []
                if vals and isinstance(vals[0], dict) and isinstance(vals[0].get("content"), str):
                    base = vals[0]["content"]
            # Streaming append to content path
            elif op.get("o") == "x" and isinstance(op.get("p"), str) \
                    and op["p"].endswith("/content") and isinstance(v, str):
                base += v
    if base.strip():
        return base
    # Fallback: heuristic scan for longest natural-language string.
    candidates: list[str] = []
    for line in raw.splitlines():
        try:
            obj = _json.loads(line.strip())
        except Exception:
            continue
        buf: list[str] = []
        _collect_texts(obj, buf)
        candidates.extend(buf)
    if not candidates:
        return ""
    seen: set[str] = set()
    uniq = [t for t in candidates if t not in seen and not seen.add(t)]  # type: ignore
    uniq.sort(key=len)
    return uniq[-1] if uniq else ""


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [
        {"id": m["id"], "object": "model", "owned_by": m["owned_by"]} for m in AVAILABLE_MODELS
    ]}


@app.get("/__proxy_health")
async def health():
    return {"status": "ok", "upstream": RUN_URL, "space_id": SPACE_ID,
            "user_id": USER_ID, "models": sorted(_ALLOWED),
            "token_configured": bool(TOKEN_V2)}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    if not _check_auth(request):
        return _err(401, "Invalid local proxy key. Set Authorization: Bearer <proxy_api_key>")
    try:
        body = _json.loads(await request.body())
    except _json.JSONDecodeError:
        return _err(400, "Invalid JSON")
    model = body.get("model", "gpt-6-astra")
    if model not in _ALLOWED:
        return _err(400, f"Unknown model '{model}'. Allowed: {sorted(_ALLOWED)}")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _err(400, "'messages' must be a non-empty array")
    stream = bool(body.get("stream", False))
    if not TOKEN_V2 or not SPACE_ID or not USER_ID:
        return _err(401, "token_v2/space_id/user_id not configured. See credentials.example.json.")
    assert _client is not None

    thread_id = str(uuid.uuid4())
    notion_model = resolve_notion_model(model)
    log.info("[CHAT] %s -> %s", model, notion_model)
    payload = {
        "traceId": str(uuid.uuid4()),
        "spaceId": SPACE_ID,
        "transcript": _build_transcript(messages, notion_model, model),
        "threadId": thread_id,
        "threadParentPointer": {"table": "space", "id": SPACE_ID, "spaceId": SPACE_ID},
        "createThread": True,
        "debugOverrides": {"emitAgentSearchExtractedResults": True,
                           "cachedInferences": {}, "annotationInferences": {},
                           "emitInferences": False},
        "generateTitle": True,
        "saveAllThreadOperations": True,
        "setUnreadState": True,
        "createdSource": "ai_module",
        "threadType": "workflow",
        "isPartialTranscript": False,
        "asPatchResponse": True,
        "patchResponseVersion": 2,
        "isUserInAnySalesAssistedSpace": False,
        "isSpaceSalesAssisted": False,
        "supportsCustomAgentNudgeTranscriptStep": True,
    }
    try:
        import asyncio
        def _do_post():
            # Chrome TLS impersonation - Notion/Cloudflare rejects plain httpx.
            with CurlSession(impersonate="chrome") as s:
                r = s.post(RUN_URL, headers=_notion_headers(), json=payload, timeout=90)
                return r.status_code, r.text
        status, raw = await asyncio.to_thread(_do_post)
    except Exception as exc:
        return _err(502, f"Upstream request error: {exc}")
    if status in (401, 403):
        return JSONResponse(status_code=401, content={"error": {
            "message": "Notion rejected token_v2 (expired/invalid). Re-login in browser and refresh token_v2 in credentials.json. Upstream: " + raw[:500],
            "type": "authentication_error"}})
    if status != 200:
        return JSONResponse(status_code=status, content={"error": {
            "message": f"Notion upstream {status}: {raw[:1000]}", "type": "upstream_error"}})
    text = _parse_ndjson(raw)
    if not text.strip():
        return _err(502, "Notion returned no parseable text. Models/picker may have changed; check proxy logs and refresh token.")
    log.info("[CHAT] %s -> %d chars", model, len(text))
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    if not stream:
        return JSONResponse({
            "id": cid, "object": "chat.completion", "created": created, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def _chunks(s: str, n: int = 60):
        words = s.split(" ")
        buf = ""
        for w in words:
            buf += (w + " ")
            if len(buf) >= n:
                yield buf
                buf = ""
        if buf:
            yield buf

    async def sse():
        yield f'data: {_json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})}\n\n'
        for ch in _chunks(text):
            yield f'data: {_json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"content": ch}, "finish_reason": None}]})}\n\n'
        yield f'data: {_json.dumps({"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def catch_all(full_path: str):
    return _err(404, f"Unknown path '/{full_path}'. Use /v1/models, /v1/chat/completions, /__proxy_health")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level="info", access_log=False)
