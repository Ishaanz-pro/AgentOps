#!/usr/bin/env bash
# Deploy the AgentOps API + UI to Cloud Run, keyless on Vertex AI. macOS/Linux twin of cloudrun.ps1.
#
#   ./deploy/cloudrun.sh --project my-proj                      # private service (IAM-authenticated)
#   ./deploy/cloudrun.sh --project my-proj --public --budget 100  # public demo link, capped at 100 chats/day
#
# Prerequisites: gcloud installed, `gcloud auth login`, a project with the Free Trial billing account linked.
# No API key is used anywhere: Gemini calls go through Vertex AI with the service account's identity.
set -euo pipefail

PROJECT=""; REGION="asia-south1"; SERVICE="agentops"; MODEL="gemini-3.5-flash"
MODEL_LOCATION="global"; PUBLIC=0; BUDGET=200; CALENDAR_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --service) SERVICE="$2"; shift 2;;
    --model) MODEL="$2"; shift 2;;
    --model-location) MODEL_LOCATION="$2"; shift 2;;
    --budget) BUDGET="$2"; shift 2;;
    --calendar-id) CALENDAR_ID="$2"; shift 2;;
    --public) PUBLIC=1; shift;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[[ -n "$PROJECT" ]] || { echo "usage: $0 --project PROJECT_ID [--public] [--budget N]" >&2; exit 2; }

command -v gcloud >/dev/null || { echo "gcloud not found. See docs/deploy.md step 1." >&2; exit 1; }
cd "$(dirname "$0")/.."

gcloud config set project "$PROJECT"
BILLING="$(gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)')"
if [[ "$BILLING" != "True" ]]; then
  echo "Project $PROJECT has no billing account linked. Cloud Run needs one even for free-tier use." >&2
  echo "Link the Free Trial billing account in the console (docs/deploy.md step 3)." >&2
  exit 1
fi

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com logging.googleapis.com cloudtrace.googleapis.com calendar-json.googleapis.com

SA="agentops-run@${PROJECT}.iam.gserviceaccount.com"
if [[ -z "$(gcloud iam service-accounts list --filter="email:${SA}" --format='value(email)')" ]]; then
  gcloud iam service-accounts create agentops-run --display-name="AgentOps Cloud Run runtime"
fi
# Least privilege: call Vertex AI, write logs and traces. Nothing else.
# A just-created service account can take a few seconds to be visible to IAM, so retry each binding.
for ROLE in roles/aiplatform.user roles/logging.logWriter roles/cloudtrace.agent; do
  for ATTEMPT in 1 2 3 4 5 6; do
    if gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${SA}" --role="$ROLE" --condition=None --quiet >/dev/null; then break; fi
    [[ $ATTEMPT -lt 6 ]] || { echo "Could not grant $ROLE to $SA after 6 tries." >&2; exit 1; }
    echo "IAM has not caught up yet (attempt $ATTEMPT), waiting 10s..."; sleep 10
  done
done

if [[ -n "$CALENDAR_ID" ]]; then CAL="AGENTOPS_CALENDAR=google,AGENTOPS_CALENDAR_ID=${CALENDAR_ID}"; else CAL="AGENTOPS_CALENDAR=dryrun"; fi
ENVS="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${MODEL_LOCATION},AGENTOPS_MODE=gemini,AGENTOPS_EMBEDDINGS=gemini,AGENTOPS_MODEL=${MODEL},AGENTOPS_DAILY_CHAT_BUDGET=${BUDGET},${CAL}"
if [[ $PUBLIC -eq 1 ]]; then AUTH="--allow-unauthenticated"; else AUTH="--no-allow-unauthenticated"; fi

# max-instances=1: sessions, traces and pending confirmations are in-process memory (see README limitations).
gcloud run deploy "$SERVICE" --source . --region "$REGION" --service-account "$SA" \
  --set-env-vars "$ENVS" --max-instances 1 --min-instances 0 --cpu 1 --memory 1Gi --timeout 120 $AUTH --quiet

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo; echo "Deployed: $URL"; echo "Health:   $URL/health"
[[ $PUBLIC -eq 1 ]] || echo "Private service. Test: curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" $URL/health"
[[ -z "$CALENDAR_ID" ]] || echo "Share calendar $CALENDAR_ID with $SA ('Make changes to events') or writes return 403."
