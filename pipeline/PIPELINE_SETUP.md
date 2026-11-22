# Data Pipeline Setup Notes (Role 2)

Owner: Matt (tonderj@mail.gvsu.edu)
Last updated: November 22, 2026
GCP project: `cis437-cyberstudents-honeypot`
Region for everything: `us-central1`

These notes record what has been built for the data pipeline so far, the exact commands used, and what is still pending. They double as raw material for the README setup section.

## 1. Pipeline overview

Data flows through the pipeline in this order:

1. Cowrie runs on `honeypot-vm` and writes one JSON line per event.
2. A log forwarder on the VM publishes each event to the Pub/Sub topic `honeypot-events`.
3. Pub/Sub triggers the Cloud Run function `process-honeypot-event` once per message.
4. The function parses the event, adds geolocation from ip-api.com, and inserts one row into the BigQuery table `honeypot_data.cowrie_events`.
5. The dashboard (Role 3) queries that table.

Steps 1 and 2 belong to Role 1. Steps 3 and 4 belong to Role 2. Step 5 belongs to Role 3.

## 2. Review of the existing project setup (done by Role 1)

Checked on November 22 with read only commands.

What is in place and done well:

* Isolated project with a custom VPC named `honeypot-vpc` (VM internal IP `10.10.0.2`).
* VM `honeypot-vm` (e2-small, us-central1-a, external IP `34.67.6.81`) runs as a dedicated service account `honeypot-vm@cis437-cyberstudents-honeypot.iam.gserviceaccount.com`. That account has no project roles, so a compromised VM cannot reach other resources.
* Firewall rule `hp-allow-admin-iap` allows ports 22 and 22222 only from Google's IAP range `35.235.240.0/20`, target tag `honeypot`.

What is still missing:

* Cowrie is not installed yet. Only the real `sshd` is listening, on port 22. Nothing listens on 2222 or 22222, and there are no iptables redirects.
* No firewall rule yet lets the internet reach the honeypot. Dr. Zhuang approved real internet exposure.
* Required order for Role 1, to avoid locking everyone out:
  1. Move the real sshd to port 22222 and confirm login through IAP on 22222 before closing anything.
  2. Install Cowrie as a non root user, listening on 2222.
  3. Redirect port 22 to 2222 with iptables, made persistent across reboots.
  4. Only then create a firewall rule allowing `0.0.0.0/0` on tcp 22 to the `honeypot` tag.
* The `default` network has wide open rules (`default-allow-ssh`, `default-allow-rdp`, `default-allow-icmp` from `0.0.0.0/0`). No VM uses it, but any new VM created without choosing a network would land there exposed. Recommendation: delete those rules or the whole default network.
* The default Compute Engine service account `967524289932-compute@developer.gserviceaccount.com` has `roles/editor` on the project. It is not attached to the VM, but anything deployed without naming a service account runs as it. Always name a dedicated service account.
* Running `gcloud compute ssh` added Matt's Cloud Shell SSH key to project wide metadata. Consider enabling OS Login on the VM so access is controlled by IAM instead of metadata keys.

Project IAM at the time of review:

* `yube@mail.gvsu.edu`: Owner (project creator, Role 1)
* `tonderj@mail.gvsu.edu` and `buurib@mail.gvsu.edu`: `roles/writer` (legacy name for Editor). Editors cannot grant IAM roles, so project level grants must be done by the Owner.

## 3. What has been built (Role 2)

### Step 1: Pub/Sub topic

The topic is the channel the VM publishes Cowrie events to. A topic stores nothing by itself; messages are kept only for subscriptions that exist when the message is published.

```bash
gcloud config set project cis437-cyberstudents-honeypot
gcloud pubsub topics create honeypot-events
gcloud pubsub topics list
```

Result: topic `projects/cis437-cyberstudents-honeypot/topics/honeypot-events` created.

### Step 2: Topic test with a debug subscription

```bash
gcloud pubsub subscriptions create honeypot-events-debug --topic honeypot-events

gcloud pubsub topics publish honeypot-events --message='{"eventid":"cowrie.login.failed","username":"root","password":"123456","src_ip":"203.0.113.45","src_port":51234,"dst_port":22,"session":"a1b2c3d4","timestamp":"2026-11-22T13:45:00.000000Z","sensor":"honeypot-vm"}'

gcloud pubsub subscriptions pull honeypot-events-debug --auto-ack --limit 5
```

Result: message published and pulled back with `ACK_STATUS: SUCCESS`.
The subscription `honeypot-events-debug` was kept for troubleshooting. Delete it before final submission:

```bash
gcloud pubsub subscriptions delete honeypot-events-debug
```

### Step 3: BigQuery dataset

```bash
bq --location=us-central1 mk --dataset \
  --description "Cowrie SSH honeypot events for the CIS 437 term project" \
  cis437-cyberstudents-honeypot:honeypot_data
```

Result: dataset `honeypot_data` created. Matt is listed as dataset Owner, so Matt can grant access on this dataset and its tables without the project Owner. Deleted data can be recovered for 7 days (time travel window of 168 hours).

### Step 4: BigQuery table

Table `honeypot_data.cowrie_events`, one row per Cowrie event, partitioned by day on `event_time` so time range queries only scan the days they need.

Columns (schema file: `pipeline/cowrie_events_schema.json`):

