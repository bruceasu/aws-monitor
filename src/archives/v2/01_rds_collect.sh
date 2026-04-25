#!/usr/bin/env bash


set -euo pipefail
DB_ID=""
REGION=""
HOURS="24"
START_TIME=""
END_TIME=""
PERIOD="60"
usage() {
  cat <<EOF
Usage:
  $0 --db <db-instance-identifier> --region <aws-region> [--start <ISO8601>] [--end <ISO8601>] [--hours 24] [--period 60]
Examples:
  $0 --db prod-rds-01 --region ap-northeast-1 --start "2026-04-25T10:30:00+09:00" --end "2026-04-25T12:00:00+09:00"
  $0 --db prod-rds-01 --region ap-northeast-1 --hours 24
Options:
  --db       RDS DBInstanceIdentifier
  --region   AWS region, e.g. ap-northeast-1
  --start    Incident window start time, ISO8601. Example: 2026-04-25T10:30:00+09:00
  --end      Incident window end time, ISO8601. Example: 2026-04-25T12:00:00+09:00
  --hours    Lookback window in hours if --start/--end are not provided. Default: 24
  --period   CloudWatch period in seconds. Default: 60
EOF
}

to_utc() {
  local input="$1"
  python3 02_to_utc.py "$input"
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)
      DB_ID="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --start)
      START_TIME="$2"
      shift 2
      ;;
    --end)
      END_TIME="$2"
      shift 2
      ;;
    --hours)
      HOURS="$2"
      shift 2
      ;;
    --period)
      PERIOD="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done
if [[ -z "$DB_ID" || -z "$REGION" ]]; then
  usage
  exit 1
