#!/usr/bin/env bash

set -euo pipefail
DB_ID=""
REGION=""
HOURS="24"
PERIOD="300"
usage() {
  cat <<EOF
Usage:
  $0 --db <db-instance-identifier> --region <aws-region> [--hours 24] [--period 300]
Example:
  $0 --db prod-mysql-01 --region ap-northeast-1 --hours 24
Options:
  --db       RDS DBInstanceIdentifier
  --region   AWS region, e.g. ap-northeast-1
  --hours    Lookback window in hours. Default: 24
  --period   CloudWatch period in seconds. Default: 300
EOF
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
TS="$(date -u +%Y%m%d_%H%M%S)"
OUT_DIR="rds_diag_${DB_ID}_${TS}"
mkdir -p "$OUT_DIR/metrics" "$OUT_DIR/logs"
START_TIME="$(date -u -v-"${HOURS}"H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "${HOURS} hours ago" +%Y-%m-%dT%H:%M:%SZ)"
END_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DURATION_MIN="$(( HOURS * 60 ))"
echo "[INFO] DB_ID=$DB_ID"
echo "[INFO] REGION=$REGION"
echo "[INFO] START_TIME=$START_TIME"
echo "[INFO] END_TIME=$END_TIME"
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
echo "[INFO] Collecting CloudWatch alarms related to DB identifier..."
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
    --start-time "$START_TIME" \
    --end-time "$END_TIME" \
    --period "$PERIOD" \
    --statistics "$STAT1" "$STAT2" \
    --output json > "$OUT_DIR/metrics/${SAFE_NAME}.json" || true
done < "$OUT_DIR/metric_queries.txt"

echo "[INFO] Trying to download recent relevant DB log portions..."
python3 02_select_log.py "$OUT_DIR"

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
python3 03_pi_info.py "$OUT_DIR" > "$OUT_DIR/pi_info.env"

# shellcheck disable=SC1090
source "$OUT_DIR/pi_info.env"
if [[ "${PERF_INSIGHTS_ENABLED:-false}" == "true" && -n "${DBI_RESOURCE_ID:-}" ]]; then
  echo "[INFO] Performance Insights enabled. Collecting Top SQL and Top Waits..."
aws pi describe-dimension-keys \
    --region "$REGION" \
    --service-type RDS \
    --identifier "$DBI_RESOURCE_ID" \
    --start-time "$START_TIME" \
    --end-time "$END_TIME" \
    --metric db.load.avg \
    --group-by '{"Group":"db.sql","Limit":10}' \
    --output json > "$OUT_DIR/perf_insights_top_sql.json" || true
aws pi describe-dimension-keys \
    --region "$REGION" \
    --service-type RDS \
    --identifier "$DBI_RESOURCE_ID" \
    --start-time "$START_TIME" \
    --end-time "$END_TIME" \
    --metric db.load.avg \
    --group-by '{"Group":"db.wait_event","Limit":10}' \
    --output json > "$OUT_DIR/perf_insights_top_waits.json" || true
else
  echo "[INFO] Performance Insights is not enabled or DBI resource id missing."
fi
echo "[INFO] Generating report..."
python3 04_report.py "$OUT_DIR" "$DB_ID" "$REGION" "$START_TIME" "$END_TIME"

echo "[DONE] Diagnostic data collected."
echo "[DONE] Report: $OUT_DIR/report.md"
