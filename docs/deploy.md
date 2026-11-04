# Deploying on Google Cloud for $0

Status: **scripts written, nothing deployed yet.** Every command below is something you run. Fill in the
"Deployment log" at the end with real outputs, and only then update the README's scope note.

## The honest cost picture (checked against Google's docs, 2026-11-04)

| Fact | Source of truth |
|---|---|
| The **Free Trial** gives $300 credit for 90 days. A payment method is required (a $0 to $1 authorization hold, not a charge). | <https://docs.cloud.google.com/free/docs/free-cloud-features> |
| **Nothing is billed automatically.** If you never upgrade to a paid account, the trial account closes when credit or time runs out and resources stop. You are billed only after you upgrade manually. | same page |
| Cloud Run **always-free**: 2M requests, 180,000 vCPU-seconds, 360,000 GiB-seconds per month. Cloud Build 2,500 min/month. Secret Manager and 0.5 GB Artifact Registry also have free quotas. A billing account is still required. | same page |
| **Gemini on Vertex AI is not free**, it is paid by trial credit. At this app's volume (capped at 200 chat turns per day) it should consume a very small fraction of the $300, but check Billing > Reports after your first test. | Billing reports |

What this means for you:

- **$0 out of pocket is realistic** if you stay on the Free Trial and do not click "Activate / Upgrade".
- **The URL will not live forever.** After 90 days the trial account closes and the service stops. Capture the
  evidence in step 6 (screenshots, `docs/deployment-evidence.md`, a short screen recording) while it is up.
- **Do not claim** things you did not deploy. Agent Engine and Gemini Enterprise are separate, paid or
  eligibility-gated products. Skip them unless your organisation gives you access. The Cloud Run deployment alone is
  honest, deployable proof of "I shipped an ADK agent on Google Cloud with Vertex AI".

## Using a Mac (or Linux)

Every PowerShell command below has a bash twin. Same flags, written `--project` style:

| Windows | macOS / Linux |
|---|---|
| `winget install Google.CloudSDK` | `brew install --cask google-cloud-sdk` |
| `.\deploy\cloudrun.ps1 -Project P -Public -Budget 100` | `./deploy/cloudrun.sh --project P --public --budget 100` |
| `.\deploy\teardown.ps1 -Project P` | `./deploy/teardown.sh --project P` |
| `$env:NAME="v"` | `export NAME=v` |
| `python scripts\cloud_evidence.py URL` | `python3 scripts/cloud_evidence.py URL` |

The `.sh` files were syntax-checked (`bash -n`) but not run on a Mac by the author.

## Step 1. Install gcloud (10 minutes)

PowerShell:

```powershell
winget install Google.CloudSDK
# open a NEW terminal, then:
gcloud --version
gcloud auth login            # opens a browser; sign in with the Google account for the trial
```

