#!/usr/bin/env bash
# boot-spark.sh — bring up the local Spawner UI and, only when explicitly
# requested, the Telegram bot. Paths come from this checkout or explicit env.
#
# Usage:
#   ./scripts/boot-spark.sh              # start Spawner; never start Telegram
#   ./scripts/boot-spark.sh --with-bot   # explicitly request one local bot
#   ./scripts/boot-spark.sh --status     # read local port status only
#   ./scripts/boot-spark.sh --plan       # print resolved paths; change nothing
#
# Optional path authority:
#   SPARK_BUILDER_DIR
#   SPARK_SPAWNER_DIR
#   SPARK_TELEGRAM_BOT_DIR
#   SPARK_BOOT_LOG_DIR

set -uo pipefail

BUILDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BUILDER_DIR="${SPARK_BUILDER_DIR:-$BUILDER_DIR}"
WORKSPACE_DIR="$(dirname "$BUILDER_DIR")"
SPAWNER_DIR="${SPARK_SPAWNER_DIR:-$WORKSPACE_DIR/vibeship-spawner-ui}"
BOT_DIR="${SPARK_TELEGRAM_BOT_DIR:-$WORKSPACE_DIR/spark-telegram-bot}"
LOG_DIR="${SPARK_BOOT_LOG_DIR:-$BUILDER_DIR/.boot-logs}"

WITH_BOT=0
STATUS_ONLY=0
PLAN_ONLY=0

usage() {
  sed -n '3,14p' "${BASH_SOURCE[0]}"
}

for arg in "$@"; do
  case "$arg" in
    --with-bot) WITH_BOT=1 ;;
    --no-bot) WITH_BOT=0 ;; # backward-compatible no-op; safe is now default
    --status) STATUS_ONLY=1 ;;
    --plan) PLAN_ONLY=1 ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

check_port() {
  local port="$1"
  curl -s -o /dev/null -w "%{http_code}" -m 3 "http://127.0.0.1:$port/" 2>/dev/null
}

wait_for() {
  local name="$1" port="$2" path="$3" deadline="$4"
  local n=0 code
  while [ "$n" -lt "$deadline" ]; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -m 2 "http://127.0.0.1:$port$path" 2>/dev/null)
    if [ "$code" = "200" ]; then
      echo "[$name] ready (:$port)"
      return 0
    fi
    sleep 1
    n=$((n + 1))
  done
  echo "[$name] TIMEOUT after ${deadline}s (:$port)"
  return 1
}

status_report() {
  local p code label
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

print_plan() {
  echo "Builder: $BUILDER_DIR"
  echo "Spawner: $SPAWNER_DIR"
  echo "Telegram: $BOT_DIR"
  echo "Logs: $LOG_DIR"
  if [ "$WITH_BOT" = "1" ]; then
    echo "Telegram launch: explicitly requested; local-owner check required"
  else
    echo "Telegram launch: skipped (default; pass --with-bot to request a local launch)"
  fi
}

require_directory() {
  local label="$1" path="$2"
  if [ ! -d "$path" ]; then
    echo "[$label] repository not found; set its SPARK_*_DIR override" >&2
    return 1
  fi
}

find_local_telegram_pid() {
  local pid
  if command -v powershell.exe >/dev/null 2>&1; then
    pid=$(powershell.exe -NoProfile -Command \
      "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { \$_.CommandLine -match 'spark-telegram-bot' } | Select-Object -ExpandProperty ProcessId" \
      2>/dev/null | tr -d '\r' | head -1)
    if [ -n "$pid" ]; then
      printf '%s\n' "$pid"
      return 0
    fi
    return 1
  fi
  if command -v pgrep >/dev/null 2>&1; then
    pid=$(pgrep -f '[s]park-telegram-bot' 2>/dev/null | head -1)
    if [ -n "$pid" ]; then
      printf '%s\n' "$pid"
      return 0
    fi
    return 1
  fi
  return 2
}

if [ "$PLAN_ONLY" = "1" ]; then
  print_plan
  exit 0
fi

if [ "$STATUS_ONLY" = "1" ]; then
  status_report
  exit 0
fi

require_directory builder "$BUILDER_DIR" || exit 1
require_directory spawner-ui "$SPAWNER_DIR" || exit 1
mkdir -p "$LOG_DIR"

echo "=== Booting local Spark services ==="

code=$(check_port 4174)
if [ "$code" = "200" ] || [ "$code" = "404" ]; then
  echo "[spawner-ui] already up on :4174"
else
  echo "[spawner-ui] starting..."
  (cd "$SPAWNER_DIR" && nohup npm run dev -- --port 4174 --host 127.0.0.1 >"$LOG_DIR/spawner-ui.log" 2>&1 &)
  wait_for spawner-ui 4174 /api/providers 45 || {
    echo "spawner-ui failed to start - see $LOG_DIR/spawner-ui.log"
    exit 1
  }
fi

if [ "$WITH_BOT" != "1" ]; then
  echo "[telegram-bot] skipped (default; pass --with-bot to request a local launch)"
else
  require_directory telegram-bot "$BOT_DIR" || exit 1
  if pid_polling=$(find_local_telegram_pid); then
    echo "[telegram-bot] already running locally (PID $pid_polling)"
  else
    poller_check=$?
    if [ "$poller_check" = "2" ]; then
      echo "[telegram-bot] refused: no supported local process probe is available" >&2
      exit 1
    fi
    echo "[telegram-bot] starting after explicit request..."
    (cd "$BOT_DIR" && nohup npm start >"$LOG_DIR/telegram-bot.log" 2>&1 &)
    sleep 3
    if grep -qE "409|conflict|terminated by" "$LOG_DIR/telegram-bot.log"; then
      echo "[telegram-bot] conflict detected; stop this local process and inspect its owner state"
      exit 1
    fi
    echo "[telegram-bot] launched locally (log: $LOG_DIR/telegram-bot.log)"
  fi
fi

echo
status_report
echo
echo "=== Done ==="
