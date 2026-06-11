#!/usr/bin/env bash
# Sync today's US equity data with batched yfinance requests.
#
# Usage:
#   ./scripts/sync_today.sh               # prices + earnings + fundamentals
#   ./scripts/sync_today.sh prices        # prices only
#   ./scripts/sync_today.sh fundamentals  # fundamentals only
#   ./scripts/sync_today.sh earnings      # earnings only
#   ./scripts/sync_today.sh prices fundamentals  # combine any subset
#
# Environment overrides:
#   IMAGE       docker image tag          (default: tradingagents-market-tools:latest)
#   NETWORK     docker network name       (default: tradingnet)
#   ENV_FILE    env file path             (default: .env.tools)
#   SERIAL_JOBS    wait for each job group before starting the next one (default: 1)
#   JOB_COOLDOWN_SECONDS pause between serial job groups (default: 30)
#   CONCURRENCY      per-container concurrency for prices fallback mode (default: 2)
#   PRICE_SLEEP_SECONDS delay between yfinance price batches (default: 0.5)
#   PRICE_REQUEST_BUDGET symbols per price batch/container (default: 80)
#   PRICE_SHARDS     number of price shards to launch (default: 3)
#   PRICE_INCLUDE_TODAY include today's incomplete daily bar (default: 0)
#   PRICE_EXCLUDE_STALE_FAILURES skip stale symbols with repeated no-data failures (default: 1)
#   FUND_CONCURRENCY per-container concurrency for fundamentals (default: 4)
#   FUND_SLEEP_SECONDS post-symbol delay for fundamentals (default: 0.1)
#   FUND_SHARDS      number of fundamentals shards to launch (default: 3)
#   FUND_REQUESTS_PER_MINUTE per-container request budget (default: 120)
#   FUND_CALLS_PER_SYMBOL estimated yfinance calls per symbol (default: 4)
#   FUND_SKIP_RECENT_HOURS skip recently refreshed fundamentals (default: 168)
#   EARNINGS_CONCURRENCY number of earnings fetch workers (default: 3)
#   EARNINGS_SLEEP_SECONDS delay before each worker request (default: 1.0)
#   EARNINGS_LOOKBACK_DAYS rolling earnings lookback window (default: 10)
#   EARNINGS_LOOKAHEAD_DAYS rolling earnings lookahead window (default: 7)
#   EARNINGS_WINDOW_START explicit earnings window start YYYY-MM-DD (default: empty)
#   EARNINGS_WINDOW_END explicit earnings window end YYYY-MM-DD (default: empty)
#   EARNINGS_SKIP_RECENT_HOURS skip recently fetched earnings symbols (default: 12)
#   EARNINGS_FULL_UNIVERSE scan every active symbol for earnings (default: 0)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

IMAGE="${IMAGE:-tradingagents-market-tools:latest}"
NETWORK="${NETWORK:-tradingnet}"
ENV_FILE="${ENV_FILE:-.env.tools}"
SERIAL_JOBS="${SERIAL_JOBS:-1}"
JOB_COOLDOWN_SECONDS="${JOB_COOLDOWN_SECONDS:-30}"
CONCURRENCY="${CONCURRENCY:-2}"
PRICE_SLEEP_SECONDS="${PRICE_SLEEP_SECONDS:-0.5}"
PRICE_REQUEST_BUDGET="${PRICE_REQUEST_BUDGET:-80}"
PRICE_SHARDS="${PRICE_SHARDS:-3}"
PRICE_INCLUDE_TODAY="${PRICE_INCLUDE_TODAY:-0}"
PRICE_EXCLUDE_STALE_FAILURES="${PRICE_EXCLUDE_STALE_FAILURES:-1}"
FUND_CONCURRENCY="${FUND_CONCURRENCY:-4}"
FUND_SLEEP_SECONDS="${FUND_SLEEP_SECONDS:-0.1}"
FUND_SHARDS="${FUND_SHARDS:-3}"
FUND_REQUESTS_PER_MINUTE="${FUND_REQUESTS_PER_MINUTE:-120}"
FUND_CALLS_PER_SYMBOL="${FUND_CALLS_PER_SYMBOL:-4}"
FUND_SKIP_RECENT_HOURS="${FUND_SKIP_RECENT_HOURS:-168}"
EARNINGS_CONCURRENCY="${EARNINGS_CONCURRENCY:-3}"
EARNINGS_SLEEP_SECONDS="${EARNINGS_SLEEP_SECONDS:-1.0}"
EARNINGS_LOOKBACK_DAYS="${EARNINGS_LOOKBACK_DAYS:-10}"
EARNINGS_LOOKAHEAD_DAYS="${EARNINGS_LOOKAHEAD_DAYS:-7}"
EARNINGS_WINDOW_START="${EARNINGS_WINDOW_START:-}"
EARNINGS_WINDOW_END="${EARNINGS_WINDOW_END:-}"
EARNINGS_SKIP_RECENT_HOURS="${EARNINGS_SKIP_RECENT_HOURS:-12}"
EARNINGS_FULL_UNIVERSE="${EARNINGS_FULL_UNIVERSE:-0}"
SCHEMA_PREFLIGHT="${SCHEMA_PREFLIGHT:-1}"

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"

