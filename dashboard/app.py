import json
import os
from collections import Counter
from pathlib import Path

from flask import Flask, render_template

app = Flask(__name__)
PROJECT_ID = "cis437-cyberstudents-honeypot"
SAMPLE_FILE = Path(__file__).resolve().parents[1] / "sample_data" / "demo_events.json"
LOGIN_TYPES = {"cowrie.login.failed", "cowrie.login.success"}

LOGIN_QUERY = """
SELECT
  FORMAT_TIMESTAMP('%Y-%m-%d %H:00', event_time, 'UTC') AS hour_utc,
  event_type,
  NULLIF(TRIM(country), '') AS country,
  NULLIF(TRIM(src_ip), '') AS src_ip,
  COUNT(*) AS attempts
FROM `cis437-cyberstudents-honeypot.honeypot_data.cowrie_events`
WHERE event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
  AND event_type IN ('cowrie.login.failed', 'cowrie.login.success')
GROUP BY 1, 2, 3, 4
"""


def load_sample_rows():
    events = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    for event in events:
        if event["event_type"] in LOGIN_TYPES:
            timestamp = event.get("event_time") or event.get("timestamp")
            yield {
                "hour_utc": timestamp[:13].replace("T", " ") + ":00" if timestamp else None,
                "event_type": event["event_type"],
                "country": event.get("country"),
                "src_ip": event.get("src_ip"),
                "attempts": 1,
            }


def load_bigquery_rows():
    from google.cloud import bigquery

    with bigquery.Client(project=PROJECT_ID) as client:
        yield from client.query_and_wait(LOGIN_QUERY)


@app.route("/")
def dashboard():
    use_bigquery = os.getenv("DASHBOARD_DATA_SOURCE") == "bigquery"
    rows = load_bigquery_rows() if use_bigquery else load_sample_rows()

    hourly = Counter()
    countries = Counter()
    source_ips = Counter()
    total = failed = 0

    for row in rows:
        count = row["attempts"]
        total += count
        if row["event_type"] == "cowrie.login.failed":
            failed += count
        if row["hour_utc"]:
            hourly[row["hour_utc"]] += count
        if row["country"]:
            countries[row["country"]] += count
        if row["src_ip"]:
            source_ips[row["src_ip"]] += count

    return render_template(
        "index.html",
        data_label="BigQuery · Last 7 days" if use_bigquery else "Demo · Sample data",
        total_attempts=total,
        failed_logins=failed,
        country_count=len(countries),
        unique_ips=len(source_ips),
        hourly_attempts=sorted(hourly.items()),
        top_countries=countries.most_common(10),
        top_ips=source_ips.most_common(10),
    )
