#!/usr/bin/env bash
# Stop all cloud spend for this project's AgentOps resources. macOS/Linux twin of teardown.ps1.
#   ./deploy/teardown.sh --project my-proj
set -uo pipefail
PROJECT=""; REGION="asia-south1"; SERVICE="agentops"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --service) SERVICE="$2"; shift 2;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[[ -n "$PROJECT" ]] || { echo "usage: $0 --project PROJECT_ID" >&2; exit 2; }

gcloud run services delete "$SERVICE" --region "$REGION" --project "$PROJECT" --quiet
# `gcloud run deploy --source` stores images in this auto-created repo; the free tier covers only 0.5 GB.
gcloud artifacts repositories delete cloud-run-source-deploy --location "$REGION" --project "$PROJECT" --quiet
gcloud iam service-accounts delete "agentops-run@${PROJECT}.iam.gserviceaccount.com" --project "$PROJECT" --quiet
echo "Removed Cloud Run service, source-deploy images and runtime service account."
echo "To remove everything including billing exposure: gcloud projects delete $PROJECT"
