#!/usr/bin/env bash
# resolve-boot-desktop.sh — print the user Desktop path for boot-spark.sh
set -euo pipefail

if [ -n "${USERPROFILE:-}" ]; then
  printf '%s/Desktop\n' "$USERPROFILE"
elif [ -n "${HOME:-}" ]; then
  printf '%s/Desktop\n' "$HOME"
else
  printf '/c/Users/%s/Desktop\n' "$(id -un)"
fi