# 3 shards, roughly balanced (~3000-3500 symbols each).
SHARD_NAMES=(ai jq rz)
UNIVERSE_PREFIXES=("A,B,C,D,E" "G,J,K,L,M,N,O,P,Q" "F,H,I,R,S,T,U,V,W,X,Y,Z,0-9")
UNIVERSE_PREFIXES_ALL="A-Z,0-9"

# Pick which jobs to run (default = daily full refresh)
if [[ $# -eq 0 ]]; then
  JOBS=(prices earnings fundamentals)
else
  JOBS=("$@")
fi

echo "==> project:      $PROJECT_DIR"
echo "==> image:        $IMAGE"
echo "==> network:      $NETWORK"
echo "==> env file:     $ENV_FILE"
echo "==> serial jobs:  $SERIAL_JOBS"
echo "==> cooldown:     $JOB_COOLDOWN_SECONDS seconds"
echo "==> concurrency:  $CONCURRENCY per container"
echo "==> price sleep:  $PRICE_SLEEP_SECONDS seconds"
echo "==> price budget: $PRICE_REQUEST_BUDGET symbols per batch"
echo "==> price shards: $PRICE_SHARDS"
echo "==> price today:  $PRICE_INCLUDE_TODAY"
echo "==> price stale:  $PRICE_EXCLUDE_STALE_FAILURES"
echo "==> fund conc:    $FUND_CONCURRENCY per container"
echo "==> fund sleep:   $FUND_SLEEP_SECONDS seconds"
echo "==> fund shards:  $FUND_SHARDS"
echo "==> fund rpm:     $FUND_REQUESTS_PER_MINUTE per container"
echo "==> fund calls:   $FUND_CALLS_PER_SYMBOL per symbol"
echo "==> fund recent:  $FUND_SKIP_RECENT_HOURS hours"
echo "==> earn conc:    $EARNINGS_CONCURRENCY"
echo "==> earn sleep:   $EARNINGS_SLEEP_SECONDS seconds"
echo "==> earn window:  -${EARNINGS_LOOKBACK_DAYS}/+${EARNINGS_LOOKAHEAD_DAYS} days"
echo "==> earn dates:   ${EARNINGS_WINDOW_START:-auto}..${EARNINGS_WINDOW_END:-auto}"
echo "==> earn skip:    $EARNINGS_SKIP_RECENT_HOURS hours"
echo "==> earn full:    $EARNINGS_FULL_UNIVERSE"
echo "==> schema check: $SCHEMA_PREFLIGHT"
echo "==> jobs:         ${JOBS[*]}"
echo "==> log dir:      $LOG_DIR"
echo

# Preflight checks
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "error: image '$IMAGE' not found. Build it first: docker build -t $IMAGE ." >&2
  exit 1
fi
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "error: network '$NETWORK' not found. Create it: docker network create $NETWORK" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: env file '$ENV_FILE' not found." >&2
  exit 1
fi

if (( SCHEMA_PREFLIGHT == 1 )); then
  echo "==> running schema preflight"
  docker rm -f sync-schema-preflight >/dev/null 2>&1 || true
  docker run --rm --name sync-schema-preflight \
    --env-file "$ENV_FILE" \
    --network "$NETWORK" \
    "$IMAGE" \
    python -c "from cli.fetch_fmp_openbb_to_postgres import ensure_schema, open_db; conn = open_db(); ensure_schema(conn); conn.close()"
fi

CONTAINERS=()
LAUNCH_COUNT=0
OVERALL_STATUS=0
STAGGER_SECONDS="${STAGGER_SECONDS:-3}"

run_shard() {
  # $1 = container name, $2 = log file, $3... = python command
  local name="$1"; shift
  local logfile="$1"; shift
  local cmd="$*"

  # Stagger ensure_schema() across containers to avoid AccessExclusiveLock deadlock.
  # First container runs immediately; each subsequent one waits N seconds more.
  local delay=$(( LAUNCH_COUNT * STAGGER_SECONDS ))
  LAUNCH_COUNT=$(( LAUNCH_COUNT + 1 ))

  # Remove any lingering container with the same name
  docker rm -f "$name" >/dev/null 2>&1 || true

  echo "   -> starting $name (delay ${delay}s, log: $(basename "$logfile"))"
  docker run -d --name "$name" \
    --env-file "$ENV_FILE" \
    --network "$NETWORK" \
    -v "$LOG_DIR:/app/logs" \
    "$IMAGE" \
    sh -c "sleep $delay && { $cmd; code=\$?; echo \"\$code\" > /tmp/sync-exit-code; } 2>&1 | tee /app/logs/$(basename "$logfile"); exit \"\$(cat /tmp/sync-exit-code 2>/dev/null || echo 1)\"" >/dev/null

  CONTAINERS+=("$name")
}

wait_for_job_group() {
  # $1 = first container index for this job group, $2 = job name, $3 = cool down after wait
  local start_index="$1"
  local job_name="$2"
  local should_cooldown="$3"
  local total="${#CONTAINERS[@]}"

  if (( SERIAL_JOBS != 1 || start_index >= total )); then
    return
  fi

  echo
  echo "==> waiting for $job_name sync to finish before launching the next yfinance job"
  for (( i=start_index; i<total; i++ )); do
    local name="${CONTAINERS[$i]}"
    local exit_code
    if ! exit_code="$(docker wait "$name" 2>&1)"; then
      echo "   <- $name wait failed: $exit_code" >&2
      OVERALL_STATUS=1
      continue
    fi
    echo "   <- $name exited with code $exit_code"
    docker rm "$name" >/dev/null 2>&1 || true
    if [[ "$exit_code" != "0" ]]; then
      OVERALL_STATUS=1
    fi
  done

  if (( should_cooldown == 1 && JOB_COOLDOWN_SECONDS > 0 )); then
    echo "==> cooling down ${JOB_COOLDOWN_SECONDS}s to avoid Yahoo rate limits"
    sleep "$JOB_COOLDOWN_SECONDS"
  fi
}

job_prices() {
  local shard_count="$PRICE_SHARDS"
  local price_extra_args=""
  if (( PRICE_INCLUDE_TODAY == 1 )); then
    price_extra_args="$price_extra_args --include-today"
  fi
  if (( PRICE_EXCLUDE_STALE_FAILURES != 1 )); then
    price_extra_args="$price_extra_args --no-exclude-stale-failures"
  fi
  if (( shard_count < 1 )); then
    shard_count=1
  elif (( shard_count > 3 )); then
    shard_count=3
  fi

  echo "==> launching prices sync (${shard_count} shard(s))"
  if (( shard_count == 1 )); then
    local shard="${SHARD_NAMES[0]}"
    local name="sync-prices-$shard"
    local log="prices-${shard}-${STAMP}.log"
    run_shard "$name" "$log" \
      "python cli/sync_us_universe_to_postgres.py --use-db-universe --provider yfinance --symbol-prefixes $UNIVERSE_PREFIXES_ALL --concurrency $CONCURRENCY --sleep-seconds $PRICE_SLEEP_SECONDS --request-budget $PRICE_REQUEST_BUDGET $price_extra_args"
    return
  fi

  for (( i=0; i<shard_count; i++ )); do
    local shard="${SHARD_NAMES[$i]}"
    local prefixes="${UNIVERSE_PREFIXES[$i]}"
    local name="sync-prices-$shard"
    local log="prices-${shard}-${STAMP}.log"
    run_shard "$name" "$log" \
      "python cli/sync_us_universe_to_postgres.py --use-db-universe --provider yfinance --symbol-prefixes $prefixes --concurrency $CONCURRENCY --sleep-seconds $PRICE_SLEEP_SECONDS --request-budget $PRICE_REQUEST_BUDGET $price_extra_args"
  done
}

job_fundamentals() {
  local shard_count="$FUND_SHARDS"
  if (( shard_count < 1 )); then
    shard_count=1
  elif (( shard_count > 3 )); then
    shard_count=3
  fi

  echo "==> launching fundamentals sync (${shard_count} shard(s))"
  if (( shard_count == 1 )); then
    local shard="${SHARD_NAMES[0]}"
    local name="sync-fund-$shard"
    local log="fund-${shard}-${STAMP}.log"
    run_shard "$name" "$log" \
      "python cli/sync_fundamentals_to_postgres.py --use-db-universe --symbol-prefixes $UNIVERSE_PREFIXES_ALL --concurrency $FUND_CONCURRENCY --sleep-seconds $FUND_SLEEP_SECONDS --requests-per-minute $FUND_REQUESTS_PER_MINUTE --calls-per-symbol $FUND_CALLS_PER_SYMBOL --skip-recent-hours $FUND_SKIP_RECENT_HOURS"
    return
  fi

  for (( i=0; i<shard_count; i++ )); do
    local shard="${SHARD_NAMES[$i]}"
    local prefixes="${UNIVERSE_PREFIXES[$i]}"
    local name="sync-fund-$shard"
    local log="fund-${shard}-${STAMP}.log"
    run_shard "$name" "$log" \
      "python cli/sync_fundamentals_to_postgres.py --use-db-universe --symbol-prefixes $prefixes --concurrency $FUND_CONCURRENCY --sleep-seconds $FUND_SLEEP_SECONDS --requests-per-minute $FUND_REQUESTS_PER_MINUTE --calls-per-symbol $FUND_CALLS_PER_SYMBOL --skip-recent-hours $FUND_SKIP_RECENT_HOURS"
  done
}

job_earnings() {
  echo "==> launching earnings sync (rolling window; no sharding)"
  local earnings_args="--use-db-universe --sleep-seconds $EARNINGS_SLEEP_SECONDS"
  if (( EARNINGS_FULL_UNIVERSE != 1 )); then
    earnings_args="$earnings_args --calendar-window --lookback-days $EARNINGS_LOOKBACK_DAYS --lookahead-days $EARNINGS_LOOKAHEAD_DAYS"
    if [[ -n "$EARNINGS_WINDOW_START" ]]; then
      earnings_args="$earnings_args --window-start-date $EARNINGS_WINDOW_START"
    fi
    if [[ -n "$EARNINGS_WINDOW_END" ]]; then
      earnings_args="$earnings_args --window-end-date $EARNINGS_WINDOW_END"
    fi
  fi
  earnings_args="$earnings_args --skip-recent-hours $EARNINGS_SKIP_RECENT_HOURS"
  run_shard "sync-earnings" "earnings-${STAMP}.log" \
    "python cli/sync_earnings_to_postgres.py $earnings_args --concurrency $EARNINGS_CONCURRENCY"
}

for job_index in "${!JOBS[@]}"; do
  job="${JOBS[$job_index]}"
  start_index="${#CONTAINERS[@]}"
  if (( SERIAL_JOBS == 1 )); then
    LAUNCH_COUNT=0
  fi
  case "$job" in
    prices)       job_prices ;;
    fundamentals) job_fundamentals ;;
    earnings)     job_earnings ;;
    *) echo "error: unknown job '$job' (valid: prices fundamentals earnings)" >&2; exit 1 ;;
  esac
  should_cooldown=0
  if (( job_index < ${#JOBS[@]} - 1 )); then
    should_cooldown=1
  fi
  wait_for_job_group "$start_index" "$job" "$should_cooldown"
done

if [[ ${#CONTAINERS[@]} -eq 0 ]]; then
  echo "no containers launched"
  exit 0
fi

echo
if (( SERIAL_JOBS == 1 )); then
  echo "==> ${#CONTAINERS[@]} container(s) completed."
  echo
  echo "    logs:     ls $LOG_DIR/*-${STAMP}.log"
  echo "    tail:     tail -f $LOG_DIR/*-${STAMP}.log"
else
  echo "==> ${#CONTAINERS[@]} container(s) launched in background:"
  docker ps --filter "name=sync-" --format 'table {{.Names}}\t{{.Status}}'
  echo
  echo "==> script exiting. containers keep running."
  echo
  echo "    status:   docker ps --filter name=sync-"
  echo "    tail:     tail -f $LOG_DIR/*-${STAMP}.log"
  echo "    wait:     docker wait ${CONTAINERS[*]}"
fi
echo
exit "$OVERALL_STATUS"
