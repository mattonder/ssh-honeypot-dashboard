# SSH Honeypot Dashboard

An SSH honeypot on Google Cloud that captures real login attempts and displays them on a live attack dashboard.

GVSU CIS 437 Cloud Computing Term Project.

## Architecture

| Service | Role |
|---|---|
| Compute Engine | Runs the Cowrie SSH honeypot and a log forwarder |
| Pub/Sub | Streams each honeypot event to the processing pipeline |
| Cloud Run functions | Parses each event, adds geolocation, and writes it to BigQuery |
| BigQuery | Stores attack data and answers dashboard queries |
| Cloud Run | Hosts the web dashboard with a world map and charts |

## Roles

| Role | Responsibilities | Assigned to |
|---|---|---|
| Infrastructure and security | GCP project, IAM, firewall rules, VM, Cowrie, log forwarder | TBD |
| Data pipeline | Pub/Sub topic, processing function, geolocation, BigQuery table and queries | TBD |
| Dashboard and demo | Cloud Run dashboard, attack simulation script, screenshots | TBD |
