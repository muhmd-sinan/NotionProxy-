@echo off
chcp 65001 >nul
title Notion Gateway
cd /d "%~dp0"

echo ==============================================
echo       Notion Gateway (Notion AI via token_v2)
echo   Config loaded from credentials.json
echo ==============================================
echo.

if not exist credentials.json (
  echo [ERROR] credentials.json not found.
  echo Copy credentials.example.json to credentials.json and add token_v2.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8330 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force; Write-Host ('Cleared previous process ' + $_.OwningProcess) }" 2>nul

echo Starting gateway on http://127.0.0.1:8330 ...
echo.
echo Endpoints:
echo   OpenAI compatible:    POST /v1/chat/completions
echo   List models:          GET  /v1/models
echo   Health check:         GET  /__proxy_health
echo.
echo Models (use these names in opencode as notion/ID):
echo   gpt-6-astra         GPT-6 Astra (verified, recommended)
echo   claude-opus-5       Opus 5 (hardest tasks)
echo   claude-sonnet-5     Sonnet 5
echo   gpt-5.6-sol         GPT-5.6 Sol (hardest tasks)
echo   gpt-5.6-terra       GPT-5.6 Terra
echo   gpt-5.6-luna        GPT-5.6 Luna
echo   kimi-k3             Kimi K3 (hardest tasks)
echo   + sonnet4.6, opus4.8/4.7/4.6, haiku4.5, gpt-5.5/5.4/5.2,
echo     gemini-3.1-pro, gemini-3.7-flash, grok-4.5/4.3,
echo     kimi-k2.6, deepseek-v4-pro/flash, glm-5.2
echo   Full list: GET /v1/models
echo.
echo NOTE: unofficial Notion AI bridge. token_v2 expires - refresh via browser cookie.
echo.

python "%~dp0notion_proxy.py"
pause
