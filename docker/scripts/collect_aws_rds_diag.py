#!/usr/bin/env python3
# pip install boto3
# python3 collect_aws_rds_diag.py \
#  --db-instance prod-rds-01 \
#  --region ap-northeast-1 \
#  --start "2026-04-25T10:00:00+09:00" \
#  --end "2026-04-25T12:30:00+09:00" \
#  --out incident_20260425_1100/aws_rds


import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


RDS_METRICS = [
    ("CPUUtilization", ["Average", "Maximum"]),
    ("DatabaseConnections", ["Average", "Maximum"]),
    ("FreeableMemory", ["Average", "Minimum"]),
    ("FreeStorageSpace", ["Average", "Minimum"]),
    ("SwapUsage", ["Average", "Maximum"]),
    ("ReadLatency", ["Average", "Maximum"]),
    ("WriteLatency", ["Average", "Maximum"]),
    ("DiskQueueDepth", ["Average", "Maximum"]),
    ("ReadIOPS", ["Average", "Maximum"]),
    ("WriteIOPS", ["Average", "Maximum"]),
    ("ReadThroughput", ["Average", "Maximum"]),
    ("WriteThroughput", ["Average", "Maximum"]),
    ("NetworkReceiveThroughput", ["Average", "Maximum"]),
    ("NetworkTransmitThroughput", ["Average", "Maximum"]),
    ("BurstBalance", ["Average", "Minimum"]),
    ("CPUCreditBalance", ["Average", "Minimum"]),
    ("EBSIOBalance%", ["Average", "Minimum"]),
    ("EBSByteBalance%", ["Average", "Minimum"]),
    ("ReplicaLag", ["Average", "Maximum"]),
    ("Deadlocks", ["Sum", "Maximum"]),
]


LOG_NAME_KEYWORDS = [
    "error",
    "postgresql",
    "slowquery",
    "alert",
    "trace",
    "general",
]


def parse_time(value: str) -> datetime:
    value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Time must include timezone, e.g. Z or +09:00")
    return dt.astimezone(timezone.utc)


def json_default(obj: Any) -> str:
    return str(obj)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="ignore")


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def get_db_instance(rds, db_instance_id: str) -> Dict[str, Any]:
    resp = rds.describe_db_instances(DBInstanceIdentifier=db_instance_id)
    instances = resp.get("DBInstances", [])
    if not instances:
        raise RuntimeError(f"DB instance not found: {db_instance_id}")
    return instances[0]


def collect_rds_events(rds, db_instance_id: str, start: datetime, end: datetime) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    marker: Optional[str] = None

    while True:
        kwargs = {
            "SourceIdentifier": db_instance_id,
            "SourceType": "db-instance",
            "StartTime": start,
            "EndTime": end,
            "MaxRecords": 100,
        }
        if marker:
            kwargs["Marker"] = marker

        resp = rds.describe_events(**kwargs)
        events.extend(resp.get("Events", []))
        marker = resp.get("Marker")
        if not marker:
            break

    return {"Events": events}


def collect_pending_maintenance(rds, db_instance_id: str) -> Dict[str, Any]:
    try:
        return rds.describe_pending_maintenance_actions(
            Filters=[
                {
                    "Name": "db-instance-id",
                    "Values": [db_instance_id],
                }
            ]
        )
    except ClientError as e:
        return {"error": str(e)}


def collect_cloudwatch_metric(
    cloudwatch,
    db_instance_id: str,
    metric_name: str,
    statistics: List[str],
    start: datetime,
    end: datetime,
    period: int,
) -> Dict[str, Any]:
    try:
        return cloudwatch.get_metric_statistics(
            Namespace="AWS/RDS",
            MetricName=metric_name,
            Dimensions=[
                {
                    "Name": "DBInstanceIdentifier",
                    "Value": db_instance_id,
                }
            ],
            StartTime=start,
            EndTime=end,
            Period=period,
            Statistics=statistics,
        )
    except ClientError as e:
        return {
            "Label": metric_name,
            "Datapoints": [],
            "error": str(e),
        }


def collect_cloudwatch_alarms(cloudwatch, db_instance_id: str) -> Dict[str, Any]:
    alarms: List[Dict[str, Any]] = []
    token: Optional[str] = None

    while True:
        kwargs: Dict[str, Any] = {}
        if token:
            kwargs["NextToken"] = token

        resp = cloudwatch.describe_alarms(**kwargs)

        for alarm in resp.get("MetricAlarms", []):
            raw = json.dumps(alarm, default=json_default)
            if db_instance_id in raw:
                alarms.append(alarm)

        token = resp.get("NextToken")
        if not token:
            break

    return {"MetricAlarms": alarms}


