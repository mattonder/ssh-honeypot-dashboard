# Infrastructure Setup

Commands run in Cloud Shell.

| Project ID | `cis437-cyberstudents-honeypot` |
| Region / zone | `us-central1` / `us-central1-a` |
| Billing | Education credits (no credit card) |

## 1. Project and APIs

Project created in the console and linked to the education billing account.

```bash
PROJECT_ID=cis437-cyberstudents-honeypot
gcloud config set project $PROJECT_ID
gcloud services enable compute.googleapis.com iap.googleapis.com \
  pubsub.googleapis.com run.googleapis.com bigquery.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com
```

## 2. Budget

Billing → Budgets & caps → Create budget → **Alerts only**

- Scope: this project, all services
- Savings/credits: all unchecked, so the budget tracks gross cost instead of the $0 net cost after credits
- Amount: $25/month
- Alerts: 50%, 90%, 100% actual; 100% forecasted

## 3. Team access

IAM → Grant access → team members' GVSU accounts as **Editor**.

## 4. Isolated network

The honeypot runs in its own VPC, separate from the default network.

```bash
gcloud compute networks create honeypot-vpc --subnet-mode=custom

gcloud compute networks subnets create honeypot-subnet \
  --network=honeypot-vpc --region=us-central1 --range=10.10.0.0/24
```

## 5. Firewall

Admin SSH is only allowed from Google's Identity-Aware Proxy (IAP) range. Port 22222 is reserved for the real SSH daemon once port 22 is handed to the honeypot.

```bash
gcloud compute firewall-rules create hp-allow-admin-iap \
  --network=honeypot-vpc --direction=INGRESS --action=ALLOW \
  --rules=tcp:22,tcp:22222 --source-ranges=35.235.240.0/20 \
  --target-tags=honeypot
```

Port 22 is **not** open to the internet until Cowrie is installed and listening.

## 6. VM service account

A dedicated, least-privilege service account instead of the Compute Engine default (which has broad project access). It starts with no roles; it will get `roles/pubsub.publisher` on the attack-events topic only.

```bash
gcloud iam service-accounts create honeypot-vm --display-name="Honeypot VM"
```

## 7. Honeypot VM

```bash
gcloud compute instances create honeypot-vm \
  --zone=us-central1-a --machine-type=e2-small \
  --network-interface=subnet=honeypot-subnet \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=20GB --boot-disk-type=pd-standard \
  --service-account=honeypot-vm@$PROJECT_ID.iam.gserviceaccount.com \
  --scopes=cloud-platform \
  --tags=honeypot --shielded-secure-boot
```

## 8. Connecting

```bash
gcloud compute ssh honeypot-vm --zone=us-central1-a --tunnel-through-iap
sudo apt update && sudo apt upgrade -y
```

## Next steps

- [ ] Move the real SSH daemon to port 22222
- [ ] Install Cowrie and redirect port 22 → Cowrie (2222)
- [ ] Open tcp:22 to `0.0.0.0/0` for the honeypot
- [ ] Create the Pub/Sub topic and grant the VM service account publish access
