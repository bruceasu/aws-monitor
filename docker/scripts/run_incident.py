#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

from detect_anomalies import detect_anomalies, score
from notify import send_teams
from runbook import build_runbook, runbook_to_markdown

BASE_DIR = Path("/work")
OUTPUT_DIR = BASE_DIR / "output"


def run(cmd: list[str]) -> None:
    print(f"[RUN] {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def run_step(name: str, cmd: list[str], allow_failure: bool = False) -> dict[str, object]:
    print(f"[RUN:{name}] {' '.join(cmd)}")
    result = subprocess.run(cmd)
    status = {
        "name": name,
        "command": cmd,
        "returncode": result.returncode,
        "success": result.returncode == 0,
    }
    if result.returncode != 0:
        status["error"] = f"Command failed: {' '.join(cmd)}"
        if not allow_failure:
            raise RuntimeError(status["error"])
    return status


def create_incident_dir() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    incident_dir = OUTPUT_DIR / f"incident_{ts}"
    incident_dir.mkdir(parents=True, exist_ok=True)
    return incident_dir


def write_meta(incident_dir: Path) -> None:
    meta = {
        "timestamp": datetime.now().isoformat(),
        "db_instance": os.getenv("DB_INSTANCE"),
        "region": os.getenv("AWS_REGION"),
        "start_time": os.getenv("START_TIME"),
        "end_time": os.getenv("END_TIME"),
        "model": os.getenv("MODEL"),
    }
    (incident_dir / "incident_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def zip_incident(incident_dir: Path) -> None:
    zip_path = incident_dir / "incident_package.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in incident_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(incident_dir))
    print(f"[DONE] zipped: {zip_path}")


def load_evidence(aws_dir: Path, pg_dir: Path) -> dict[str, object]:
    evidence: dict[str, object] = {}

    def load_json_safe(path: Path):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    evidence["metric_summaries.json"] = load_json_safe(aws_dir / "metric_summaries.json") or {}
    evidence["events.json"] = load_json_safe(aws_dir / "events.json") or {}
    evidence["pending_maintenance.json"] = (
        load_json_safe(aws_dir / "pending_maintenance.json") or {}
    )
    evidence["cloudwatch_alarms.json"] = load_json_safe(aws_dir / "cloudwatch_alarms.json") or {}

    evidence["long_transactions.json"] = load_json_safe(pg_dir / "long_transactions.json") or []
    evidence["idle_in_transaction.json"] = (
        load_json_safe(pg_dir / "idle_in_transaction.json") or []
    )
    evidence["blocking_locks.json"] = load_json_safe(pg_dir / "blocking_locks.json") or []
    evidence["connection_summary.json"] = load_json_safe(pg_dir / "connection_summary.json") or []
    evidence["db_stats.json"] = load_json_safe(pg_dir / "db_stats.json") or []
    evidence["errors.json"] = load_json_safe(pg_dir / "errors.json") or {}

    log_findings: dict[str, str] = {}
    logs_dir = aws_dir / "logs"
    if logs_dir.exists():
        for path in sorted(logs_dir.glob("*.txt")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            lower = text.lower()
            if any(
                token in lower
                for token in [
                    "fatal",
                    "panic",
                    "error",
                    "out of memory",
                    "too many connections",
                    "remaining connection slots",
                    "no space left",
                    "failover",
                    "recovery",
                ]
            ):
                log_findings[path.name] = text[:6000]
    evidence["log_findings"] = log_findings

    return evidence


def validate_env() -> list[str]:
    missing = []
    for key in ["DB_INSTANCE", "AWS_REGION", "START_TIME", "END_TIME", "MODEL"]:
        if not os.getenv(key):
            missing.append(key)
    return missing


def main() -> None:
    missing_env = validate_env()
    if missing_env:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing_env)}")

    incident_dir = create_incident_dir()
    aws_dir = incident_dir / "aws_rds"
    pg_dir = incident_dir / "postgresql"
    write_meta(incident_dir)
    step_results: list[dict[str, object]] = []

    step_results.append(
        run_step(
            "collect_aws_rds_diag",
            [
                "python",
                "scripts/collect_aws_rds_diag.py",
                "--db-instance",
                os.environ["DB_INSTANCE"],
                "--region",
                os.environ["AWS_REGION"],
                "--start",
                os.environ["START_TIME"],
                "--end",
                os.environ["END_TIME"],
                "--period",
                "60",
                "--out",
                str(aws_dir),
            ],
            allow_failure=True,
        )
    )

    step_results.append(
        run_step(
            "collect_pg_diag",
            [
                "python",
                "scripts/collect_pg_diag.py",
                "--out",
                str(pg_dir),
                "--long-query-seconds",
                "30",
                "--long-xact-seconds",
                "120",
            ],
            allow_failure=True,
        )
    )

    evidence = load_evidence(aws_dir, pg_dir)
    evidence["incident_steps.json"] = step_results
    failed_steps = [step for step in step_results if not step["success"]]
    if failed_steps:
        evidence["errors.json"] = {
            **(evidence.get("errors.json") or {}),
            "incident_steps": failed_steps,
        }

    findings = detect_anomalies(evidence)
    confidence = score(findings)
    anomaly_text = "\n".join(
        f"- [{finding['level']}] {finding['type']}: {finding['message']}" for finding in findings
    ) or "- 未检测到明确异常"

    steps = build_runbook(findings)
    runbook_md = runbook_to_markdown(steps)

    (incident_dir / "findings.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (incident_dir / "runbook.md").write_text(runbook_md, encoding="utf-8")
    (incident_dir / "incident_status.json").write_text(
        json.dumps(
            {
                "steps": step_results,
                "failed_steps": failed_steps,
                "confidence": confidence,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    analyze_step = run_step(
        "analyze_incident_ai",
        [
            "python",
            "scripts/analyze_incident_ai.py",
            "--aws-input",
            str(aws_dir),
            "--pg-input",
            str(pg_dir),
            "--findings",
            str(incident_dir / "findings.json"),
            "--output",
            str(incident_dir / "final_rca_report.md"),
            "--model",
            os.environ["MODEL"],
            "--dump-evidence",
        ],
        allow_failure=True,
    )
    step_results.append(analyze_step)
    if not analyze_step["success"]:
        fallback_report = f"""# RDS PostgreSQL Incident RCA

## 自动异常检测结果

{anomaly_text}

## RCA 置信度

**{confidence}**

## 步骤执行状态

```json
{json.dumps(step_results, ensure_ascii=False, indent=2)}
```

## 推荐处理方案

{runbook_md}
"""
        (incident_dir / "final_rca_report.md").write_text(fallback_report, encoding="utf-8")

    zip_incident(incident_dir)
    print(f"\n[DONE] Incident folder: {incident_dir}")

    report = f"""
# RDS PostgreSQL Incident RCA

## 自动异常检测结果

{anomaly_text}

## RCA 置信度

**{confidence}**

## 推荐处理方案

{runbook_md}

---

完整报告见：
{incident_dir / "final_rca_report.md"}
"""

    webhook = os.getenv("TEAMS_WEBHOOK")
    if webhook:
        send_teams(webhook, f"RDS RCA ({confidence})", report)


if __name__ == "__main__":
    main()
