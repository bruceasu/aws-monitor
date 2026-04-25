#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

import psycopg2
import psycopg2.extras

from credential_provider import get_db_password
from runbook import build_runbook


def connect():
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=get_db_password(),
        connect_timeout=10,
        sslmode=os.getenv("PGSSLMODE", "require"),
    )


def load_findings(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--findings", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    findings = load_findings(Path(args.findings))
    steps = build_runbook(findings)

    executable_steps = [
        s for s in steps
        if s.auto_executable and s.risk == "LOW" and s.command_type == "sql"
    ]

    results = []

    if not args.execute:
        for s in executable_steps:
            results.append({
                "step_id": s.id,
                "title": s.title,
                "dry_run": True,
                "command": s.command,
            })
    else:
        conn = connect()
        try:
            for s in executable_steps:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(s.command)
                    rows = cur.fetchall()
                    results.append({
                        "step_id": s.id,
                        "title": s.title,
                        "dry_run": False,
                        "rows": rows,
                    })
        finally:
            conn.close()

    Path(args.out).write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )


if __name__ == "__main__":
    main()