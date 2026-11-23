import json
from pathlib import Path

from flask import Flask, render_template

app = Flask(__name__)
sample_file = Path(__file__).resolve().parents[1] / "sample_data" / "demo_events.json"


@app.route("/")
def dashboard():
    events = json.loads(sample_file.read_text(encoding="utf-8"))
    attempts = [
        event for event in events
        if event["event_type"] in {"cowrie.login.failed", "cowrie.login.success"}
    ]
    countries = {event["country"] for event in attempts if event.get("country")}
    source_ips = {event["src_ip"] for event in attempts if event.get("src_ip")}

    return render_template(
        "index.html",
        total_attempts=len(attempts),
        failed_logins=sum(event["event_type"] == "cowrie.login.failed" for event in attempts),
        country_count=len(countries),
        unique_ips=len(source_ips) if source_ips else "—",
    )