No installs on your machine? Use **Cloud Shell** (<https://shell.cloud.google.com>): gcloud is preinstalled and free.
You would need the repo on GitHub to clone it there.

## Step 2. Start the Free Trial

<https://cloud.google.com/free> > "Get started for free". Enter your details and a payment method.
Choose **individual** account. Do not select upgrade at any point.

## Step 3. Create the project and protect yourself (10 minutes)

```powershell
gcloud projects create agentops-demo-<your-initials-123> --name="AgentOps"
gcloud config set project agentops-demo-<your-initials-123>
```

In the console: **Billing > link** the Free Trial billing account to this project. Then:

1. **Billing > Budgets & alerts > Create budget**: amount 1 USD, alerts at 50, 90 and 100 percent to your email.
   A budget alert warns you; it does not cap spend. The real safety is staying on the trial (no auto-charge).
2. Note the project id; every script below takes it as `-Project`.

## Step 4. Deploy to Cloud Run (15 minutes)

```powershell
cd C:\Users\IshaanZalani\Downloads\enterprise-agentops
.\deploy\cloudrun.ps1 -Project YOUR_PROJECT_ID -Public -Budget 100
```

What it does: checks that billing is linked, enables the APIs, creates a runtime service account with only
`aiplatform.user`, `logging.logWriter` and `cloudtrace.agent`, builds the Dockerfile with Cloud Build, deploys with
`--max-instances 1 --min-instances 0` (scales to zero, so idle time costs nothing), and prints the URL.
Your local `.env` is excluded from the upload by `.gcloudignore`. No API key exists in the cloud: Gemini and embeddings
use Vertex AI through the service account.

`-Budget 100` stops the app answering after 100 chat turns per UTC day, so a stranger cannot burn your credit.

If the build fails, the console shows Cloud Build logs: Cloud Build > History. Common causes: an API still
enabling (wait a minute and re-run), or Vertex AI not yet enabled for the project.

## Step 5. Verify it really runs in the cloud

```powershell
curl https://YOUR-URL/health
```

Expect `"model_mode": "gemini"`, `"backend": "vertex-ai"` and `"runtime": "cloud-run:agentops/agentops-0000N-xxx"`.
The runtime value comes from variables Cloud Run itself injects, so it cannot be faked by a local run. Then open the URL in
a browser, ask the refund question, try the denied-order scenario, and attach the sample photo.

## Step 6. Collect evidence

```powershell
python scripts\cloud_evidence.py https://YOUR-URL        # refuses to write unless /health says cloud-run
```

This writes `docs/deployment-evidence.md` from the live service's own responses. Also take: a screenshot of the Cloud Run
service page, Logs Explorer filtered on one `request_id` (shows trace correlation), and optionally run the eval on Vertex:

```powershell
$env:GOOGLE_GENAI_USE_VERTEXAI="true"; $env:GOOGLE_CLOUD_PROJECT="YOUR_PROJECT_ID"
gcloud auth application-default login
python evals\run_evals.py --mode gemini --pace 2
```

Commit the results file as it is, including any failures. Then fix the README scope note, which currently says
"not deployed", and add the URL. Do not remove the "synthetic data" statements.

## Step 7. Check spend, then turn it off when the job hunt moves on

- Billing > Reports: confirm credit use is small after your tests.
- `.\deploy\teardown.ps1 -Project YOUR_PROJECT_ID` deletes the service, its images and service account.
- `gcloud projects delete YOUR_PROJECT_ID` removes everything.

## Optional, only if you have access: Agent Engine and Gemini Enterprise

`.\deploy\agent_engine.ps1 -Project YOUR_PROJECT_ID` deploys the same agent to Vertex AI Agent Engine, then
register it in Gemini Enterprise: <https://docs.cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent>.
Gemini Enterprise is a paid product and a trial may not be available to you. If it is not, do not claim it; say
"the agent is packaged for Agent Engine and loads under ADK's server locally, I deployed it to Cloud Run".

Identity note: Agent Engine has no HTTP layer of ours, so the agent trusts the runtime's authenticated `user_id`
(`AGENTOPS_TRUST_RUNNER_USER_ID=1`) and the demo directory only knows `u-alice`, `u-bob`, `u-carol`, `u-dan`. Side effects
(tickets, calendar) can only be *proposed* there; executing them needs `POST /confirm` on the Cloud Run service.

## Optional: real Google Calendar write

1. Create a calendar "Support follow-ups" in a Google account you control.
2. Share it with `agentops-run@YOUR_PROJECT.iam.gserviceaccount.com`, permission **Make changes to events**.
3. Redeploy with `-CalendarId <calendar id>`. `/health` then shows `calendar: google-calendar`.

## Terraform alternative

`deploy/terraform/main.tf` expresses the Cloud Run side as code. Not validated by the author (Terraform was not installed):
run `terraform validate` and fix what it reports before claiming it.

## Interview-ready summary

"I deployed an ADK agent to Cloud Run, keyless on Vertex AI with a least-privilege service account, scale-to-zero,
a daily request cap, and Cloud Logging trace correlation. Sessions and pending confirmations are in memory, so I pinned it
to one instance and documented the move to Firestore or Agent Engine sessions for real scale."

## Deployment log

Fill this in with real outputs. Leave a line blank if you did not do it.

| Step | Date | Result / URL |
|---|---|---|
| Free Trial started | before 2026-11-04 | project `agentops-demo-iz7` |
| Cloud Run deploy | 2026-11-04 | `https://agentops-72n3irkweq-el.a.run.app` (region asia-south1, revision `agentops-00001-mwh`) |
| `/health` shows backend vertex-ai, runtime cloud-run | 2026-11-04 | yes, see `docs/deployment-evidence.md` |
| `cloud_evidence.py` run | 2026-11-04 17:41 UTC | first request took 48 s (cold start, not profiled) |
| Live eval on Vertex AI | | |
| Credit used after tests (Billing > Reports) | | |
