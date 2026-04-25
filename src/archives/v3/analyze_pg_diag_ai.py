#!/usr/bin/env python3
import argparse
from email.mime import text
import json
from pathlib import Path
from typing import Any, Dict
from openai import OpenAI

MAX_TOTAL_CHARS = 180_000
IMPORTANT_FILES = [
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
    
def build_evidence(input_dir: Path) -> Dict[str, Any]:
    evidence = {}
    for name in IMPORTANT_FILES:
        data = load_json(input_dir / name)
        if data is not None:
            evidence[name] = data
    return evidence

def compact_json(data: Dict[str, Any]) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) <= MAX_TOTAL_CHARS:
        return text
    # 简单压缩：大数组只保留前 30 条。
    compacted = {}
    for key, value in data.items():
        if isinstance(value, list):
            compacted[key] = value[:30]
        else:
            compacted[key] = value
    text = json.dumps(compacted, ensure_ascii=False, indent=2)
    if len(text) <= MAX_TOTAL_CHARS:
        return text
    return text[:MAX_TOTAL_CHARS] + "\n...[TRUNCATED]..."

def analyze_with_ai(evidence_json: str, model: str) -> str:
    client = OpenAI()
    prompt = f"""
你是资深 PostgreSQL / AWS RDS SRE。请基于下面 PostgreSQL 诊断数据生成中文 RCA 报告。
要求：
1. 不要编造，只根据证据判断。
2. 明确区分：
   - 已确认事实
   - 高置信根因
   - 可能根因
   - 证据不足
3. 重点分析：
   - 长 SQL
   - 长事务
   - idle in transaction
   - 锁等待和 blocking session
   - 连接数异常
   - wait_event / wait_event_type
   - pg_stat_statements Top SQL
   - IO heavy SQL
   - deadlock
   - temp files / work_mem 压力
   - autovacuum / dead tuples
   - checkpoint 压力
   - replication slot / WAL 堆积
   - DDL / VACUUM / CREATE INDEX 等维护操作
4. 给出：
   - 结论摘要
   - 证据链
   - 立即止血方案
   - 1-3 天内修复方案
   - 长期治理建议
   - 需要补充采集的信息
5. 如果建议 pg_cancel_backend 或 pg_terminate_backend，必须说明风险。
6. 输出 Markdown。
诊断数据：
```json
{evidence_json}
"""
    response = client.responses.create(
        model=model,
        input=prompt,
    )
    return response.output_text

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Directory from collect_pg_diag.py")
    parser.add_argument("--output", default="", help="Output markdown path")
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--dump-evidence", action="store_true")
    args = parser.parse_args()
    input_dir = Path(args.input)
    if not input_dir.exists():
        raise SystemExit(f"input directory not found: {input_dir}")
    evidence = build_evidence(input_dir)
    evidence_json = compact_json(evidence)
    if args.dump_evidence:
        evidence_path = input_dir / "ai_evidence_pack.json"
        evidence_path.write_text(evidence_json, encoding="utf-8")
        print(f"[INFO] wrote evidence: {evidence_path}")
    output_path = Path(args.output) if args.output else input_dir / "ai_pg_rca_report.md"
    print("[INFO] calling OpenAI API...")
    report = analyze_with_ai(evidence_json, args.model)
    output_path.write_text(report, encoding="utf-8")
    print(f"[DONE] report written: {output_path}")

if __name__ == "__main__":
    main()
