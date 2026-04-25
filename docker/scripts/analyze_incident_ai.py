#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

from redact import redact_text
from detect_anomalies import score


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
    "incident_status.json",
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
    except Exception as exc:
        return {"error": f"failed to read {path.name}: {exc}"}


def load_text(path: Path, max_chars: int = 20_000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[TRUNCATED]..."
    return text


def collect_files(input_dir: Path, names: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in names:
        data = load_json(input_dir / name)
        if data is not None:
            result[name] = data
    return result


def collect_aws_logs(aws_dir: Optional[Path]) -> dict[str, str]:
    if not aws_dir:
        return {}

    log_dir = aws_dir / "logs"
    if not log_dir.exists():
        return {}

    result: dict[str, str] = {}
    keywords = [
        "fatal",
        "panic",
        "error",
        "restart",
        "shutdown",
        "crash",
        "out of memory",
        "too many connections",
        "remaining connection slots",
        "deadlock",
        "terminating connection",
        "no space left",
        "recovery",
        "failover",
    ]

    for path in sorted(log_dir.glob("*.txt")):
        text = load_text(path, 40_000)
        lines: list[str] = []
        for line in text.splitlines():
            lower = line.lower()
            if any(keyword in lower for keyword in keywords):
                lines.append(line[:1200])
            if len(lines) >= 80:
                break
        if lines:
            result[path.name] = "\n".join(lines)

    return result


def build_evidence(aws_dir: Optional[Path], pg_dir: Optional[Path]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    if aws_dir:
        evidence["aws_rds"] = collect_files(aws_dir, AWS_FILES)
        evidence["aws_rds"]["log_findings"] = collect_aws_logs(aws_dir)
    if pg_dir:
        evidence["postgresql"] = collect_files(pg_dir, PG_FILES)
        incident_status = load_json(pg_dir.parent / "incident_status.json")
        if incident_status is not None:
            evidence["incident_status"] = incident_status
    return evidence


def compact_evidence(evidence: dict[str, Any]) -> str:
    text = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    if len(text) <= MAX_TOTAL_CHARS:
        return text

    def trim(value: Any) -> Any:
        if isinstance(value, list):
            return value[:50]
        if isinstance(value, dict):
            return {key: trim(item) for key, item in value.items()}
        return value

    compacted = trim(evidence)
    text = json.dumps(compacted, ensure_ascii=False, indent=2, default=str)
    if len(text) <= MAX_TOTAL_CHARS:
        return text
    return text[:MAX_TOTAL_CHARS] + "\n...[TRUNCATED]..."


def analyze_with_ai(evidence_json: str, findings: list[dict[str, Any]], confidence: str, model: str) -> str:
    client = OpenAI()
    anomaly_text = "\n".join(
        f"- [{finding['level']}] {finding['type']}: {finding['message']}" for finding in findings
    ) or "- 未检测到明确异常，需要人工结合原始证据继续分析"

    prompt = f"""
你是资深 AWS RDS PostgreSQL SRE。

已检测异常：
{anomaly_text}

置信度参考：{confidence}

请基于下面证据输出 RCA 报告。

要求：
1. 只根据证据推断，不要编造。
2. 明确区分：
   - 已确认事实
   - 高置信根因
   - 可能根因
3. 同时分析：
   - AWS/RDS 层
   - PostgreSQL 内部
4. 必须给出解决方案，并说明风险与适用条件。
5. 如果证据不足，要明确写出缺什么。

输出结构（Markdown）：
- 结论摘要
- 时间线
- 证据链
- 根因判断
- 解决方案（立即/短期/长期）
- 风险说明

证据：
```json
{evidence_json}
```
"""

    response = client.responses.create(model=model, input=prompt)
    return response.output_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aws-input", default="")
    parser.add_argument("--pg-input", default="")
    parser.add_argument("--findings", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--dump-evidence", action="store_true")
    args = parser.parse_args()

    aws_dir = Path(args.aws_input) if args.aws_input else None
    pg_dir = Path(args.pg_input) if args.pg_input else None
    findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    confidence = score(findings)
    evidence = build_evidence(aws_dir, pg_dir)

    evidence_json = compact_evidence(evidence)
    evidence_json = redact_text(evidence_json)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.dump_evidence:
        (output_path.parent / "ai_evidence_pack.json").write_text(
            evidence_json,
            encoding="utf-8",
        )

    print("[INFO] calling OpenAI API")
    report = analyze_with_ai(evidence_json, findings, confidence, args.model)
    output_path.write_text(report, encoding="utf-8")
    print(f"[DONE] RCA report written to: {output_path}")


if __name__ == "__main__":
    main()
