
#!/usr/bin/env bash
# 脚本：pg_collect.sh
# 安装客户端：
# Amazon Linux / RHEL 系
# sudo yum install -y postgresql15
# Ubuntu / Debian 系
# sudo apt-get update
# sudo apt-get install -y postgresql-client
# 用环境变量传连接信息，避免把密码写进命令历史：
# export PGHOST="your-rds-endpoint.xxxxxx.ap-northeast-1.rds.amazonaws.com"
# export PGPORT="5432"
# export PGDATABASE="your_db"
# export PGUSER="readonly_user"
# export PGPASSWORD="your_password"
# 建议使用一个只读账号，但为了读取 pg_stat_activity.query、锁、统计视图，最好授予：
# GRANT pg_monitor TO readonly_user;
# 如果不能授予 pg_monitor，脚本仍可运行，但看到的 SQL 文本和其他用户会话信息可能不完整。

set -euo pipefail
OUT_DIR=""
LONG_QUERY_SECONDS="60"
LONG_XACT_SECONDS="300"
LOCK_WAIT_SECONDS="10"
usage() {
  cat <<EOF
Usage:
  $0 --out <output-dir> [--long-query-seconds 60] [--long-xact-seconds 300] [--lock-wait-seconds 10]
Required env:
  PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
Example:
  export PGHOST="xxx.ap-northeast-1.rds.amazonaws.com"
  export PGPORT="5432"
  export PGDATABASE="appdb"
  export PGUSER="readonly_user"
  export PGPASSWORD="secret"
$0 --out pg_diag_20260425_1100
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      OUT_DIR="$2"
      shift 2
      ;;
    --long-query-seconds)
      LONG_QUERY_SECONDS="$2"
      shift 2
      ;;
    --long-xact-seconds)
      LONG_XACT_SECONDS="$2"
      shift 2
      ;;
    --lock-wait-seconds)
      LOCK_WAIT_SECONDS="$2"
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
if [[ -z "$OUT_DIR" ]]; then
  usage
  exit 1
fi
for v in PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD; do
  if [[ -z "${!v:-}" ]]; then
    echo "Missing required env: $v"
    exit 1
  fi
done
command -v psql >/dev/null 2>&1 || { echo "psql not found"; exit 1; }
mkdir -p "$OUT_DIR/sql" "$OUT_DIR/results"
cat > "$OUT_DIR/connection_meta.json" <<EOF
{
  "pg_host": "$PGHOST",
  "pg_port": "$PGPORT",
  "pg_database": "$PGDATABASE",
  "pg_user": "$PGUSER",
  "collected_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "long_query_seconds": $LONG_QUERY_SECONDS,
  "long_xact_seconds": $LONG_XACT_SECONDS,
  "lock_wait_seconds": $LOCK_WAIT_SECONDS
}
EOF
run_sql() {
  local name="$1"
  local sql_file="$OUT_DIR/sql/${name}.sql"
  local out_file="$OUT_DIR/results/${name}.json"
echo "[INFO] Running ${name}"
  psql \
    --set ON_ERROR_STOP=off \
    --tuples-only \
    --no-align \
    --quiet \
    --command "\pset pager off" \
    --command "\copy ($(cat "$sql_file")) TO STDOUT WITH CSV HEADER" \
    2> "$OUT_DIR/results/${name}.stderr.txt" \
    | python3 -c '
import csv, json, sys
rows = list(csv.DictReader(sys.stdin))
print(json.dumps(rows, ensure_ascii=False, indent=2))
' > "$out_file" || true
}
cat > "$OUT_DIR/sql/00_version.sql" <<'SQL'
SELECT
  version() AS version,
  current_database() AS current_database,
  current_user AS current_user,
  now() AS db_now,
  pg_postmaster_start_time() AS postmaster_start_time,
  now() - pg_postmaster_start_time() AS postmaster_uptime
SQL
cat > "$OUT_DIR/sql/01_settings_core.sql" <<'SQL'
SELECT name, setting, unit, source, pending_restart, short_desc
FROM pg_settings
WHERE name IN (
  'max_connections',
  'shared_buffers',
  'work_mem',
  'maintenance_work_mem',
  'effective_cache_size',
  'statement_timeout',
  'idle_in_transaction_session_timeout',
  'lock_timeout',
  'deadlock_timeout',
  'log_min_duration_statement',
  'log_lock_waits',
  'track_activity_query_size',
  'track_io_timing',
  'shared_preload_libraries',
  'max_wal_size',
  'checkpoint_timeout',
  'checkpoint_completion_target',
  'autovacuum',
  'autovacuum_max_workers',
  'autovacuum_naptime',
  'autovacuum_vacuum_scale_factor',
  'autovacuum_analyze_scale_factor'
)
ORDER BY name
SQL
cat > "$OUT_DIR/sql/02_connection_summary.sql" <<'SQL'
SELECT
  state,
  wait_event_type,
  wait_event,
  count(*) AS sessions
