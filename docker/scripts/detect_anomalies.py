from __future__ import annotations

from typing import Any


Finding = dict[str, Any]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _event_matches(events: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    lowered_keywords = [keyword.lower() for keyword in keywords]
    for event in events:
        message = str(event.get("Message", "")).lower()
        if any(keyword in message for keyword in lowered_keywords):
            matched.append(event)
    return matched


def detect_anomalies(evidence: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []

    def add(
        level: str,
        type_: str,
        message: str,
        value: Any = None,
        threshold: Any = None,
        evidence_data: Any = None,
    ) -> None:
        findings.append(
            {
                "level": level,
                "type": type_,
                "message": message,
                "value": value,
                "threshold": threshold,
                "evidence": evidence_data,
            }
        )

    conns = evidence.get("connection_summary.json") or []
    total_connections = sum(int(item.get("sessions", 0)) for item in conns)
    if total_connections > 200:
        add(
            "CRITICAL",
            "CONNECTION",
            f"数据库连接数过高: {total_connections}",
            value=total_connections,
            threshold=200,
            evidence_data=conns[:5],
        )

    long_tx = evidence.get("long_transactions.json") or []
    if len(long_tx) > 5:
        add(
            "CRITICAL",
            "TRANSACTION",
            f"长事务数量过多: {len(long_tx)}",
            value=len(long_tx),
            threshold=5,
            evidence_data=long_tx[:5],
        )

    idle_tx = evidence.get("idle_in_transaction.json") or []
    if len(idle_tx) > 0:
        add(
            "WARNING",
            "IDLE_IN_TRANSACTION",
            f"存在 idle in transaction 会话: {len(idle_tx)}",
            value=len(idle_tx),
            threshold=0,
            evidence_data=idle_tx[:5],
        )

    locks = evidence.get("blocking_locks.json") or []
    if len(locks) > 0:
        add(
            "CRITICAL",
            "LOCK",
            f"存在锁等待/阻塞链: {len(locks)}",
            value=len(locks),
            threshold=0,
            evidence_data=locks[:5],
        )

    db_stats_rows = evidence.get("db_stats.json") or []
    if db_stats_rows:
        deadlocks = int(db_stats_rows[0].get("deadlocks") or 0)
        if deadlocks > 0:
            add(
                "WARNING",
                "DEADLOCK",
                f"数据库统计中存在死锁: {deadlocks}",
                value=deadlocks,
                threshold=0,
                evidence_data=db_stats_rows[0],
            )

    metric_summaries = evidence.get("metric_summaries.json") or {}

    cpu = metric_summaries.get("CPUUtilization") or {}
    max_cpu = _safe_float((cpu.get("Maximum") or {}).get("max"))
    if max_cpu > 90:
        add(
            "WARNING",
            "CPU",
            f"CPU 使用率过高: {max_cpu:.2f}%",
            value=max_cpu,
            threshold=90,
            evidence_data=cpu,
        )

    free_mem = metric_summaries.get("FreeableMemory") or {}
    min_mem = _safe_float((free_mem.get("Minimum") or {}).get("min"))
    if 0 < min_mem < 256 * 1024 * 1024:
        add(
            "CRITICAL",
            "MEMORY",
            f"可用内存过低: {min_mem:.0f} bytes",
            value=min_mem,
            threshold=256 * 1024 * 1024,
            evidence_data=free_mem,
        )

    free_storage = metric_summaries.get("FreeStorageSpace") or {}
    min_storage = _safe_float((free_storage.get("Minimum") or {}).get("min"))
    if 0 < min_storage < 10 * 1024 * 1024 * 1024:
        add(
            "CRITICAL",
            "STORAGE",
            f"可用存储空间过低: {min_storage:.0f} bytes",
            value=min_storage,
            threshold=10 * 1024 * 1024 * 1024,
            evidence_data=free_storage,
        )

    write_latency = metric_summaries.get("WriteLatency") or {}
    max_write_latency = _safe_float((write_latency.get("Maximum") or {}).get("max"))
    if max_write_latency > 0.2:
        add(
            "WARNING",
            "IO",
            f"写延迟偏高: {max_write_latency:.4f}s",
            value=max_write_latency,
            threshold=0.2,
            evidence_data=write_latency,
        )

    disk_queue = metric_summaries.get("DiskQueueDepth") or {}
    max_disk_queue = _safe_float((disk_queue.get("Maximum") or {}).get("max"))
    if max_disk_queue > 64:
        add(
            "WARNING",
            "IO",
            f"磁盘队列深度偏高: {max_disk_queue:.2f}",
            value=max_disk_queue,
            threshold=64,
            evidence_data=disk_queue,
        )

    replica_lag = metric_summaries.get("ReplicaLag") or {}
    max_replica_lag = _safe_float((replica_lag.get("Maximum") or {}).get("max"))
    if max_replica_lag > 300:
        add(
            "WARNING",
            "REPLICATION",
            f"复制延迟偏高: {max_replica_lag:.2f}s",
            value=max_replica_lag,
            threshold=300,
            evidence_data=replica_lag,
        )

    aws_events = (evidence.get("events.json") or {}).get("Events") or []
    availability_events = _event_matches(
        aws_events,
        ["failover", "reboot", "restart", "crash", "shutdown", "recovery"],
    )
    if availability_events:
        add(
            "CRITICAL",
            "AVAILABILITY_EVENT",
            f"RDS 事件中发现可用性异常相关记录: {len(availability_events)}",
            value=len(availability_events),
            threshold=0,
            evidence_data=availability_events[:5],
        )

    maintenance_events = _event_matches(
        aws_events,
        ["maintenance", "patch", "upgrade", "backup"],
    )
    if maintenance_events:
        add(
            "WARNING",
            "MAINTENANCE",
            f"RDS 事件中发现维护/升级相关记录: {len(maintenance_events)}",
            value=len(maintenance_events),
            threshold=0,
            evidence_data=maintenance_events[:5],
        )

    pending_maintenance = (evidence.get("pending_maintenance.json") or {}).get(
        "PendingMaintenanceActions"
    ) or []
    if pending_maintenance:
        add(
            "WARNING",
            "MAINTENANCE",
            f"实例存在待执行维护项: {len(pending_maintenance)}",
            value=len(pending_maintenance),
            threshold=0,
            evidence_data=pending_maintenance[:5],
        )

    alarms = (evidence.get("cloudwatch_alarms.json") or {}).get("MetricAlarms") or []
    alarm_names = [
        alarm.get("AlarmName")
        for alarm in alarms
        if str(alarm.get("StateValue", "")).upper() == "ALARM"
    ]
    if alarm_names:
        add(
            "WARNING",
            "ALARM",
            f"CloudWatch 存在告警中的指标报警: {len(alarm_names)}",
            value=len(alarm_names),
            threshold=0,
            evidence_data=alarm_names[:10],
        )

    log_findings = evidence.get("log_findings") or {}
    if log_findings:
        combined_log_text = "\n".join(str(value) for value in log_findings.values()).lower()
        if any(token in combined_log_text for token in ["out of memory", "oom", "cannot allocate memory"]):
            add(
                "CRITICAL",
                "OOM",
                "RDS 日志中出现疑似内存不足/OOM 迹象",
                value="oom",
                threshold=None,
                evidence_data=log_findings,
            )
        if any(
            token in combined_log_text
            for token in ["remaining connection slots", "too many connections"]
        ):
            add(
                "CRITICAL",
                "CONNECTION_EXHAUSTION",
                "RDS 日志中出现连接耗尽迹象",
                value="connection_exhaustion",
                threshold=None,
                evidence_data=log_findings,
            )
        if any(token in combined_log_text for token in ["no space left", "disk full", "storage full"]):
            add(
                "CRITICAL",
                "STORAGE",
                "RDS 日志中出现存储空间耗尽迹象",
                value="storage_exhaustion",
                threshold=None,
                evidence_data=log_findings,
            )

    collect_errors = evidence.get("errors.json") or {}
    if collect_errors:
        add(
            "WARNING",
            "COLLECTION_GAP",
            f"PostgreSQL 采集存在失败项: {len(collect_errors)}",
            value=len(collect_errors),
            threshold=0,
            evidence_data=collect_errors,
        )

    return findings


def score(findings: list[Finding]) -> str:
    total_score = 0.0
    weights = {
        "LOCK": 50,
        "TRANSACTION": 40,
        "CONNECTION": 30,
        "CPU": 20,
        "MEMORY": 35,
        "STORAGE": 45,
        "IO": 25,
        "AVAILABILITY_EVENT": 50,
        "MAINTENANCE": 15,
        "REPLICATION": 20,
        "DEADLOCK": 20,
        "IDLE_IN_TRANSACTION": 15,
        "ALARM": 15,
        "OOM": 50,
        "CONNECTION_EXHAUSTION": 45,
        "COLLECTION_GAP": 10,
    }

    for finding in findings:
        base = weights.get(finding.get("type"), 10)
        if finding.get("level") == "CRITICAL":
            total_score += base
        elif finding.get("level") == "WARNING":
            total_score += base * 0.5

    if total_score >= 80:
        return "HIGH"
    if total_score >= 40:
        return "MEDIUM"
    return "LOW"