def collect_log_file_list(rds, db_instance_id: str) -> Dict[str, Any]:
    logs: List[Dict[str, Any]] = []
    marker: Optional[str] = None

    while True:
        kwargs = {
            "DBInstanceIdentifier": db_instance_id,
            "MaxRecords": 100,
        }
        if marker:
            kwargs["Marker"] = marker

        resp = rds.describe_db_log_files(**kwargs)
        logs.extend(resp.get("DescribeDBLogFiles", []))
        marker = resp.get("Marker")
        if not marker:
            break

    return {"DescribeDBLogFiles": logs}


def select_relevant_logs(
    log_files: Dict[str, Any],
    start: datetime,
    end: datetime,
    max_files: int,
) -> List[str]:
    selected: List[str] = []

    for item in log_files.get("DescribeDBLogFiles", []):
        name = item.get("LogFileName", "")
        lname = name.lower()

        if not any(k in lname for k in LOG_NAME_KEYWORDS):
            continue

        last_written_ms = item.get("LastWritten")
        if last_written_ms:
            last_written = datetime.fromtimestamp(last_written_ms / 1000, tz=timezone.utc)
            # Include logs written within incident window +/- 1 hour.
            if not (start.timestamp() - 3600 <= last_written.timestamp() <= end.timestamp() + 3600):
                continue

        selected.append(name)

    return selected[:max_files]


def download_log_portion(rds, db_instance_id: str, log_file_name: str) -> str:
    chunks: List[str] = []
    marker = "0"
    loops = 0

    while True:
        loops += 1
        if loops > 20:
            chunks.append("\n...[TRUNCATED_AFTER_20_CHUNKS]...\n")
            break

        try:
            resp = rds.download_db_log_file_portion(
                DBInstanceIdentifier=db_instance_id,
                LogFileName=log_file_name,
                Marker=marker,
                NumberOfLines=1000,
            )
        except ClientError as e:
            return f"[ERROR] failed to download log {log_file_name}: {e}"

        chunks.append(resp.get("LogFileData", ""))

        if not resp.get("AdditionalDataPending"):
            break

        next_marker = resp.get("Marker")
        if not next_marker or next_marker == marker:
            break
        marker = next_marker

    return "".join(chunks)