FROM pg_stat_activity
GROUP BY state, wait_event_type, wait_event
ORDER BY sessions DESC
SQL
cat > "$OUT_DIR/sql/03_connection_by_user_app_client.sql" <<'SQL'
SELECT
  usename,
  application_name,
  client_addr::text AS client_addr,
  state,
  count(*) AS sessions,
  max(now() - backend_start) AS max_backend_age,
  max(now() - xact_start) AS max_xact_age,
  max(now() - query_start) AS max_query_age
FROM pg_stat_activity
GROUP BY usename, application_name, client_addr, state
ORDER BY sessions DESC, max_query_age DESC NULLS LAST
LIMIT 100
SQL
cat > "$OUT_DIR/sql/04_long_running_queries.sql" <<SQL
SELECT
  pid,
  usename,
  application_name,
  client_addr::text AS client_addr,
  state,
  wait_event_type,
  wait_event,
  now() - query_start AS query_age,
  now() - xact_start AS xact_age,
  left(regexp_replace(query, '[[:space:]]+', ' ', 'g'), 4000) AS query
FROM pg_stat_activity
WHERE query_start IS NOT NULL
  AND state <> 'idle'
  AND now() - query_start >= interval '${LONG_QUERY_SECONDS} seconds'
ORDER BY query_age DESC
LIMIT 100
SQL
cat > "$OUT_DIR/sql/05_long_transactions.sql" <<SQL
SELECT
  pid,
  usename,
  application_name,
  client_addr::text AS client_addr,
  state,
  wait_event_type,
  wait_event,
  now() - xact_start AS xact_age,
  now() - query_start AS query_age,
  left(regexp_replace(query, '[[:space:]]+', ' ', 'g'), 4000) AS query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
  AND now() - xact_start >= interval '${LONG_XACT_SECONDS} seconds'
ORDER BY xact_age DESC
LIMIT 100
SQL
cat > "$OUT_DIR/sql/06_idle_in_transaction.sql" <<'SQL'
SELECT
  pid,
  usename,
  application_name,
  client_addr::text AS client_addr,
  now() - xact_start AS xact_age,
  now() - state_change AS idle_age,
  wait_event_type,
  wait_event,
  left(regexp_replace(query, '[[:space:]]+', ' ', 'g'), 4000) AS last_query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
ORDER BY xact_age DESC
LIMIT 100
SQL
cat > "$OUT_DIR/sql/07_blocking_locks.sql" <<SQL
WITH blocked AS (
  SELECT
    a.pid AS blocked_pid,
    a.usename AS blocked_user,
    a.application_name AS blocked_application,
    a.client_addr::text AS blocked_client,
    now() - a.query_start AS blocked_query_age,
    now() - a.xact_start AS blocked_xact_age,
    a.wait_event_type,
    a.wait_event,
    left(regexp_replace(a.query, '[[:space:]]+', ' ', 'g'), 3000) AS blocked_query,
    pg_blocking_pids(a.pid) AS blocking_pids
  FROM pg_stat_activity a
  WHERE cardinality(pg_blocking_pids(a.pid)) > 0
)
SELECT
  b.blocked_pid,
  b.blocked_user,
  b.blocked_application,
  b.blocked_client,
  b.blocked_query_age,
  b.blocked_xact_age,
  b.wait_event_type,
  b.wait_event,
  blocker.pid AS blocking_pid,
  blocker.usename AS blocking_user,
  blocker.application_name AS blocking_application,
  blocker.client_addr::text AS blocking_client,
  now() - blocker.query_start AS blocking_query_age,
  now() - blocker.xact_start AS blocking_xact_age,
  left(regexp_replace(blocker.query, '[[:space:]]+', ' ', 'g'), 3000) AS blocking_query,
  b.blocked_query
FROM blocked b
JOIN LATERAL unnest(b.blocking_pids) AS bp(pid) ON true
JOIN pg_stat_activity blocker ON blocker.pid = bp.pid
WHERE b.blocked_query_age >= interval '${LOCK_WAIT_SECONDS} seconds'
   OR b.blocked_xact_age >= interval '${LOCK_WAIT_SECONDS} seconds'
