#!/usr/bin/env python3
"""Fetch fresh Notion credentials and write them to credentials.json.

No browser automation needed. The script opens Notion in your default
browser, you sign in (if needed), paste one cookie value, and the script
resolves the workspace/user details itself and pastes everything into
credentials.json next to this file (old file is backed up first).

Usage:
    python refresh_credentials.py            # open browser + paste token_v2 (default)
    python refresh_credentials.py --manual   # same, without opening the browser
    python refresh_credentials.py --auto     # full automation via Playwright (needs pip install playwright)
    python refresh_credentials.py --check    # validate current credentials.json only
    python refresh_credentials.py --out credentials.work.json   # write to another file (multi-account)

Default/manual/check modes need only: curl_cffi (already in requirements.txt).
Auto mode additionally needs: pip install playwright (uses system Chrome/Edge).
"""
from __future__ import annotations

import argparse
import datetime
import getpass
import json as _json
import re
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).parent
DEFAULT_OUT = HERE / "credentials.json"
NOTION_BASE = "https://app.notion.com"


def _redact(token: str) -> str:
    if len(token) <= 12:
        return "***"
    return f"{token[:6]}...len:{len(token)}"


def parse_load_user_content(data: dict):
    """Extract users / spaces / space_views from loadUserContent recordMap."""
    rm = data.get("recordMap", data) if isinstance(data, dict) else {}
    users, spaces, views = [], [], []
    for uid, entry in (rm.get("notion_user") or {}).items():
        v = (entry.get("value") or {}).get("value", {}) if isinstance(entry, dict) else {}
        if isinstance(v, dict):
            users.append({"id": uid, "name": v.get("given_name") or v.get("name") or "",
                          "email": v.get("email") or ""})
    for sid, entry in (rm.get("space") or {}).items():
        v = (entry.get("value") or {}).get("value", {}) if isinstance(entry, dict) else {}
        if isinstance(v, dict):
            spaces.append({"space_id": sid, "name": v.get("name") or "",
                           "plan": v.get("plan_type") or v.get("subscription_tier") or ""})
    for svid, entry in (rm.get("space_view") or {}).items():
        v = (entry.get("value") or {}).get("value", {}) if isinstance(entry, dict) else {}
        if isinstance(v, dict) and v.get("space_id"):
            views.append({"svid": svid, "space_id": v.get("space_id") or ""})
    return users, spaces, views


def pick_space(spaces: list[dict], views: list[dict]) -> tuple[dict, str]:
    if not spaces:
        raise SystemExit("ERROR: no workspaces found for this session.")
    if len(spaces) == 1:
        chosen = spaces[0]
    else:
        print(f"\nFound {len(spaces)} workspaces:")
        for i, s in enumerate(spaces):
            print(f"  [{i}] {s['name'] or s['space_id'][:13]}  plan={s['plan']}")
        idx = input(f"Pick one (0-{len(spaces) - 1}): ").strip()
        chosen = spaces[int(idx)]
    svid = next((v["svid"] for v in views if v["space_id"] == chosen["space_id"]), "")
    return chosen, svid


def client_version_from_url(url: str, fallback: str) -> str:
    try:
        q = parse_qs(urlparse(url).query)
        if q.get("assetsVersion"):
            return q["assetsVersion"][0]
    except Exception:
        pass
    return fallback


def extract_token_v2(pasted: str) -> str:
    """Accept a raw token_v2 or a full document.cookie dump; return token_v2."""
    pasted = pasted.strip().strip('"').strip("'")
    if not pasted:
        return ""
    if "=" in pasted and (";" in pasted or pasted.startswith("token_v2=")):
        m = re.search(r"(?:^|;\s*)token_v2=([^;]+)", pasted)
        if m:
            return m.group(1).strip()
    return pasted


def server_fetch_user_content(token_v2: str) -> dict:
    """Resolve session details server-side with just the token_v2 cookie."""
    from curl_cffi.requests import Session as CurlSession
    with CurlSession(impersonate="chrome") as s:
        r = s.post(f"{NOTION_BASE}/api/v3/loadUserContent",
                   headers={"Content-Type": "application/json",
                            "Origin": "https://www.notion.so",
                            "Referer": "https://www.notion.so/ai",
                            "Cookie": f"token_v2={token_v2}",
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                                          "Chrome/126.0.0.0 Safari/537.36"},
                   json={}, timeout=30)
        r.raise_for_status()
        return r.json()


def write_credentials(out: Path, new: dict) -> None:
    old: dict = {}
    if out.exists():
        old = _json.loads(out.read_text(encoding="utf-8"))
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = out.parent / f"{out.stem}.bak-{stamp}.json"
        backup.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Backed up old file -> {backup.name}")
    # Preserve local gateway settings; only session fields are refreshed.
    merged = dict(old)
    merged.update(new)
    for k, default in (("notion_base", NOTION_BASE), ("proxy_api_key", "notion-local-key"),
                       ("host", "127.0.0.1"), ("port", 8330), ("log_level", "INFO")):
        merged.setdefault(k, default)
    out.write_text(_json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out.name}  (space='{merged.get('space_name')}', user='{merged.get('user_email')}')")


