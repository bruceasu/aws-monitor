from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker" / "scripts"))

from run_incident import load_evidence, validate_env


def test_validate_env_reports_missing_keys(monkeypatch):
    for key in ["DB_INSTANCE", "AWS_REGION", "START_TIME", "END_TIME", "MODEL"]:
        monkeypatch.delenv(key, raising=False)

    missing = validate_env()

    assert missing == ["DB_INSTANCE", "AWS_REGION", "START_TIME", "END_TIME", "MODEL"]


def test_load_evidence_includes_logs_and_partial_files(tmp_path):
    aws_dir = tmp_path / "aws_rds"
    pg_dir = tmp_path / "postgresql"
    logs_dir = aws_dir / "logs"
    logs_dir.mkdir(parents=True)
    pg_dir.mkdir(parents=True)

    (aws_dir / "metric_summaries.json").write_text("{}", encoding="utf-8")
    (aws_dir / "events.json").write_text('{"Events": []}', encoding="utf-8")
    (aws_dir / "pending_maintenance.json").write_text(
        '{"PendingMaintenanceActions": []}',
        encoding="utf-8",
    )
    (aws_dir / "cloudwatch_alarms.json").write_text('{"MetricAlarms": []}', encoding="utf-8")
    (logs_dir / "postgresql.log.txt").write_text(
        "ERROR: out of memory\nsome other text",
        encoding="utf-8",
    )

    (pg_dir / "long_transactions.json").write_text("[]", encoding="utf-8")
    (pg_dir / "idle_in_transaction.json").write_text("[]", encoding="utf-8")
    (pg_dir / "blocking_locks.json").write_text("[]", encoding="utf-8")
    (pg_dir / "connection_summary.json").write_text("[]", encoding="utf-8")
    (pg_dir / "db_stats.json").write_text("[]", encoding="utf-8")
    (pg_dir / "errors.json").write_text('{"sample": "boom"}', encoding="utf-8")

    evidence = load_evidence(aws_dir, pg_dir)

    assert "cloudwatch_alarms.json" in evidence
    assert "errors.json" in evidence
    assert "log_findings" in evidence
    assert "postgresql.log.txt" in evidence["log_findings"]
