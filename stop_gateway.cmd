@echo off
title Stop Notion Gateway
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8330 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force; Write-Host ('Stopped process ' + $_.OwningProcess) }"
echo Done.
pause
