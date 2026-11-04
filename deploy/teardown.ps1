# Stop all cloud spend for this project's AgentOps resources. Safe to run any time; asks nothing, deletes only what
# cloudrun.ps1 created.  .\deploy\teardown.ps1 -Project my-proj
param(
  [Parameter(Mandatory = $true)][string]$Project,
  [string]$Region = "asia-south1",
  [string]$Service = "agentops"
)
$ErrorActionPreference = "Continue"
gcloud run services delete $Service --region $Region --project $Project --quiet
# `gcloud run deploy --source` stores images in this auto-created repo; the free tier covers only 0.5 GB.
gcloud artifacts repositories delete cloud-run-source-deploy --location $Region --project $Project --quiet
gcloud iam service-accounts delete "agentops-run@$Project.iam.gserviceaccount.com" --project $Project --quiet
Write-Host "Removed Cloud Run service, source-deploy images and runtime service account." -ForegroundColor Green
Write-Host "To remove everything including billing exposure: gcloud projects delete $Project"
