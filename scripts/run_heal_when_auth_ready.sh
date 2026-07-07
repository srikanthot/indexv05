#!/usr/bin/env bash
set -euo pipefail
 
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT_DIR/reports"
LOG_FILE="$LOG_DIR/overnight_heal.log"
 
mkdir -p "$LOG_DIR"
 
export SSL_CERT_FILE="${SSL_CERT_FILE:-/c/Users/C90255306/Downloads/combined-ca.crt}"
export REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-/c/Users/C90255306/Downloads/combined-ca.crt}"
export PYTHONUNBUFFERED=1
 
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] auth-wait wrapper started" | tee -a "$LOG_FILE"
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] waiting for az token scope https://search.azure.us/.default" | tee -a "$LOG_FILE"
 
while true; do
  if az account get-access-token --scope https://search.azure.us/.default -o none >/dev/null 2>&1; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] auth ready; launching heal loop" | tee -a "$LOG_FILE"
    "$ROOT_DIR/.venv/Scripts/python.exe" -u "$ROOT_DIR/scripts/heal_until_done.py" \
      --config "$ROOT_DIR/deploy.config.json" \
      --max-iterations 24 \
      --wait-minutes 45 \
      --grace-minutes 10 \
      --max-per-iteration 46 \
      --reset-after-zero-minutes 12 \
      --repeat-stuck-fail-streak 10 \
      2>&1 | tee -a "$LOG_FILE"
    exit ${PIPESTATUS[0]}
  fi
 
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] auth not ready; retrying in 300s" | tee -a "$LOG_FILE"
  sleep 300
done
 