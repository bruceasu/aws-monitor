import os
import subprocess
from datetime import datetime, timedelta, timezone

import boto3


def get_metric_max(cw, db, metric, minutes=10):
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)

    resp = cw.get_metric_statistics(
        Namespace="AWS/RDS",
        MetricName=metric,
        Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db}],
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=["Maximum"]
    )

    values = [p["Maximum"] for p in resp.get("Datapoints", [])]
    return max(values) if values else 0


def main():
    db = os.environ["DB_INSTANCE"]
    region = os.environ["AWS_REGION"]

    cw = boto3.client("cloudwatch", region_name=region)

    cpu = get_metric_max(cw, db, "CPUUtilization")
    conns = get_metric_max(cw, db, "DatabaseConnections")
    write_latency = get_metric_max(cw, db, "WriteLatency")

    triggered = False
    reasons = []

    if cpu > float(os.getenv("TRIGGER_CPU", "90")):
        triggered = True
        reasons.append(f"CPUUtilization={cpu}")

    if conns > float(os.getenv("TRIGGER_CONNECTIONS", "300")):
        triggered = True
        reasons.append(f"DatabaseConnections={conns}")

    if write_latency > float(os.getenv("TRIGGER_WRITE_LATENCY", "0.2")):
        triggered = True
        reasons.append(f"WriteLatency={write_latency}")

    if not triggered:
        print("[OK] no trigger condition met")
        return

    print("[TRIGGERED]", ", ".join(reasons))

    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=30)
    end = now + timedelta(minutes=5)

    env = os.environ.copy()
    env["START_TIME"] = start.isoformat()
    env["END_TIME"] = end.isoformat()

    subprocess.run(
        ["python", "scripts/run_incident.py"],
        check=True,
        env=env
    )


if __name__ == "__main__":
    main()