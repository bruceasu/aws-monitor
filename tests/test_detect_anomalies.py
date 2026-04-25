from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker" / "scripts"))

from detect_anomalies import detect_anomalies, score


def test_detect_anomalies_captures_rds_and_pg_signals():
    evidence = {
        "connection_summary.json": [{"sessions": 240}],
        "long_transactions.json": [{"pid": i} for i in range(6)],
        "idle_in_transaction.json": [{"pid": 99}],
        "blocking_locks.json": [{"blocked_pid": 1, "blocking_pid": 2}],
        "db_stats.json": [{"deadlocks": 3}],
        "cloudwatch_alarms.json": {
            "MetricAlarms": [{"AlarmName": "HighCPU", "StateValue": "ALARM"}]
        },
        "log_findings": {
            "postgresql.log": "FATAL: remaining connection slots are reserved\nERROR: out of memory\nPANIC: no space left on device"
        },
        "errors.json": {"pg_stat_statements_top_io": "permission denied"},
        "metric_summaries.json": {
            "CPUUtilization": {"Maximum": {"max": 95}},
            "FreeableMemory": {"Minimum": {"min": 100 * 1024 * 1024}},
            "FreeStorageSpace": {"Minimum": {"min": 5 * 1024 * 1024 * 1024}},
            "WriteLatency": {"Maximum": {"max": 0.35}},
            "DiskQueueDepth": {"Maximum": {"max": 80}},
            "ReplicaLag": {"Maximum": {"max": 600}},
        },
        "events.json": {
            "Events": [
                {"Message": "DB instance restarted during failover"},
                {"Message": "Maintenance patch is required"},
            ]
        },
        "pending_maintenance.json": {
            "PendingMaintenanceActions": [{"Action": "system-update"}]
        },
    }

    findings = detect_anomalies(evidence)
    finding_types = {finding["type"] for finding in findings}

    assert "CONNECTION" in finding_types
    assert "TRANSACTION" in finding_types
    assert "LOCK" in finding_types
    assert "CPU" in finding_types
    assert "MEMORY" in finding_types
    assert "STORAGE" in finding_types
    assert "IO" in finding_types
    assert "AVAILABILITY_EVENT" in finding_types
    assert "MAINTENANCE" in finding_types
    assert "ALARM" in finding_types
    assert "OOM" in finding_types
    assert "CONNECTION_EXHAUSTION" in finding_types
    assert "COLLECTION_GAP" in finding_types
    assert score(findings) == "HIGH"


def test_detect_anomalies_returns_empty_for_clean_evidence():
    findings = detect_anomalies(
        {
            "connection_summary.json": [{"sessions": 10}],
            "long_transactions.json": [],
            "blocking_locks.json": [],
            "metric_summaries.json": {},
            "events.json": {"Events": []},
            "pending_maintenance.json": {"PendingMaintenanceActions": []},
        }
    )

    assert findings == []
    assert score(findings) == "LOW"