def collect_performance_insights(
    pi,
    dbi_resource_id: str,
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    dimensions = {
        "top_sql": "db.sql",
        "top_waits": "db.wait_event",
        "top_users": "db.user",
        "top_hosts": "db.host",
        "top_applications": "db.application",
    }

    for key, group in dimensions.items():
        try:
            resp = pi.describe_dimension_keys(
                ServiceType="RDS",
                Identifier=dbi_resource_id,
                StartTime=start,
                EndTime=end,
                Metric="db.load.avg",
                GroupBy={
                    "Group": group,
                    "Limit": 15,
                },
            )
            result[key] = resp
        except ClientError as e:
            result[key] = {"error": str(e)}

    return result


def summarize_metric_file(data: Dict[str, Any]) -> Dict[str, Any]:
    points = data.get("Datapoints", [])
    summary: Dict[str, Any] = {
        "label": data.get("Label"),
        "datapoints": len(points),
    }

    for stat in ["Average", "Maximum", "Minimum", "Sum"]:
        vals = [float(p[stat]) for p in points if stat in p and p[stat] is not None]
        if vals:
            summary[stat] = {
                "min": min(vals),
                "avg": sum(vals) / len(vals),
                "max": max(vals),
            }

    return summary


def build_basic_report(out_dir: Path) -> None:
    db = json.loads((out_dir / "db_instance.json").read_text())
    events = json.loads((out_dir / "events.json").read_text())
    metric_summaries = json.loads((out_dir / "metric_summaries.json").read_text())

    lines: List[str] = []
    lines.append("# AWS/RDS Diagnostic Summary\n")

    lines.append("## Instance\n")
    keep = [
        "DBInstanceIdentifier",
        "DBInstanceClass",
        "Engine",
        "EngineVersion",
        "DBInstanceStatus",
        "MultiAZ",
        "StorageType",
        "AllocatedStorage",
        "MaxAllocatedStorage",
        "Iops",
        "StorageThroughput",
        "PerformanceInsightsEnabled",
        "PreferredBackupWindow",
        "PreferredMaintenanceWindow",
        "AutoMinorVersionUpgrade",
        "LatestRestorableTime",
    ]

    for key in keep:
        if key in db:
            lines.append(f"- {key}: `{db.get(key)}`")

    lines.append("\n## Event Keywords\n")

    keywords = [
        "failover",
        "reboot",
        "restart",
        "crash",
        "shutdown",
        "maintenance",
        "patch",
        "storage",
        "backup",
        "upgrade",
    ]

    matched = []
    for e in events.get("Events", []):
        msg = e.get("Message", "")
        if any(k in msg.lower() for k in keywords):
            matched.append(f"- `{e.get('Date')}` {msg}")

    if matched:
        lines.extend(matched[:50])
    else:
        lines.append("- No obvious reboot/failover/maintenance keywords found.")

    lines.append("\n## Metric Summaries\n")

    for metric_name, summary in metric_summaries.items():
        lines.append(f"### {metric_name}")
        lines.append(f"- datapoints: `{summary.get('datapoints')}`")
        for stat in ["Average", "Maximum", "Minimum", "Sum"]:
            if stat in summary:
                s = summary[stat]
                lines.append(
                    f"- {stat}: min=`{s['min']:.4f}`, avg=`{s['avg']:.4f}`, max=`{s['max']:.4f}`"
                )
        lines.append("")

    write_text(out_dir / "aws_rds_summary.md", "\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-instance", required=True, help="RDS DBInstanceIdentifier")
    parser.add_argument("--region", required=True)
    parser.add_argument("--start", required=True, help="ISO8601, e.g. 2026-04-25T10:00:00+09:00")
    parser.add_argument("--end", required=True, help="ISO8601, e.g. 2026-04-25T12:30:00+09:00")
    parser.add_argument("--period", type=int, default=60)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-log-files", type=int, default=8)
    args = parser.parse_args()

    start = parse_time(args.start)
    end = parse_time(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = boto3.Session(region_name=args.region)
    rds = session.client("rds")
    cloudwatch = session.client("cloudwatch")
    pi = session.client("pi")

    metadata = {
        "db_instance_identifier": args.db_instance,
        "region": args.region,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "period_seconds": args.period,
    }
    write_json(out_dir / "metadata.json", metadata)

    print("[INFO] collecting RDS instance metadata")
    db = get_db_instance(rds, args.db_instance)
    write_json(out_dir / "db_instance.json", db)

    print("[INFO] collecting RDS events")
    events = collect_rds_events(rds, args.db_instance, start, end)
    write_json(out_dir / "events.json", events)

    print("[INFO] collecting pending maintenance")
    pending = collect_pending_maintenance(rds, args.db_instance)
    write_json(out_dir / "pending_maintenance.json", pending)

    print("[INFO] collecting related CloudWatch alarms")
    alarms = collect_cloudwatch_alarms(cloudwatch, args.db_instance)
    write_json(out_dir / "cloudwatch_alarms.json", alarms)

    print("[INFO] collecting CloudWatch metrics")
    metric_summaries: Dict[str, Any] = {}
    for metric_name, stats in RDS_METRICS:
        print(f"[INFO] metric: {metric_name}")
        data = collect_cloudwatch_metric(
            cloudwatch=cloudwatch,
            db_instance_id=args.db_instance,
            metric_name=metric_name,
            statistics=stats,
            start=start,
            end=end,
            period=args.period,
        )
        write_json(out_dir / "metrics" / f"{safe_name(metric_name)}.json", data)
        metric_summaries[metric_name] = summarize_metric_file(data)

    write_json(out_dir / "metric_summaries.json", metric_summaries)

    print("[INFO] collecting RDS log file list")
    log_files = collect_log_file_list(rds, args.db_instance)
    write_json(out_dir / "log_files.json", log_files)

    selected_logs = select_relevant_logs(log_files, start, end, args.max_log_files)
    write_json(out_dir / "selected_logs.json", selected_logs)

    if selected_logs:
        print("[INFO] downloading selected RDS logs")
        for log_name in selected_logs:
            print(f"[INFO] log: {log_name}")
            data = download_log_portion(rds, args.db_instance, log_name)
            write_text(out_dir / "logs" / f"{safe_name(log_name)}.txt", data)
    else:
        print("[INFO] no relevant logs selected")

    if db.get("PerformanceInsightsEnabled") and db.get("DbiResourceId"):
        print("[INFO] collecting Performance Insights")
        pi_data = collect_performance_insights(
            pi=pi,
            dbi_resource_id=db["DbiResourceId"],
            start=start,
            end=end,
        )
        write_json(out_dir / "performance_insights.json", pi_data)
    else:
        write_json(out_dir / "performance_insights.json", {
            "enabled": False,
            "reason": "Performance Insights is not enabled or DbiResourceId missing.",
        })

    print("[INFO] generating basic summary")
    build_basic_report(out_dir)

    print(f"[DONE] AWS/RDS diagnostics written to: {out_dir}")
    print(f"[DONE] Summary: {out_dir / 'aws_rds_summary.md'}")


if __name__ == "__main__":
    main()