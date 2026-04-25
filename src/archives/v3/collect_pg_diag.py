#!/usr/bin/env python3

# pip install psycopg2-binary openai
# 如果后面要把 AWS/RDS 采集也改成 Python，再加：
# pip install boto3

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import psycopg2
import psycopg2.extras

QUERIES = {
    "version": """
        SELECT
          version() AS version,
          current_database() AS current_database,
          current_user AS current_user,
          now() AS db_now,
          pg_postmaster_start_time() AS postmaster_start_time,
          now() - pg_postmaster_start_time() AS postmaster_uptime
    """,
"settings_core": """
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
          'autovacuum_naptime'
        )
        ORDER BY name
    """,
"connection_summary": """
        SELECT
          state,
          wait_event_type,
          wait_event,
          count(*) AS sessions
        FROM pg_stat_activity
        GROUP BY state, wait_event_type, wait_event
        ORDER BY sessions DESC
    """,
"connections_by_user_app_client": """
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
    """,
"long_running_queries": """
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
          AND now() - query_start >= (%(long_query_seconds)s || ' seconds')::interval
        ORDER BY query_age DESC
        LIMIT 100
    """,
"long_transactions": """
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
          AND now() - xact_start >= (%(long_xact_seconds)s || ' seconds')::interval
        ORDER BY xact_age DESC
        LIMIT 100
    """,
"idle_in_transaction": """
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
    """,
"blocking_locks": """
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
        ORDER BY b.blocked_query_age DESC NULLS LAST
        LIMIT 100
    """,
"waiting_sessions": """
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
    """,
"locks_by_type": """
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
    """,
"db_stats": """
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
    """,
"top_tables_activity": """
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
    """,
"missing_index_candidates": """
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
    """,
"table_sizes": """
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
    """,
"replication_slots": """
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
    """,
"bgwriter_checkpoint": """
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
    """,
"active_ddl": """
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
    """,
}
PG_STAT_STATEMENTS_QUERIES = {
    "pg_stat_statements_top_total_time": """
        SELECT
          userid::regrole::text AS user_name,
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
    """,
"pg_stat_statements_top_io": """
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
    """,
}

def json_default(obj):
    return str(obj)

def connect():
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        connect_timeout=10,
    )

def query_rows(conn, sql, params):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())

def write_json(path: Path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )

def has_pg_stat_statements(conn) -> bool:
    rows = query_rows(
        conn,
        """
        SELECT EXISTS (
          SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
        ) AS exists
        """,
        {},
    )
    return bool(rows and rows[0]["exists"])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--long-query-seconds", type=int, default=60)
    parser.add_argument("--long-xact-seconds", type=int, default=300)
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    params = {
        "long_query_seconds": args.long_query_seconds,
        "long_xact_seconds": args.long_xact_seconds,
    }
    meta = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "pg_host": os.environ.get("PGHOST"),
        "pg_port": os.environ.get("PGPORT", "5432"),
        "pg_database": os.environ.get("PGDATABASE"),
        "pg_user": os.environ.get("PGUSER"),
        "long_query_seconds": args.long_query_seconds,
        "long_xact_seconds": args.long_xact_seconds,
    }
    write_json(out_dir / "metadata.json", meta)
    conn = connect()
    errors = {}
    try:
        for name, sql in QUERIES.items():
            print(f"[INFO] collecting {name}")
            try:
                rows = query_rows(conn, sql, params)
                write_json(out_dir / f"{name}.json", rows)
            except Exception as e:
                conn.rollback()
                errors[name] = str(e)
                write_json(out_dir / f"{name}.error.json", {"error": str(e)})
        if has_pg_stat_statements(conn):
            for name, sql in PG_STAT_STATEMENTS_QUERIES.items():
                print(f"[INFO] collecting {name}")
                try:
                    rows = query_rows(conn, sql, params)
                    write_json(out_dir / f"{name}.json", rows)
                except Exception as e:
                    conn.rollback()
                    errors[name] = str(e)
                    write_json(out_dir / f"{name}.error.json", {"error": str(e)})
        else:
            write_json(out_dir / "pg_stat_statements_missing.json", {
                "installed": False,
                "note": "pg_stat_statements is not installed or not visible to current user."
            })
    finally:
        conn.close()

    write_json(out_dir / "errors.json", errors)
    print(f"[DONE] PostgreSQL diagnostics written to: {out_dir}")
    
if __name__ == "__main__":
    main()
