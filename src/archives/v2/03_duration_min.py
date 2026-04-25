#!/usr/bin/env python3
#-*- coding: utf-8 -*-

import sys
from datetime import datetime, timezone
s = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
e = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
print(max(1, int((e - s).total_seconds() / 60)))