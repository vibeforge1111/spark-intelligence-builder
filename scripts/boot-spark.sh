#!/usr/bin/env bash
# boot-spark.sh — bring up the full Spark stack (spawner-ui, builder, telegram-bot)
# with port-conflict detection and health gating. Safe to re-run.
#
# Usage:
#   ./scripts/boot-spark.sh              # boot all
#   ./scripts/boot-spark.sh --no-bot     # skip telegram-bot (avoid live poll)
#   ./scripts/boot-spark.sh --status     # just check what's up
#
# Expected services:
#   spawner-ui      :4174
#   telegram-relay  :8788  (inside spark-telegram-bot)
#   telegram-webhk  :8907  (webhook mode, optional)

set -uo pipefail

DESKTOP="/c/Users/USER/Desktop"
SPAWNER_DIR="$DESKTOP/spawner-ui"
BOT_DIR="$DESKTOP/spark-telegram-bot"
BUILDER_DIR="$DESKTOP/spark-intelligence-builder"
LOG_DIR="$BUILDER_DIR/.boot-logs"
mkdir -p "$LOG_DIR"

SKIP_BOT=0
STATUS_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-bot) SKIP_BOT=1 ;;
    --status) STATUS_ONLY=1 ;;
  esac
done

check_port() {
  local port="$1"
  curl -s -o /dev/null -w "%{http_code}" -m 3 "http://127.0.0.1:$port/" 2>/dev/null
}

wait_for() {
  local name="$1" port="$2" path="$3" deadline="$4"
  local n=0
  while [ "$n" -lt "$deadline" ]; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -m 2 "http://127.0.0.1:$port$path" 2>/dev/null)
    if [ "$code" = "200" ]; then
      echo "[$name] ready (:$port)"
      return 0
    fi
    sleep 1; n=$((n+1))
  done
  echo "[$name] TIMEOUT after ${deadline}s (:$port)"
  return 1
}

status_report() {
  echo "=== Spark stack status ==="
  for p in 4174 8788 8907 8011; do
    code=$(check_port "$p")
    case "$p" in
      4174) label="spawner-ui" ;;
      8788) label="telegram-relay" ;;
      8907) label="telegram-webhook" ;;
      8011) label="builder-harness" ;;
    esac
    if [ "$code" = "200" ] || [ "$code" = "404" ]; then
      echo "  :$p $label -> UP (http $code)"
    else
      echo "  :$p $label -> down"
    fi
  done
}

if [ "$STATUS_ONLY" = "1" ]; then
  status_report
  exit 0
fi

echo "=== Booting Spark stack ==="

# --- spawner-ui ---
code=$(check_port 4174)
if [ "$code" = "200" ] || [ "$code" = "404" ]; then
  echo "[spawner-ui] already up on :4174"
else
  echo "[spawner-ui] starting..."
  (cd "$SPAWNER_DIR" && nohup npm run dev -- --port 4174 --host 127.0.0.1 >"$LOG_DIR/spawner-ui.log" 2>&1 &)
  wait_for spawner-ui 4174 /api/providers 45 || { echo "spawner-ui failed to start - see $LOG_DIR/spawner-ui.log"; exit 1; }
fi

# --- telegram-bot ---
if [ "$SKIP_BOT" = "1" ]; then
  echo "[telegram-bot] skipped (--no-bot)"
else
  # refuse to start if already polling somewhere else
  pid_polling=$(powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { \$_.CommandLine -match 'spark-telegram-bot' } | Select-Object -ExpandProperty ProcessId" 2>/dev/null | tr -d '\r' | head -1)
  if [ -n "$pid_polling" ]; then
    echo "[telegram-bot] already running (PID $pid_polling)"
  else
    echo "[telegram-bot] starting..."
    (cd "$BOT_DIR" && nohup npm start >"$LOG_DIR/telegram-bot.log" 2>&1 &)
    sleep 3
    if grep -qE "409|conflict|terminated by" "$LOG_DIR/telegram-bot.log"; then
      echo "[telegram-bot] CONFLICT - another instance is polling this bot token. Check $LOG_DIR/telegram-bot.log"
      exit 1
    fi
    echo "[telegram-bot] launched (log: $LOG_DIR/telegram-bot.log)"
  fi
fi

echo
status_report
echo
echo "=== Done ==="