def run_manual(out: Path, open_browser: bool = True) -> None:
    if open_browser:
        print("Opening Notion in your browser - sign in if needed...")
        webbrowser.open(f"{NOTION_BASE}/ai")
    print("""
Sign in, then copy ONE of these (DevTools F12 -> Application -> Cookies):
  a) the token_v2 row's Value,  OR
  b) right-click the cookie list -> copy the whole cookie string.
Paste below (input is hidden):""")
    token = extract_token_v2(getpass.getpass("Paste: "))
    if not token:
        raise SystemExit("ERROR: empty token.")
    data = server_fetch_user_content(token)
    users, spaces, views = parse_load_user_content(data)
    if not users:
        raise SystemExit("ERROR: token rejected (no user found) - re-login and copy a fresh token_v2.")
    user = users[0]
    space, svid = pick_space(spaces, views)
    old_browser = _json.loads(out.read_text(encoding="utf-8")).get("notion_browser_id", "") if out.exists() else ""
    write_credentials(out, {
        "token_v2": token,
        "space_id": space["space_id"], "user_id": user["id"],
        "space_view_id": svid, "user_name": user["name"] or "user",
        "user_email": user["email"], "notion_browser_id": old_browser,
        "space_name": space["name"],
    })
    print(f"\ntoken_v2 saved ({_redact(token)}). Restart the gateway (stop_gateway.cmd -> start_gateway.cmd).")


def run_check(out: Path) -> int:
    if not out.exists():
        print(f"ERROR: {out.name} not found.")
        return 1
    cfg = _json.loads(out.read_text(encoding="utf-8"))
    token = cfg.get("token_v2", "")
    if not token:
        print("ERROR: token_v2 empty.")
        return 1
    try:
        data = server_fetch_user_content(token)
    except Exception as exc:
        print(f"EXPIRED/INVALID ({exc}). Run refresh_credentials.py to renew.")
        return 1
    users, spaces, _ = parse_load_user_content(data)
    if not users:
        print("EXPIRED/INVALID (no user in response). Run refresh_credentials.py to renew.")
        return 1
    print(f"VALID  user={users[0]['email'] or users[0]['id']}  "
          f"spaces={[(s['name'], s['plan']) for s in spaces]}  token={_redact(token)}")
    return 0


def run_auto(out: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright (python) is not installed. Either:")
        print("  pip install playwright     # then re-run (uses your Chrome/Edge, no download)")
        print("  or run: python refresh_credentials.py --manual")
        raise SystemExit(1)
    with sync_playwright() as p:
        browser = None
        for channel in ("chrome", "msedge"):
            try:
                browser = p.chromium.launch(channel=channel, headless=False)
                print(f"Opened {channel}.")
                break
            except Exception:
                continue
        if browser is None:
            print("No system Chrome/Edge found for Playwright. Run:")
            print("  python -m playwright install chromium")
            raise SystemExit(1)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(f"{NOTION_BASE}/ai")
        print("\nSign in to Notion in the opened window (if not already).")
        print("Waiting for login (up to 10 min) - you can close the window to abort...")
        token_v2 = browser_id = ""
        deadline = time.time() + 600
        while time.time() < deadline:
            try:
                cookies = ctx.cookies(NOTION_BASE)
            except Exception:
                cookies = []
            jar = {c["name"]: c["value"] for c in cookies}
            if jar.get("token_v2"):
                token_v2 = jar["token_v2"]
                browser_id = jar.get("notion_browser_id", "")
                break
            time.sleep(3)
        if not token_v2:
            browser.close()
            raise SystemExit("ERROR: timed out waiting for login (no token_v2 cookie).")
        print(f"Session captured (token_v2 {_redact(token_v2)}). Reading workspace info...")
        info_json = page.evaluate(
            """async () => {
              const r = await fetch('/api/v3/loadUserContent', {
                method: 'POST', headers: {'content-type': 'application/json'},
                body: '{}', credentials: 'include'});
              return JSON.stringify(await r.json());
            }""")
        client_version = client_version_from_url(page.url, "23.13.20260907.1809")
        browser.close()
    users, spaces, views = parse_load_user_content(_json.loads(info_json))
    if not users:
        raise SystemExit("ERROR: logged in but no user found - retry.")
    # Prefer the browser's active account when several are visible.
    user = users[0]
    space, svid = pick_space(spaces, views)
    write_credentials(out, {
        "token_v2": token_v2,
        "space_id": space["space_id"], "user_id": user["id"],
        "space_view_id": svid, "user_name": user["name"] or "user",
        "user_email": user["email"], "notion_browser_id": browser_id,
        "space_name": space["name"], "notion_base": NOTION_BASE,
        "client_version": client_version,
    })
    print("\nDone. Restart the gateway (stop_gateway.cmd -> start_gateway.cmd).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch fresh Notion credentials into credentials.json")
    ap.add_argument("--manual", action="store_true", help="paste token_v2 without opening the browser")
    ap.add_argument("--auto", action="store_true", help="full browser automation via Playwright (needs pip install playwright)")
    ap.add_argument("--check", action="store_true", help="validate current credentials.json only")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="target file (default: credentials.json)")
    args = ap.parse_args()
    out = Path(args.out)
    if args.check:
        return run_check(out)
    if args.auto:
        run_auto(out)
    else:
        # Default: open browser for sign-in, paste one cookie value (no extra deps).
        run_manual(out, open_browser=not args.manual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
