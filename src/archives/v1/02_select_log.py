#!/usr/bin/env python3
#-*- coding: utf-8 -*-

import json
import os
import sys
out_dir = sys.argv[1]
log_file = os.path.join(out_dir, "log_files.json")
try:
    data = json.load(open(log_file))
except Exception:
    sys.exit(0)
files = data.get("DescribeDBLogFiles", [])
keywords = [
    "error",
    "postgresql",
    "slowquery",
    "alert",
    "trace",
    "general",
]
selected = []
for f in files:
    name = f.get("LogFileName", "")
    lname = name.lower()
    if any(k in lname for k in keywords):
        selected.append(name)
# Most recent files first if LastWritten exists.
def last_written(name):
    for f in files:
        if f.get("LogFileName") == name:
            return f.get("LastWritten", 0)
    return 0
selected = sorted(set(selected), key=last_written, reverse=True)[:8]
with open(os.path.join(out_dir, "selected_logs.txt"), "w") as fh:
    for name in selected:
        fh.write(name + "\n")