* From Cowrie: `event_time` (TIMESTAMP, required), `event_type` (STRING, required), `session`, `src_ip`, `src_port` (INTEGER), `dst_port` (INTEGER), `username`, `password`, `command_input`, `sensor`
* Added by the function: `country`, `country_code`, `city`, `latitude` (FLOAT), `longitude` (FLOAT)
* Bookkeeping: `ingested_at` (TIMESTAMP, required), `raw_event` (original JSON as STRING)

Columns that do not apply to an event type are left NULL.

```bash
bq mk --table \
  --time_partitioning_field event_time \
  --time_partitioning_type DAY \
  --description "One row per Cowrie honeypot event, enriched with geolocation" \
  honeypot_data.cowrie_events \
  ~/cowrie_events_schema.json

bq show --schema --format=prettyjson honeypot_data.cowrie_events
```

Result: table created with all 17 columns.

### Step 5: Function code and local test

Decision: geolocation uses the free ip-api.com service instead of a local MaxMind database, because it is simpler and the project's goal is service integration, not geolocation accuracy. Mitigations built into the code:

* If a lookup fails (service down, rate limit, private IP), the row is still written with empty location fields.
* Results are cached in memory per function instance, so repeated events from one attacker IP cause only one lookup.
* Free tier limits: 45 requests per minute, HTTP only. City level locations are estimates (ip-api even returned a city for the reserved test IP 203.0.113.45).

Files in `pipeline/process_event/`:

* `main.py`: the function. Entry point `process_honeypot_event` decodes the Pub/Sub message, `build_row` maps Cowrie fields to table columns (Cowrie's `input` becomes `command_input`), `geolocate` calls ip-api.com with caching, and the row is inserted with `insert_rows_json`.
* `requirements.txt`: `functions-framework`, `google-cloud-bigquery`, `requests`.
* `test_local.py`: builds three sample rows without writing to BigQuery.
* `.gcloudignore`: keeps `.venv/`, `__pycache__/`, and the test file out of the deploy upload.

Local test (run in Cloud Shell):

```bash
cd ~/process_event
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 test_local.py
deactivate
```

Result: all three sample rows built correctly; `8.8.8.8` geolocated to Ashburn, United States.

### Step 6: APIs and the function's service account

The function gets its own identity instead of the default Compute account that has Editor. It can only insert rows into one table.

```bash
gcloud services enable cloudfunctions.googleapis.com eventarc.googleapis.com

gcloud iam service-accounts create honeypot-processor \
  --display-name "Honeypot event processor function"

bq query --use_legacy_sql=false \
  'GRANT `roles/bigquery.dataEditor` ON TABLE `cis437-cyberstudents-honeypot.honeypot_data.cowrie_events` TO "serviceAccount:honeypot-processor@cis437-cyberstudents-honeypot.iam.gserviceaccount.com"'

bq get-iam-policy honeypot_data.cowrie_events
```

Result: service account `honeypot-processor` created with `roles/bigquery.dataEditor` on the `cowrie_events` table only.

### Step 7: Deploy the function (in progress, blocked)

```bash
cd ~/process_event

gcloud functions deploy process-honeypot-event \
  --gen2 \
  --region us-central1 \
  --runtime python312 \
  --source . \
  --entry-point process_honeypot_event \
  --trigger-topic honeypot-events \
  --service-account honeypot-processor@cis437-cyberstudents-honeypot.iam.gserviceaccount.com \
  --trigger-service-account honeypot-processor@cis437-cyberstudents-honeypot.iam.gserviceaccount.com \
  --memory 256Mi \
  --timeout 60s \
  --max-instances 3
```

`--max-instances 3` caps cost and keeps lookups under the ip-api rate limit during attack bursts.

Result: failed with a 403 while validating the Eventarc trigger:
"Caller does not have required permission to use project ... Grant the caller the roles/serviceusage.serviceUsageConsumer role."

Likely cause: Matt's legacy `roles/writer` lacks `serviceusage.services.use`. Possible alternative cause: propagation delay right after enabling Eventarc.

## 4. Pending items

Needs the project Owner (yube):

1. Grant Matt the Service Usage Consumer role, then rerun the deploy command:
   ```bash
   gcloud projects add-iam-policy-binding cis437-cyberstudents-honeypot --member="user:tonderj@mail.gvsu.edu" --role="roles/serviceusage.serviceUsageConsumer"
   ```
2. After the function exists, grant the trigger permission to invoke it if the deploy could not do it automatically (exact command to be confirmed after deploy):
   ```bash
   gcloud run services add-iam-policy-binding process-honeypot-event --region us-central1 --member="serviceAccount:honeypot-processor@cis437-cyberstudents-honeypot.iam.gserviceaccount.com" --role="roles/run.invoker"
   ```
3. Grant `honeypot-vm@cis437-cyberstudents-honeypot.iam.gserviceaccount.com` the role `roles/pubsub.publisher` on the topic `honeypot-events` only, so the forwarder can publish.

Role 2 next steps:

1. Finish the deploy and confirm `state: ACTIVE`.
2. End to end test: publish a sample event to `honeypot-events`, then confirm a row appears in BigQuery and check the function logs.
3. Write example dashboard queries for Role 3 (top countries, top usernames and passwords, attempts over time).
4. Agree on the exact event format with Role 1's forwarder (one Cowrie JSON line per Pub/Sub message, unchanged).

Cleanup before submission:

* Delete the debug subscription `honeypot-events-debug`.
* Teardown after Week 15: function, topic, dataset, service accounts, and the VM.

## 5. AI use acknowledgment

Claude (Anthropic) was used to review the existing project configuration, design the pipeline and table schema, write and comment the function code, and produce these notes. All commands were run and verified by the team.
