# Deploying on Google Cloud

Status: **scripts written, nothing deployed yet.** Everything below is a procedure you run; none of it has
been executed by the author of this repo. Record what you actually ran and saw in the "Deployment log" at
the bottom, and only then update the README's scope note.

## 0. One-time setup (about 20 minutes)

1. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install) and run `gcloud auth login`.
2. Create a project and **link a billing account** (console: Billing). A card is required. New accounts get
   free credits; check your current balance before relying on it.
3. Set a **budget alert** first: Billing > Budgets & alerts > create a 10 USD budget with email alerts at 50 / 90 / 100 %.
   This does not cap spend, it warns you.
4. `gcloud auth application-default login` (only needed for local runs against Vertex AI).

## 1. Cloud Run (live URL for the resume)

```powershell
.\deploy\cloudrun.ps1 -Project YOUR_PROJECT -Public -Budget 200
```

The script enables APIs, creates the `agentops-run` service account with only `aiplatform.user`,
`logging.logWriter` and `cloudtrace.agent`, builds the Dockerfile with Cloud Build, and deploys with
`--max-instances 1`. No API key exists anywhere: Gemini and embeddings run on Vertex AI as the service account.

Check it:

```powershell
curl https://YOUR-URL/health        # expect model_mode gemini, backend vertex-ai, embeddings gemini
```

Cost shape (verify against current pricing, these are not quotes): Cloud Run scales to zero; the main cost
is Gemini tokens. `-Budget 200` limits the demo to 200 chat turns per UTC day so a public link cannot run up a bill.
Delete when done: `gcloud run services delete agentops --region asia-south1`.

**Public link caveat.** The UI ships demo bearer tokens (`tok-alice`...) that anyone can see. That is fine
for synthetic data and is the reason for the daily budget. Do not put real data behind it.

## 2. Vertex AI Agent Engine and Gemini Enterprise

```powershell
.\deploy\agent_engine.ps1 -Project YOUR_PROJECT
```

This runs `adk deploy agent_engine` on `deploy/agent_engine/employee_assistant`, staging `src/agentops` and
`data/` into the runtime. It prints a `reasoningEngines/...` resource name. Then register the agent in Gemini Enterprise:
<https://docs.cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent>

Requirements you must check yourself: a Gemini Enterprise app (a trial may be available in your region/organisation;
this is a paid product and eligibility varies) and permission to administer it.

### Identity in Gemini Enterprise

Agent Engine has no FastAPI layer, so the agent reads the runtime's `user_id`
(`AGENTOPS_TRUST_RUNNER_USER_ID=1`). The demo directory only knows `u-alice`, `u-bob`, `u-carol`, `u-dan`.
Any other caller is rejected as unauthenticated. For a real customer you would replace the static directory with
a lookup from the verified IAM principal (email) to an employee record. That is a deliberate gap, listed in Limitations.

### Side effects in this deployment

The Agent Engine runtime can only **propose** tickets and calendar events. Executing them needs `POST /confirm`
on the Cloud Run service. Say this plainly in an interview: the human-confirmation step lives in the application
tier, and Gemini Enterprise would surface the proposal text rather than perform the write.

## 3. Google Calendar (optional, for the Workspace claim)

1. Create a calendar "Support follow-ups" in a Google account you control.
2. Share it with `agentops-run@YOUR_PROJECT.iam.gserviceaccount.com`, permission **Make changes to events**.
3. Redeploy: `.\deploy\cloudrun.ps1 -Project YOUR_PROJECT -Public -CalendarId <calendar id>`.
4. `/health` should now report `calendar: google-calendar`. Confirm a follow-up in the UI and check the event appears.

Run-time scope is `calendar.events` only. If the write returns 403 the UI says nothing was scheduled and no audit row is written.

## 4. Terraform alternative

`deploy/terraform/main.tf` expresses the Cloud Run side as code. Build and push the image first
(`gcloud builds submit --tag ...`), then `terraform init && terraform validate && terraform apply`.
Not validated by the author (Terraform was not installed). Fix whatever `terraform validate` reports and say so.

## 5. Evidence to collect (this is what makes it credible)

- Screenshot of `/health` on the live URL.
- Logs Explorer filtered by one `request_id`, showing `logging.googleapis.com/trace` correlation.
- Run the eval against the deployed model: `$env:GOOGLE_GENAI_USE_VERTEXAI="true"; $env:GOOGLE_CLOUD_PROJECT="YOUR_PROJECT"; python evals/run_evals.py --mode gemini`
  and commit the results file as it is, including failures.
- The Agent Engine resource name and a screenshot of the agent inside Gemini Enterprise.

## Deployment log

Fill this in with real outputs. Leave a line blank if you did not do it.

| Step | Date | Result / URL |
|---|---|---|
| Cloud Run deploy | | |
| `/health` backend=vertex-ai | | |
| Live eval (Vertex AI) | | |
| Agent Engine deploy | | |
| Gemini Enterprise registration | | |
| Real Calendar write | | |