ORDER BY b.blocked_query_age DESC NULLS LAST
LIMIT 100
SQL
cat > "$OUT_DIR/sql/08_locks_by_type.sql" <<'SQL'
SELECT
  locktype,
  mode,
  granted,
  relation::regclass::text AS relation,
  count(*) AS locks
FROM pg_locks
GROUP BY locktype, mode, granted, relation
ORDER BY granted ASC, locks DESC
LIMIT 200
SQL
cat > "$OUT_DIR/sql/09_waiting_sessions.sql" <<'SQL'
SELECT
  pid,
  usename,
  application_name,
  client_addr::text AS client_addr,
  state,
  wait_event_type,
  wait_event,
  now() - query_start AS query_age,
  now() - xact_start AS xact_age,
  left(regexp_replace(query, '[[:space:]]+', ' ', 'g'), 4000) AS query
FROM pg_stat_activity
WHERE wait_event IS NOT NULL
ORDER BY query_age DESC NULLS LAST
LIMIT 100
SQL
cat > "$OUT_DIR/sql/10_db_stats.sql" <<'SQL'
SELECT
  datname,
  numbackends,
  xact_commit,
  xact_rollback,
  blks_read,
  blks_hit,
  CASE WHEN blks_hit + blks_read = 0 THEN NULL
       ELSE round(100.0 * blks_hit / (blks_hit + blks_read), 2)
  END AS cache_hit_pct,
  tup_returned,
  tup_fetched,
  tup_inserted,
  tup_updated,
  tup_deleted,
  conflicts,
  deadlocks,
  temp_files,
  pg_size_pretty(temp_bytes) AS temp_bytes,
  blk_read_time,
  blk_write_time,
  stats_reset
FROM pg_stat_database
WHERE datname = current_database()
SQL
cat > "$OUT_DIR/sql/11_top_tables_activity.sql" <<'SQL'
SELECT
  schemaname,
  relname,
  n_live_tup,
  n_dead_tup,
  CASE WHEN n_live_tup + n_dead_tup = 0 THEN 0
       ELSE round(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 2)
  END AS dead_tuple_pct,
  seq_scan,
  seq_tup_read,
  idx_scan,
  idx_tup_fetch,
  n_tup_ins,
  n_tup_upd,
  n_tup_del,
  n_tup_hot_upd,
  vacuum_count,
  autovacuum_count,
  analyze_count,
  autoanalyze_count,
  last_vacuum,
  last_autovacuum,
  last_analyze,
  last_autoanalyze
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT 100
SQL
cat > "$OUT_DIR/sql/12_missing_index_candidates.sql" <<'SQL'
SELECT
  schemaname,
  relname,
  seq_scan,
  seq_tup_read,
  idx_scan,
  n_live_tup,
  pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_stat_user_tables
WHERE seq_scan > 0
ORDER BY seq_tup_read DESC
LIMIT 100
SQL
cat > "$OUT_DIR/sql/13_index_usage.sql" <<'SQL'
SELECT
  schemaname,
  relname,
  indexrelname,
  idx_scan,
  idx_tup_read,
  idx_tup_fetch,
  pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC, pg_relation_size(indexrelid) DESC
LIMIT 100
SQL
cat > "$OUT_DIR/sql/14_table_sizes.sql" <<'SQL'
SELECT
  schemaname,
  relname,
  pg_size_pretty(pg_relation_size(relid)) AS table_size,
  pg_size_pretty(pg_indexes_size(relid)) AS indexes_size,
  pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
  pg_total_relation_size(relid) AS total_bytes
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 100
SQL
cat > "$OUT_DIR/sql/15_replication_slots.sql" <<'SQL'
SELECT
  slot_name,
  plugin,
  slot_type,
  database,
  active,
  restart_lsn,
  confirmed_flush_lsn,
  wal_status,
  safe_wal_size
FROM pg_replication_slots
ORDER BY slot_name
SQL
cat > "$OUT_DIR/sql/16_replication_stat.sql" <<'SQL'
SELECT
  pid,
  usename,
  application_name,
  client_addr::text AS client_addr,
  state,
  sync_state,
  write_lag,
  flush_lag,
  replay_lag
FROM pg_stat_replication
ORDER BY application_name
SQL
cat > "$OUT_DIR/sql/17_bgwriter_checkpoint.sql" <<'SQL'
SELECT
  checkpoints_timed,
  checkpoints_req,
  checkpoint_write_time,
  checkpoint_sync_time,
  buffers_checkpoint,
  buffers_clean,
  maxwritten_clean,
  buffers_backend,
  buffers_backend_fsync,
  buffers_alloc,
  stats_reset
