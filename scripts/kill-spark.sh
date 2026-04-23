#!/usr/bin/env bash
# kill-spark.sh - clean teardown of the Spark stack.

set -uo pipefail

echo "=== Tearing down Spark stack ==="

# spawner-ui (vite)
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { \$_.CommandLine -match 'spawner-ui' } | ForEach-Object { Write-Host ('killing spawner-ui PID ' + \$_.ProcessId); Stop-Process -Id \$_.ProcessId -Force }" 2>/dev/null

# telegram-bot
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { \$_.CommandLine -match 'spark-telegram-bot' } | ForEach-Object { Write-Host ('killing telegram-bot PID ' + \$_.ProcessId); Stop-Process -Id \$_.ProcessId -Force }" 2>/dev/null

# builder python gateway (if running)
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'spark_intelligence' -and \$_.CommandLine -match 'gateway' } | ForEach-Object { Write-Host ('killing builder PID ' + \$_.ProcessId); Stop-Process -Id \$_.ProcessId -Force }" 2>/dev/null

echo "=== Done ==="
