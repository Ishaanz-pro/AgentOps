# Deploy the same ADK agent to Vertex AI Agent Engine, then register it in Gemini Enterprise.
#
#   .\deploy\agent_engine.ps1 -Project my-proj
#
# Uses the repo's own ADK CLI (google-adk 2.13). The agent folder is deploy/agent_engine/employee_assistant;
# its .agent_engine_config.json stages src/agentops and data/ next to it, so the deployed agent runs the
# exact same tools, authorization and RAG code that the tests cover.
param(
  [Parameter(Mandatory = $true)][string]$Project,
  [string]$Region = "us-central1",
  [string]$AgentEngineId = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$adk = Join-Path $root ".venv\Scripts\adk.exe"

gcloud config set project $Project
gcloud services enable aiplatform.googleapis.com cloudbuild.googleapis.com discoveryengine.googleapis.com
if ($LASTEXITCODE -ne 0) { throw "enabling APIs failed" }

# The runtime reads GOOGLE_CLOUD_PROJECT for Vertex AI; everything else is in .agent_engine_config.json.
$envFile = Join-Path $root "deploy\agent_engine\employee_assistant\.env"
"GOOGLE_CLOUD_PROJECT=$Project`nGOOGLE_CLOUD_LOCATION=global`nGOOGLE_GENAI_USE_VERTEXAI=true`nAGENTOPS_TRUST_RUNNER_USER_ID=1`nAGENTOPS_MODE=gemini" |
  Set-Content -Encoding utf8 $envFile

Push-Location (Join-Path $root "deploy\agent_engine")
try {
  $adkArgs = @("deploy", "agent_engine", "--project", $Project, "--region", $Region, "--otel_to_cloud",
               "--display_name", "AgentOps Employee Assistant")
  if ($AgentEngineId) { $adkArgs += @("--agent_engine_id", $AgentEngineId) }  # update in place instead of creating
  $adkArgs += "employee_assistant"
  & $adk @adkArgs
  if ($LASTEXITCODE -ne 0) { throw "adk deploy failed" }
} finally { Pop-Location; Remove-Item $envFile -ErrorAction SilentlyContinue }

Write-Host "`nNext: register it in Gemini Enterprise (console, about 5 minutes):" -ForegroundColor Green
Write-Host "  https://docs.cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent"
Write-Host "  Use the reasoningEngines/... resource name printed above."
Write-Host "  Test users must map to demo principals: the user_id Gemini Enterprise sends must be u-alice, u-bob, ..."
Write-Host "  (see docs/deploy.md, 'Identity in Gemini Enterprise')."
