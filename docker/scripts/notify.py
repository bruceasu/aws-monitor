#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests


def send_to_teams(webhook_url, title, content):
    payload = {
        "text": f"**{title}**\n\n{content}"
    }

    response = requests.post(webhook_url, json=payload, timeout=10)
    response.raise_for_status()


def send_teams(webhook_url, title, content):
    send_to_teams(webhook_url, title, content)

# send_to_teams(
#     os.getenv("TEAMS_WEBHOOK"),
#     "RDS Incident RCA",
#     open(report_path).read()[:4000]
# )        
