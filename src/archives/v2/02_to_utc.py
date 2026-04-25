#!/usr/bin/env python3
#-*- coding: utf-8 -*-

import sys
from datetime import datetime, timezone
s = sys.argv[1]
s = s.replace("Z", "+00:00")
dt = datetime.fromisoformat(s)
if dt.tzinfo is None:
    raise SystemExit("Time must include timezone, e.g. Z or +09:00")
print(dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))