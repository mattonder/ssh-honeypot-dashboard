"""
Local test for main.py. Checks parsing and geolocation WITHOUT writing to
BigQuery, so it is safe to run as often as you like.

Run from the process_event folder:  python3 test_local.py
"""

import json

from main import build_row

# Three sample events shaped like real Cowrie log lines.
# 8.8.8.8 is Google's public DNS server, a real IP that geolocates.
# 203.0.113.45 is a reserved documentation IP, so its location should be empty.
samples = [
    {"eventid": "cowrie.session.connect", "src_ip": "8.8.8.8", "src_port": 40112,
     "dst_port": 22, "session": "s1", "timestamp": "2026-11-22T13:44:59.000000Z",
     "sensor": "honeypot-vm"},
    {"eventid": "cowrie.login.failed", "username": "root", "password": "123456",
     "src_ip": "8.8.8.8", "src_port": 40112, "dst_port": 22, "session": "s1",
     "timestamp": "2026-11-22T13:45:00.000000Z", "sensor": "honeypot-vm"},
    {"eventid": "cowrie.command.input", "input": "uname -a", "src_ip": "203.0.113.45",
     "session": "s2", "timestamp": "2026-11-22T13:46:00.000000Z", "sensor": "honeypot-vm"},
]

for event in samples:
    row = build_row(event)
    # Hide raw_event in the printout to keep it readable.
    row.pop("raw_event")
    print(json.dumps(row, indent=2))
    print("-" * 40)
