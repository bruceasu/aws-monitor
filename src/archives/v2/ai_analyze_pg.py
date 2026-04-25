#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#脚本：ai_analyze_pg.py
# 这个脚本支持两种输入：
# 只分析 PostgreSQL 内部采集结果：
# python3 ai_analyze_pg.py \
#   --pg-input pg_diag_20260425_1100 \
#   --output pg_diag_20260425_1100/ai_pg_rca_report.md
# 同时合并上一轮 AWS/RDS 采集目录一起分析：
# python3 ai_analyze_pg.py \
#   --pg-input pg_diag_20260425_1100 \
#   --aws-input rds_diag_prod-rds-01_20260425_110000 \
#   --output final_rca_report.md

#安装依赖：
# pip install openai
# 设置 API Key：
# export OPENAI_API_KEY="你的 key"

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unicodedata import name
from openai import OpenAI

MAX_FILE_CHARS = 40000
MAX_TOTAL_CHARS = 220000

def load_text(path: Path, max_chars: Optional[int] = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + "\n...[TRUNCATED]..."
    return text

def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default if default is not None else {}
    
def load_result_json(pg_dir: Path, name: str) -> Any:
    return load_json(pg_dir / "results" / f"{name}.json", [])

def load_stderr(pg_dir: Path, name: str) -> str:
    return load_text(pg_dir / "results" / f"{name}.stderr.txt", 5000)

def compact_rows(rows: Any, limit: int = 50) -> Any:
    if isinstance(rows, list):
        return rows[:limit]
    return rows

def build_pg_evidence(pg_dir: Path) -> Dict[str, Any]:
    sql_names = [
        "00_version",
        "01_settings_core",
        "02_connection_summary",
        "03_connection_by_user_app_client",
        "04_long_running_queries",
        "05_long_transactions",
        "06_idle_in_transaction",
        "07_blocking_locks",
        "08_locks_by_type",
        "09_waiting_sessions",
        "10_db_stats",
        "11_top_tables_activity",
        "12_missing_index_candidates",
        "13_index_usage",
        "14_table_sizes",
        "15_replication_slots",
        "16_replication_stat",
        "17_bgwriter_checkpoint",
        "18_statements_extension_exists",
        "19_top_statements",
        "20_top_statements_io",
        "21_prepared_xacts",
        "22_temp_file_settings",
        "23_vacuum_progress",
        "24_index_create_progress",
        "25_active_ddl",
    ]
    evidence: Dict[str, Any] = {
        "connection_meta": load_json(pg_dir / "connection_meta.json", {}),
        "results": {},
        "errors": {},
    }

    for name in sql_names:
        rows = load_result_json(pg_dir, name)
        if rows:
            # Keep more rows for high-value sections.
            if name in [
                "04_long_running_queries",
                "05_long_transactions",
                "06_idle_in_transaction",
                "07_blocking_locks",
                "09_waiting_sessions",
                "19_top_statements",
                "20_top_statements_io",
            ]:
                evidence["results"][name] = compact_rows(rows, 100)
            else:
                evidence["results"][name] = compact_rows(rows, 50)
        err = load_stderr(pg_dir, name)
        if err.strip():
            evidence["errors"][name] = err.strip()
    return evidence

def build_aws_evidence(aws_dir: Optional[Path]) -> Dict[str, Any]:
    if not aws_dir:
        return {}
    wanted_files = [
        "incident_window.json",
        "db_instance.json",
        "events.json",
        "pending_maintenance.json",
        "perf_insights_top_sql.json",
        "perf_insights_top_waits.json",
        "perf_insights_top_users.json",
        "perf_insights_top_hosts.json",
    ]
    evidence: Dict[str, Any] = {
        "files": {},
        "metric_summaries": {},
        "log_findings": {},
    }
    for name in wanted_files:
        path = aws_dir / name
        if path.exists():
            evidence["files"][name] = load_json(path, {})
    metrics_dir = aws_dir / "metrics"
    if metrics_dir.exists():
        for path in sorted(metrics_dir.glob("*.json")):
            data = load_json(path, {})
            points = data.get("Datapoints", [])
            if not points:
                continue
            metric_name = data.get("Label") or path.stem
            summary: Dict[str, Any] = {
                "datapoints": len(points)
            }
            for stat in ["Average", "Maximum", "Minimum", "Sum"]:
                vals = [float(p[stat]) for p in points if stat in p and p[stat] is not None]
                if vals:
                    summary[stat] = {
                        "min": min(vals),
                        "avg": sum(vals) / len(vals),
                        "max": max(vals),
                    }
            evidence["metric_summaries"][metric_name] = summary
    logs_dir = aws_dir / "logs"
    if logs_dir.exists():
        patterns = [
            "FATAL",
            "PANIC",
            "ERROR",
            "restart",
            "shutdown",
            "crash",
            "out of memory",
            "Too many connections",
            "remaining connection slots",
            "deadlock",
            "lock wait",
            "terminating connection",
            "No space left",
        ]
        for path in sorted(logs_dir.glob("*.txt")):
            text = load_text(path, MAX_FILE_CHARS)
            matches: List[str] = []
            lower = text.lower()
            for pat in patterns:
                if pat.lower() in lower:
                    for line in text.splitlines():
                        if pat.lower() in line.lower():
                            matches.append(line[:1000])
                            if len(matches) >= 30:
                                break
            if matches:
                evidence["log_findings"][path.name] = matches[:30]
    return evidence

def truncate_evidence(evidence: Dict[str, Any]) -> str:
    text = json.dumps(evidence, ensure_ascii=False, indent=2)
    if len(text) <= MAX_TOTAL_CHARS:
        return text
    # Reduce high-volume sections.
    ev = dict(evidence)
    if "postgresql" in ev and "results" in ev["postgresql"]:
        results = dict(ev["postgresql"]["results"])
        for k in list(results.keys()):
            if isinstance(results[k], list):
                results[k] = results[k][:30]
        ev["postgresql"] = dict(ev["postgresql"])
        ev["postgresql"]["results"] = results
    text = json.dumps(ev, ensure_ascii=False, indent=2)
    if len(text) <= MAX_TOTAL_CHARS:
        return text
    return text[:MAX_TOTAL_CHARS] + "\n...[TRUNCATED]..."

def analyze(evidence_json: str, model: str) -> str:
    client = OpenAI()
    prompt = f"""
你是资深 PostgreSQL / AWS RDS SRE。请基于下面证据生成中文 RCA 分析报告。
分析目标：
1. 判断 PostgreSQL 是否存在导致 RDS 假死、不可用、必须重启的内部原因。
2. 重点分析：
   - 长 SQL
   - 长事务
   - idle in transaction
   - 锁等待 / blocking session
   - 连接数异常
   - wait_event / wait_event_type
   - pg_stat_statements Top SQL
   - IO heavy SQL
   - deadlocks
   - temp files / work_mem 压力
   - autovacuum 问题
   - checkpoint 压力
   - replication slot / WAL 堆积
   - DDL / VACUUM / CREATE INDEX 等维护操作
3. 如果同时提供 AWS/RDS 证据，请把 PostgreSQL 内部证据和 CloudWatch/RDS Events 关联起来。
4. 不要编造。没有证据时写“证据不足”。
5. 输出 Markdown，结构必须包含：
   - 结论摘要
   - 已确认事实
   - 高置信根因
   - 可能根因
   - 证据链
   - 立即止血措施
   - 1-3 天内修复项
   - 长期治理建议
   - 需要补充采集的信息
   - 推荐 SQL / 配置调整
6. 对危险操作要标注风险，例如 terminate backend、reindex、vacuum full、改参数重启等。
证据 JSON：
```json
{evidence_json}
"""
    response = client.responses.create(
        model=model,
        input=prompt,
    )
    return response.output_text

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-input", required=True, help="Directory generated by pg_collect.sh")
    parser.add_argument("--aws-input", default="", help="Optional directory generated by rds_collect.sh")
    parser.add_argument("--output", default="", help="Output markdown path")
    parser.add_argument("--model", default="gpt-5.4", help="OpenAI model name")
    parser.add_argument("--dump-evidence", action="store_true")
    args = parser.parse_args()
    pg_dir = Path(args.pg_input)
    if not pg_dir.exists():
        raise SystemExit(f"pg input dir not found: {pg_dir}")
    aws_dir = Path(args.aws_input) if args.aws_input else None
    if aws_dir and not aws_dir.exists():
        raise SystemExit(f"aws input dir not found: {aws_dir}")
    evidence = {
        "postgresql": build_pg_evidence(pg_dir),
        "aws_rds": build_aws_evidence(aws_dir) if aws_dir else {},
    }
    evidence_json = truncate_evidence(evidence)
    if args.dump_evidence:
        evidence_path = pg_dir / "ai_pg_evidence_pack.json"
        evidence_path.write_text(evidence_json, encoding="utf-8")
        print(f"[INFO] wrote evidence pack: {evidence_path}")
    output_path = Path(args.output) if args.output else pg_dir / "ai_pg_rca_report.md"
    print("[INFO] calling OpenAI API...")
    report = analyze(evidence_json, args.model)
    output_path.write_text(report, encoding="utf-8")
    print(f"[DONE] report written: {output_path}")

    
if __name__ == "__main__":
    main()
