#!/usr/bin/env python3

# 这个脚本可以读取：
# 
# incident_20260425_1100/
#   aws_rds/
#   postgresql/
#
# 安装依赖
# pip install openai
# 设置环境变量
# export OPENAI_API_KEY="你的 key"
# 
# python3 analyze_incident_ai.py \
#   --aws-input incident_20260425_1100/aws_rds \
#   --pg-input incident_20260425_1100/postgresql \
#   --output incident_20260425_1100/final_rca_report.md \
#   --model gpt-4.1


import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional
from unicodedata import name
from xmlrpc import client

from openai import OpenAI


MAX_TOTAL_CHARS = 220_000


AWS_FILES = [
    "metadata.json",
    "db_instance.json",
    "events.json",
    "pending_maintenance.json",
    "cloudwatch_alarms.json",
    "metric_summaries.json",
    "performance_insights.json",
    "selected_logs.json",
]

PG_FILES = [
    "metadata.json",
    "version.json",
    "settings_core.json",
    "connection_summary.json",
    "connections_by_user_app_client.json",
    "long_running_queries.json",
    "long_transactions.json",
    "idle_in_transaction.json",
    "blocking_locks.json",
    "waiting_sessions.json",
    "locks_by_type.json",
    "db_stats.json",
    "top_tables_activity.json",
    "missing_index_candidates.json",
    "table_sizes.json",
    "replication_slots.json",
    "bgwriter_checkpoint.json",
    "active_ddl.json",
    "pg_stat_statements_top_total_time.json",
    "pg_stat_statements_top_io.json",
    "pg_stat_statements_missing.json",
    "errors.json",
]


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        return {"error": f"failed to read {path.name}: {e}"}


def load_text(path: Path, max_chars: int = 20_000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[TRUNCATED]..."
    return text


def collect_files(input_dir: Path, names: list[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for name in names:
        data = load_json(input_dir / name)
        if data is not None:
            result[name] = data
    return result


def collect_aws_logs(aws_dir: Optional[Path]) -> Dict[str, str]:
    if not aws_dir:
        return {}
    log_dir = aws_dir / "logs"
    if not log_dir.exists():
        return {}

    result: Dict[str, str] = {}
    keywords = [
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
        "lock",
        "terminating connection",
        "No space left",
    ]

    for path in sorted(log_dir.glob("*.txt")):
        text = load_text(path, 40_000)
        lines = []
        for line in text.splitlines():
            lower = line.lower()
            if any(k.lower() in lower for k in keywords):
                lines.append(line[:1200])
            if len(lines) >= 80:
                break
        if lines:
            result[path.name] = "\n".join(lines)

    return result


def build_evidence(aws_dir: Optional[Path], pg_dir: Optional[Path]) -> Dict[str, Any]:
    evidence: Dict[str, Any] = {}

    if aws_dir:
        evidence["aws_rds"] = collect_files(aws_dir, AWS_FILES)
        evidence["aws_rds"]["log_findings"] = collect_aws_logs(aws_dir)

    if pg_dir:
        evidence["postgresql"] = collect_files(pg_dir, PG_FILES)

    return evidence


def compact_evidence(evidence: Dict[str, Any]) -> str:
    text = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    if len(text) <= MAX_TOTAL_CHARS:
        return text

    compacted = json.loads(json.dumps(evidence, ensure_ascii=False, default=str))

    def trim_lists(obj: Any) -> Any:
        if isinstance(obj, list):
            return [trim_lists(x) for x in obj[:50]]
        if isinstance(obj, dict):
            return {k: trim_lists(v) for k, v in obj.items()}
        return obj

    compacted = trim_lists(compacted)
    text = json.dumps(compacted, ensure_ascii=False, indent=2, default=str)

    if len(text) <= MAX_TOTAL_CHARS:
        return text

    return text[:MAX_TOTAL_CHARS] + "\n...[TRUNCATED]..."


def analyze_with_ai(evidence_json: str, model: str) -> str:
    client = OpenAI()

    prompt = f"""
你是资深 AWS RDS PostgreSQL SRE。请基于下面证据生成中文 RCA 报告。

分析要求：
1. 不要编造，只根据证据判断。
2. 明确区分：
   - 已确认事实
   - 高置信根因
   - 可能根因
   - 证据不足
3. 同时关联 AWS/RDS 层和 PostgreSQL 内部层：
   - RDS Events: reboot, restart, failover, maintenance, storage, backup
   - CloudWatch: CPU, connections, memory, swap, storage, read/write latency, DiskQueueDepth, burst/credit
   - Performance Insights: Top SQL, Top waits, users, hosts, applications
   - PostgreSQL: long SQL, long transaction, idle in transaction, blocking locks, wait_event, pg_stat_statements, deadlocks, temp files, autovacuum, checkpoint, replication slot, active DDL
4. 给出按优先级排列的处理建议：
   - 立即止血
   - 1-3 天修复
   - 长期治理
5. 对危险操作说明风险，例如：
   - pg_terminate_backend
   - 改参数并重启
   - VACUUM FULL
   - REINDEX
   - 扩容/变更存储
6. 输出 Markdown，结构必须包含：
   - 结论摘要
   - 事故时间线
   - 证据链
   - 根因判断
   - 解决方案
   - 需要补充的信息
   - 推荐告警
   - 推荐配置/SQL

证据 JSON：
```json
{evidence_json}
 ```   
"""

    response = client.responses.create(
        model=model,
        input=prompt,
    )

    return response.output_text

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aws-input", default="")
    parser.add_argument("--pg-input", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--dump-evidence", action="store_true")
    args = parser.parse_args()

    aws_dir = Path(args.aws_input) if args.aws_input else None
    pg_dir = Path(args.pg_input) if args.pg_input else None

    if aws_dir and not aws_dir.exists():
        raise SystemExit(f"AWS input dir not found: {aws_dir}")
    if pg_dir and not pg_dir.exists():
        raise SystemExit(f"PostgreSQL input dir not found: {pg_dir}")
    if not aws_dir and not pg_dir:
        raise SystemExit("At least one of --aws-input or --pg-input is required")

    evidence = build_evidence(aws_dir, pg_dir)
    evidence_json = compact_evidence(evidence)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.dump_evidence:
        evidence_path = output_path.parent / "ai_evidence_pack.json"
        evidence_path.write_text(evidence_json, encoding="utf-8")
        print(f"[INFO] evidence written to: {evidence_path}")

    print("[INFO] calling OpenAI API")
    report = analyze_with_ai(evidence_json, args.model)
    output_path.write_text(report, encoding="utf-8")

    print(f"[DONE] RCA report written to: {output_path}")

if __name__ == "__main__":
    main()
