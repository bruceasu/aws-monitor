from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker" / "scripts"))

from runbook import build_runbook


def test_build_runbook_uses_finding_types_instead_of_message_text():
    findings = [
        {"level": "CRITICAL", "type": "LOCK", "message": "存在锁等待/阻塞链: 3"},
        {"level": "CRITICAL", "type": "CONNECTION", "message": "数据库连接数过高: 250"},
        {"level": "WARNING", "type": "AVAILABILITY_EVENT", "message": "发现重启事件"},
        {"level": "WARNING", "type": "IO", "message": "写延迟偏高"},
    ]

    steps = build_runbook(findings)
    step_ids = {step.id for step in steps}

    assert "pg-lock-chain-check" in step_ids
    assert "pg-connection-distribution" in step_ids
    assert "rds-check-events" in step_ids
    assert "pg-top-load-sql" in step_ids


def test_build_runbook_has_fallback_when_no_findings():
    steps = build_runbook([])

    assert len(steps) == 1
    assert steps[0].id == "collect-more-evidence"
