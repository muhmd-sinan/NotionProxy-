@echo off
chcp 65001 >nul
title Notion - Refresh Credentials
cd /d "%~dp0"

echo ==============================================
echo    Notion Gateway - Refresh Credentials
echo  Opens Notion so you can sign in, then
echo  paste one cookie value - the rest is
echo  resolved and saved to credentials.json
echo ==============================================
echo.
echo Flags: --manual (no browser open)  --auto (Playwright)  --check (validate only)
echo.

python "%~dp0refresh_credentials.py" %*
echo.
pause
