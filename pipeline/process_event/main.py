"""
process_honeypot_event: Cloud Run function for the CIS 437 SSH honeypot pipeline.

Data flow:
    Cowrie on honeypot-vm
      -> log forwarder publishes each JSON event to Pub/Sub topic "honeypot-events"
      -> Pub/Sub triggers THIS function once per message
      -> we parse the event, look up the attacker IP's location, and
         insert one row into BigQuery table honeypot_data.cowrie_events
      -> the dashboard queries that table

Geolocation uses the free ip-api.com service. If the lookup fails for any
reason, the row is still written with empty location fields, so an outage
at ip-api.com never stops data from reaching BigQuery.
"""

import base64
import json
import os
from datetime import datetime, timezone

import functions_framework
import requests
from google.cloud import bigquery

# Full BigQuery table ID in the form project.dataset.table.
# Read from an environment variable so the same code can point at a test
# table later without editing it. The default is our real table.
BQ_TABLE = os.environ.get(
    "BQ_TABLE", "cis437-cyberstudents-honeypot.honeypot_data.cowrie_events"
)

# ip-api.com free endpoint. The "fields" parameter asks only for what we
# store, which keeps responses small. Free tier: HTTP only, 45 requests/min.
GEO_URL = "http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,city,lat,lon"
GEO_TIMEOUT_SECONDS = 3

# The BigQuery client is created on first use, not at import time.
# A function instance handles many messages, so reusing one client is faster,
# and creating it lazily lets us import this file in local tests without
# needing BigQuery access.
_bq_client = None

# In memory cache of IP -> location results for this function instance.
# A brute force attack sends hundreds of events from one IP; the cache means
# we ask ip-api.com once instead of hundreds of times. The cache is lost when
# the instance shuts down, which is fine.
_geo_cache = {}


def get_bq_client():
    """Return a shared BigQuery client, creating it the first time."""
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client()
    return _bq_client


def geolocate(ip):
    """
    Look up the location of an IP address.

    Returns a dict with country, country_code, city, latitude, longitude.
    Any field can be None if unknown. Never raises: a failed lookup just
    means empty location fields for that row.
    """
    empty = {"country": None, "country_code": None, "city": None,
             "latitude": None, "longitude": None}

    if not ip:
        return empty
    if ip in _geo_cache:
        return _geo_cache[ip]

    try:
        resp = requests.get(GEO_URL.format(ip=ip), timeout=GEO_TIMEOUT_SECONDS)

        # 429 means we hit the 45/min limit. Do NOT cache this, so the IP
        # gets looked up again on a later event once the limit resets.
        if resp.status_code == 429:
            print(f"Geolocation rate limited for {ip}")
            return empty

        data = resp.json()
    except (requests.RequestException, ValueError) as err:
        # Network error, timeout, or a response that was not JSON.
        # Not cached, so we retry on the next event from this IP.
        print(f"Geolocation lookup failed for {ip}: {err}")
        return empty

    if data.get("status") != "success":
        # ip-api answers "fail" for private or reserved addresses
        # (for example 10.x.x.x or our 203.0.113.x test IPs). That answer
        # will never change, so caching it saves pointless repeat lookups.
        print(f"No location for {ip}: {data.get('message')}")
        _geo_cache[ip] = empty
        return empty

    result = {
        "country": data.get("country"),
        "country_code": data.get("countryCode"),
        "city": data.get("city"),
        "latitude": data.get("lat"),
        "longitude": data.get("lon"),
    }
    _geo_cache[ip] = result
    return result


def build_row(event):
    """
    Turn one Cowrie event (a dict) into a row for honeypot_data.cowrie_events.

    Column names here must match the table schema exactly. Cowrie events do
    not all have the same fields (a login has username/password, a command
    has input), so we use .get() and let missing values become NULL.
    """
    now = datetime.now(timezone.utc).isoformat()

    row = {
        # Cowrie timestamps look like 2026-11-22T13:45:00.000000Z, which
        # BigQuery accepts directly. If one is missing we fall back to now,
        # because event_time is REQUIRED and partitions the table.
        "event_time": event.get("timestamp") or now,
        "event_type": event.get("eventid") or "unknown",
        "session": event.get("session"),
        "src_ip": event.get("src_ip"),
        "src_port": event.get("src_port"),
        "dst_port": event.get("dst_port"),
        "username": event.get("username"),
        "password": event.get("password"),
        # Cowrie calls the typed command "input"; we store it as command_input.
        "command_input": event.get("input"),
        "sensor": event.get("sensor"),
        "ingested_at": now,
        # Keep the untouched original for debugging and future columns.
        "raw_event": json.dumps(event),
    }

    # Add the five location columns.
    row.update(geolocate(event.get("src_ip")))
    return row


@functions_framework.cloud_event
def process_honeypot_event(cloud_event):
    """
    Entry point. Pub/Sub calls this once per published message.

    The message arrives wrapped in a CloudEvent. The original text we
    published is base64 encoded at cloud_event.data["message"]["data"].
    """
    # Step 1: unwrap the Pub/Sub message back into the original JSON text.
    try:
        encoded = cloud_event.data["message"]["data"]
        event = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except (KeyError, TypeError, ValueError) as err:
        # A malformed message will never parse no matter how often we retry,
        # so log it and return normally. Returning (not raising) tells
        # Pub/Sub the message is handled.
        print(f"Skipping message that is not valid Cowrie JSON: {err}")
        return

    # Step 2: build the BigQuery row, including geolocation.
    row = build_row(event)

    # Step 3: insert the row. insert_rows_json returns a list of errors,
    # empty on success. It does not raise for bad rows, so we check.
    errors = get_bq_client().insert_rows_json(BQ_TABLE, [row])
    if errors:
        # Raising makes the failure show up clearly in the function's logs.
        raise RuntimeError(f"BigQuery insert failed: {errors}")

    print(f"Stored {row['event_type']} from {row['src_ip']} ({row['country']})")