fi
command -v aws >/dev/null 2>&1 || { echo "aws cli not found"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not found"; exit 1; }
if [[ -n "$START_TIME" || -n "$END_TIME" ]]; then
  if [[ -z "$START_TIME" || -z "$END_TIME" ]]; then
    echo "--start and --end must be provided together"
    exit 1
  fi
  START_UTC="$(to_utc "$START_TIME")"
  END_UTC="$(to_utc "$END_TIME")"
else
  START_UTC="$(date -u -v-"${HOURS}"H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "${HOURS} hours ago" +%Y-%m-%dT%H:%M:%SZ)"
  END_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
# RDS describe-events uses duration in minutes more reliably across CLI versions.
DURATION_MIN="$(python3 03_duration_min.py "$START_UTC" "$END_UTC")"
TS="$(date -u +%Y%m%d_%H%M%S)"
OUT_DIR="rds_diag_${DB_ID}_${TS}"
mkdir -p "$OUT_DIR/metrics" "$OUT_DIR/logs"
cat > "$OUT_DIR/incident_window.json" <<EOF
{
  "db_instance_identifier": "$DB_ID",
  "region": "$REGION",
  "start_utc": "$START_UTC",
  "end_utc": "$END_UTC",
  "period_seconds": $PERIOD
}
EOF
echo "[INFO] DB_ID=$DB_ID"
echo "[INFO] REGION=$REGION"
echo "[INFO] START_UTC=$START_UTC"
echo "[INFO] END_UTC=$END_UTC"
echo "[INFO] PERIOD=$PERIOD"
echo "[INFO] OUT_DIR=$OUT_DIR"
echo "[INFO] Collecting RDS instance metadata..."
aws rds describe-db-instances \
  --region "$REGION" \
  --db-instance-identifier "$DB_ID" \
  --output json > "$OUT_DIR/db_instance.json"
echo "[INFO] Collecting RDS events..."
aws rds describe-events \
  --region "$REGION" \
  --source-type db-instance \
  --source-identifier "$DB_ID" \
  --start-time "$START_UTC" \
  --end-time "$END_UTC" \
  --duration "$DURATION_MIN" \
  --output json > "$OUT_DIR/events.json" || true
echo "[INFO] Collecting pending maintenance actions..."
aws rds describe-pending-maintenance-actions \
  --region "$REGION" \
  --filters "Name=db-instance-id,Values=$DB_ID" \
  --output json > "$OUT_DIR/pending_maintenance.json" || true
echo "[INFO] Collecting DB log file list..."
aws rds describe-db-log-files \
  --region "$REGION" \
  --db-instance-identifier "$DB_ID" \
  --output json > "$OUT_DIR/log_files.json" || true
echo "[INFO] Collecting CloudWatch alarms..."
aws cloudwatch describe-alarms \
  --region "$REGION" \
  --output json > "$OUT_DIR/cloudwatch_alarms_all.json" || true
cat > "$OUT_DIR/metric_queries.txt" <<EOF
CPUUtilization Percent Average Maximum
DatabaseConnections Count Average Maximum
FreeableMemory Bytes Average Minimum
FreeStorageSpace Bytes Average Minimum
SwapUsage Bytes Average Maximum
ReadLatency Seconds Average Maximum
WriteLatency Seconds Average Maximum
DiskQueueDepth Count Average Maximum
ReadIOPS Count/Second Average Maximum
WriteIOPS Count/Second Average Maximum
ReadThroughput Bytes/Second Average Maximum
WriteThroughput Bytes/Second Average Maximum
NetworkReceiveThroughput Bytes/Second Average Maximum
NetworkTransmitThroughput Bytes/Second Average Maximum
BurstBalance Percent Average Minimum
CPUCreditBalance Count Average Minimum
EBSIOBalance% Percent Average Minimum
EBSByteBalance% Percent Average Minimum
ReplicaLag Seconds Average Maximum
Deadlocks Count Sum Maximum
EOF
echo "[INFO] Collecting CloudWatch metrics..."
while read -r METRIC UNIT STAT1 STAT2; do
  [[ -z "$METRIC" ]] && continue
  SAFE_NAME="$(echo "$METRIC" | tr '%/' '__')"
aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/RDS \
    --metric-name "$METRIC" \
    --dimensions "Name=DBInstanceIdentifier,Value=$DB_ID" \
    --start-time "$START_UTC" \
    --end-time "$END_UTC" \
    --period "$PERIOD" \
    --statistics "$STAT1" "$STAT2" \
    --output json > "$OUT_DIR/metrics/${SAFE_NAME}.json" || true
done < "$OUT_DIR/metric_queries.txt"

echo "[INFO] Selecting relevant logs..."
python3 04_selected_logs.py "$OUT_DIR" "$START_UTC" "$END_UTC"

if [[ -s "$OUT_DIR/selected_logs.txt" ]]; then
  while read -r LOG_NAME; do
    [[ -z "$LOG_NAME" ]] && continue
    SAFE_LOG_NAME="$(echo "$LOG_NAME" | tr '/ ' '__')"
    echo "[INFO] Downloading log portion: $LOG_NAME"
    aws rds download-db-log-file-portion \
      --region "$REGION" \
      --db-instance-identifier "$DB_ID" \
      --log-file-name "$LOG_NAME" \
      --starting-token 0 \
      --output text > "$OUT_DIR/logs/${SAFE_LOG_NAME}.txt" || true
  done < "$OUT_DIR/selected_logs.txt"
fi

echo "[INFO] Collecting Performance Insights if enabled..."
python3 05_pi_info.py "$OUT_DIR" > "$OUT_DIR/pi_info.env"

# shellcheck disable=SC1090
source "$OUT_DIR/pi_info.env"
if [[ "${PERF_INSIGHTS_ENABLED:-false}" == "true" && -n "${DBI_RESOURCE_ID:-}" ]]; then
  echo "[INFO] Performance Insights enabled. Collecting Top SQL and Top Waits..."
aws pi describe-dimension-keys \
    --region "$REGION" \
    --service-type RDS \
    --identifier "$DBI_RESOURCE_ID" \
    --start-time "$START_UTC" \
    --end-time "$END_UTC" \
    --metric db.load.avg \
    --group-by '{"Group":"db.sql","Limit":15}' \
    --output json > "$OUT_DIR/perf_insights_top_sql.json" || true
aws pi describe-dimension-keys \
    --region "$REGION" \
    --service-type RDS \
    --identifier "$DBI_RESOURCE_ID" \
    --start-time "$START_UTC" \
    --end-time "$END_UTC" \
    --metric db.load.avg \
    --group-by '{"Group":"db.wait_event","Limit":15}' \
    --output json > "$OUT_DIR/perf_insights_top_waits.json" || true
aws pi describe-dimension-keys \
    --region "$REGION" \
    --service-type RDS \
    --identifier "$DBI_RESOURCE_ID" \
    --start-time "$START_UTC" \
    --end-time "$END_UTC" \
    --metric db.load.avg \
    --group-by '{"Group":"db.user","Limit":10}' \
    --output json > "$OUT_DIR/perf_insights_top_users.json" || true
aws pi describe-dimension-keys \
    --region "$REGION" \
    --service-type RDS \
    --identifier "$DBI_RESOURCE_ID" \
    --start-time "$START_UTC" \
    --end-time "$END_UTC" \
    --metric db.load.avg \
    --group-by '{"Group":"db.host","Limit":10}' \
    --output json > "$OUT_DIR/perf_insights_top_hosts.json" || true
else
  echo "[INFO] Performance Insights is not enabled or DBI resource id missing."
fi
echo "[DONE] Diagnostic data collected."
echo "[DONE] Directory: $OUT_DIR"
echo "[NEXT] Run:"
echo "  python3 ai_analyze_rds.py --input $OUT_DIR --output $OUT_DIR/ai_rca_report.md"

