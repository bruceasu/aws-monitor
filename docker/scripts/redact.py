#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re

def redact_text(text: str) -> str:
    rules = [
        (r"\b\d{1,3}(\.\d{1,3}){3}\b", "[IP]"),
        (r"(?i)password\s*=\s*\S+", "password=[REDACTED]"),
        (r"(?i)token\s*=\s*\S+", "token=[REDACTED]"),
        (r"(?i)authorization:\s*bearer\s+\S+", "authorization: [REDACTED]"),
        (r"'[^']*'", "'?'")  # SQL literal
    ]

    for pattern, repl in rules:
        text = re.sub(pattern, repl, text)

    return text