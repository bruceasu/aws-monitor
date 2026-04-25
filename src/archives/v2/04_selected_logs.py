#!/usr/bin/env python3
#-*- coding: utf-8 -*-

import json
import os
import sys
from datetime import datetime, timezone
out_dir, start_s, end_s = sys.argv[1:4]
path = os.path.join(out_dir, "log_files.json")
try:
    data = json.load(open(path))
except Exception:
    sys.exit(0)
start = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
end = datetime.fromisoformat(end_s.replace("Z", "+00:00"))
keywords = [
    "error",
    "postgresql",
    "slowquery",
    "alert",
    "trace",
    "general",
]
files = data.get("DescribeDBLogFiles", [])
selected = []
for f in files:
    name = f.get("LogFileName", "")
    lname = name.lower()
    if not any(k in lname for k in keywords):
        continue
# LastWritten is epoch milliseconds.
    lw = f.get("LastWritten")
    if lw:
        dt = datetime.fromtimestamp(lw / 1000, tz=timezone.utc)
        # Include logs written in the incident window or shortly after.
        if start.timestamp() - 3600 <= dt.timestamp() <= end.timestamp() + 3600:
            selected.append(name)
    else:
        selected.append(name)
selected = selected[:12]
with open(os.path.join(out_dir, "selected_logs.txt"), "w") as fh:
    for name in selected:
        fh.write(name + "\n")