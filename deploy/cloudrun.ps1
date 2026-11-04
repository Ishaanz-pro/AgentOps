# Deploy the AgentOps API + UI to Cloud Run, keyless on Vertex AI.
#
#   .\deploy\cloudrun.ps1 -Project my-proj                       # private service (IAM-authenticated)
#   .\deploy\cloudrun.ps1 -Project my-proj -Public -Budget 200    # public demo link, capped at 200 chats/day
#
# Prerequisites (one time): gcloud CLI installed and `gcloud auth login`, a project with billing enabled.
# What it does, in order: enables APIs, creates a least-privilege runtime service account, builds the
# Dockerfile with Cloud Build, and deploys. No API key is used anywhere: Gemini calls go through Vertex AI
# with the service account's identity.
param(
  [Parameter(Mandatory = $true)][string]$Project,
  [string]$Region = "asia-south1",
  [string]$Service = "agentops",
  [string]$Model = "gemini-3.5-flash",
  [string]$ModelLocation = "global",
  [switch]$Public,
  [int]$Budget = 200,
  [string]$CalendarId = ""
)
$ErrorActionPreference = "Stop"
function Run($cmd) { Write-Host "> $cmd" -ForegroundColor Cyan; Invoke-Expression $cmd; if ($LASTEXITCODE -ne 0) { throw "failed: $cmd" } }

Run "gcloud config set project $Project"
Run "gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com logging.googleapis.com cloudtrace.googleapis.com calendar-json.googleapis.com"

$sa = "agentops-run@$Project.iam.gserviceaccount.com"
$exists = gcloud iam service-accounts list --filter="email:$sa" --format="value(email)"
if (-not $exists) { Run "gcloud iam service-accounts create agentops-run --display-name='AgentOps Cloud Run runtime'" }
# Least privilege: call Vertex AI, write logs and traces. Nothing else.
foreach ($role in "roles/aiplatform.user", "roles/logging.logWriter", "roles/cloudtrace.agent") {
  Run "gcloud projects add-iam-policy-binding $Project --member=serviceAccount:$sa --role=$role --condition=None --quiet"
}

$calendar = if ($CalendarId) { "AGENTOPS_CALENDAR=google,AGENTOPS_CALENDAR_ID=$CalendarId" } else { "AGENTOPS_CALENDAR=dryrun" }
$envs = "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$Project,GOOGLE_CLOUD_LOCATION=$ModelLocation," +
        "AGENTOPS_MODE=gemini,AGENTOPS_EMBEDDINGS=gemini,AGENTOPS_MODEL=$Model,AGENTOPS_DAILY_CHAT_BUDGET=$Budget,$calendar"
$auth = if ($Public) { "--allow-unauthenticated" } else { "--no-allow-unauthenticated" }

# max-instances=1: sessions, traces and pending confirmations are in-process memory (see README limitations).
Run ("gcloud run deploy $Service --source . --region $Region --service-account $sa " +
     "--set-env-vars `"$envs`" --max-instances 1 --min-instances 0 --cpu 1 --memory 1Gi --timeout 120 $auth --quiet")

$url = gcloud run services describe $Service --region $Region --format="value(status.url)"
Write-Host "`nDeployed: $url" -ForegroundColor Green
Write-Host "Health:   $url/health"
if (-not $Public) { Write-Host "Private service. Test with: curl -H `"Authorization: Bearer `$(gcloud auth print-identity-token)`" $url/health" }
if ($CalendarId) { Write-Host "Share calendar $CalendarId with $sa ('Make changes to events') or writes will return 403." }
