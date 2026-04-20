#!/usr/bin/env bash
# Auto-run pre-analysis for all PDFs in the configured container.
#
# Behavior:
#   - Lists every PDF (case-insensitive .pdf / .PDF).
#   - Skips PDFs that already have a cached final output.json.
#   - Processes only the rest. Per-figure caching means a killed
#     run resumes from where it stopped -- just run this again.
#   - Retries transient errors automatically (up to 3 attempts per
#     figure, tracked in blob cache). Permanent failures like
#     content-filter blocks are recorded and never retried.
#
# Usage:
#   ./scripts/run_preanalyze.sh                                      # defaults
#   ./scripts/run_preanalyze.sh --vision-parallel 48                 # more throughput
#   ./scripts/run_preanalyze.sh --concurrency 3                      # 3 PDFs at once
#   ./scripts/run_preanalyze.sh --max-attempts 5                     # more sweeps
#   ./scripts/run_preanalyze.sh --config path/to/deploy.config.json
#
# The wrapper loops up to --max-attempts times. Each pass is a full
# incremental run. If a pass leaves PDFs in a failed state, the next
# pass retries them. Once no failures remain, the wrapper exits.

set -u  # fail on unset vars; we deliberately do NOT use -e so Python
        # can write a traceback to stderr without killing the wrapper.

CONFIG="deploy.config.json"
VISION_PARALLEL=40
CONCURRENCY=2
MAX_ATTEMPTS=3

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG="$2"; shift 2 ;;
        --vision-parallel) VISION_PARALLEL="$2"; shift 2 ;;
        --concurrency) CONCURRENCY="$2"; shift 2 ;;
        --max-attempts) MAX_ATTEMPTS="$2"; shift 2 ;;
        -h|--help)
            head -n 22 "$0" | tail -n 21
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [ ! -f "$CONFIG" ]; then
    echo "Config file not found: $CONFIG" >&2
    exit 1
fi

# Move to repo root so relative paths to scripts/ work.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

echo
echo "============================================"
echo " Pre-analyze auto-runner"
echo "============================================"
echo "  Config:           $CONFIG"
echo "  Vision parallel:  $VISION_PARALLEL"
echo "  PDFs in parallel: $CONCURRENCY"
echo "  Max sweep passes: $MAX_ATTEMPTS"
echo

export PYTHONUNBUFFERED=1  # force Python to flush stdout line-by-line

# Preflight: verify environment before wasting hours on a bad setup.
python scripts/preflight.py --config "$CONFIG"
PREFLIGHT_EXIT=$?
if [ $PREFLIGHT_EXIT -ne 0 ]; then
    echo
    echo "Preflight failed. Fix the issues above before running preanalyze." >&2
    exit $PREFLIGHT_EXIT
fi

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
    echo
    echo "--- Pass $attempt of $MAX_ATTEMPTS ---"

    LOGFILE="$(mktemp)"
    # tee both to stdout (live) and to the file (for parsing afterward).
    # The pipeline's exit status is the last command's (tee), so use
    # PIPESTATUS to capture Python's exit code.
    python scripts/preanalyze.py \
        --config "$CONFIG" \
        --incremental \
        --vision-parallel "$VISION_PARALLEL" \
        --concurrency "$CONCURRENCY" 2>&1 | tee "$LOGFILE"
    PY_EXIT="${PIPESTATUS[0]}"

    if [ "$PY_EXIT" -ne 0 ]; then
        echo
        echo "Python exited with code $PY_EXIT. See the traceback above." >&2
        echo "Already-processed work is cached and safe. Fix the issue and re-run." >&2
        rm -f "$LOGFILE"
        exit "$PY_EXIT"
    fi

    # Pull the 'Done: X processed, Y skipped, Z failed' line and grab Z.
    FAILED="$(grep -oE 'Done: [0-9]+ processed, [0-9]+ skipped, [0-9]+ failed' "$LOGFILE" | awk '{print $(NF-1)}')"
    rm -f "$LOGFILE"
    if [ -z "$FAILED" ]; then
        FAILED=0  # 'Nothing to process.' path
    fi

    echo
    if [ "$FAILED" -eq 0 ]; then
        echo "Pass $attempt complete with no failures. Done."
        exit 0
    fi

    echo "$FAILED PDFs failed in pass $attempt."

    if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
        echo "Retrying..."
        sleep 5
    fi

    attempt=$((attempt + 1))
done

echo
echo "Reached max passes ($MAX_ATTEMPTS) with failures remaining." >&2
echo "Run again to continue, or check what is left with:" >&2
echo "  python scripts/preanalyze.py --config $CONFIG --status" >&2
exit 1