FROM pg_stat_bgwriter
SQL
cat > "$OUT_DIR/sql/18_statements_extension_exists.sql" <<'SQL'
SELECT
  EXISTS (
    SELECT 1
    FROM pg_extension
    WHERE extname = 'pg_stat_statements'
  ) AS pg_stat_statements_installed
SQL
cat > "$OUT_DIR/sql/19_top_statements.sql" <<'SQL'
SELECT
  userid::regrole::text AS user_name,
  dbid,
  calls,
  round(total_exec_time::numeric, 2) AS total_exec_time_ms,
  round(mean_exec_time::numeric, 2) AS mean_exec_time_ms,
  round(max_exec_time::numeric, 2) AS max_exec_time_ms,
  rows,
  shared_blks_hit,
  shared_blks_read,
  shared_blks_dirtied,
  shared_blks_written,
  temp_blks_read,
  temp_blks_written,
  left(regexp_replace(query, '[[:space:]]+', ' ', 'g'), 4000) AS query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC
LIMIT 100
SQL
cat > "$OUT_DIR/sql/20_top_statements_io.sql" <<'SQL'
SELECT
  userid::regrole::text AS user_name,
  calls,
  round(total_exec_time::numeric, 2) AS total_exec_time_ms,
  shared_blks_read,
  shared_blks_written,
  temp_blks_read,
  temp_blks_written,
  rows,
  left(regexp_replace(query, '[[:space:]]+', ' ', 'g'), 4000) AS query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY shared_blks_read + shared_blks_written + temp_blks_read + temp_blks_written DESC
LIMIT 100
SQL
cat > "$OUT_DIR/sql/21_prepared_xacts.sql" <<'SQL'
SELECT
  transaction,
  gid,
  prepared,
  owner,
  database
FROM pg_prepared_xacts
ORDER BY prepared
SQL
cat > "$OUT_DIR/sql/22_temp_file_settings.sql" <<'SQL'
SELECT name, setting, unit, source, short_desc
FROM pg_settings
WHERE name IN (
  'temp_file_limit',
  'log_temp_files',
  'work_mem',
  'hash_mem_multiplier'
)
ORDER BY name
SQL
cat > "$OUT_DIR/sql/23_vacuum_progress.sql" <<'SQL'
SELECT
  pid,
  datname,
  relid::regclass::text AS relation,
  phase,
  heap_blks_total,
  heap_blks_scanned,
  heap_blks_vacuumed,
  index_vacuum_count,
  max_dead_tuples,
  num_dead_tuples
FROM pg_stat_progress_vacuum
ORDER BY pid
SQL
cat > "$OUT_DIR/sql/24_index_create_progress.sql" <<'SQL'
SELECT
  pid,
  datname,
  relid::regclass::text AS table_name,
  index_relid::regclass::text AS index_name,
  command,
  phase,
  lockers_total,
  lockers_done,
  blocks_total,
  blocks_done,
  tuples_total,
  tuples_done
FROM pg_stat_progress_create_index
ORDER BY pid
SQL
cat > "$OUT_DIR/sql/25_active_ddl.sql" <<'SQL'
SELECT
  pid,
  usename,
  application_name,
  client_addr::text AS client_addr,
  state,
  now() - query_start AS query_age,
  wait_event_type,
  wait_event,
  left(regexp_replace(query, '[[:space:]]+', ' ', 'g'), 4000) AS query
FROM pg_stat_activity
WHERE query ~* '(^|\\s)(alter|create|drop|truncate|reindex|vacuum|cluster|refresh materialized view)\\s'
ORDER BY query_age DESC
LIMIT 100
SQL
for n in \
  00_version \
  01_settings_core \
  02_connection_summary \
  03_connection_by_user_app_client \
  04_long_running_queries \
  05_long_transactions \
  06_idle_in_transaction \
  07_blocking_locks \
  08_locks_by_type \
  09_waiting_sessions \
  10_db_stats \
  11_top_tables_activity \
  12_missing_index_candidates \
  13_index_usage \
  14_table_sizes \
  15_replication_slots \
  16_replication_stat \
  17_bgwriter_checkpoint \
  18_statements_extension_exists \
  21_prepared_xacts \
  22_temp_file_settings \
  23_vacuum_progress \
  24_index_create_progress \
  25_active_ddl
do
  run_sql "$n"
done
# pg_stat_statements may not exist or may not be visible.
if grep -q "true" "$OUT_DIR/results/18_statements_extension_exists.json"; then
  run_sql "19_top_statements"
  run_sql "20_top_statements_io"
else
  echo "[WARN] pg_stat_statements is not installed; skipping Top Statements."
fi
echo "[DONE] PostgreSQL diagnostic data collected: $OUT_DIR"

