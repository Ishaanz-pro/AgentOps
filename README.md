# Enterprise AgentOps

An enterprise employee assistant built on **Google Agent Development Kit (ADK)** and the **Gemini API**.
It answers policy questions with RAG over internal documents, looks up orders through **authorized tools**,
and proposes support tickets and **Google Calendar follow-ups** that need **explicit human confirmation**. It also
accepts **photos** (multimodal): a damage photo is described by Gemini and bound to the proposed ticket by the
backend. The focus is not the chatbot; it is
what surrounds it: backend-enforced authorization, prompt-injection resistance, evaluation, and observability.

> **Scope and honesty.** This is a portfolio project on **synthetic data**. It is not production-ready.
> It **was deployed to Cloud Run on 2026-11-04** (Vertex AI, keyless, one instance, scale to zero, daily request cap),
> on a Free Trial project that will stop working when the trial ends. Evidence captured from the live service:
> [docs/deployment-evidence.md](docs/deployment-evidence.md). It has **not** been registered in Gemini Enterprise, and
> the Agent Engine package, Terraform and real Calendar writes have **not** been run. The 25-case eval has not been
> run against the deployment (see [Deploying on Google Cloud](#deploying-on-google-cloud) and
> [What was and wasn't verified](#what-was-and-wasnt-verified)).

## The core journey

An authenticated employee asks: *"Can order ORD-1001 be refunded, and what is its current delivery status?"*
The service (real output from the offline mode, `scripts/demo.py`):

```
A: Order ORD-1001 (Noise-cancelling headphones) is delivered; delivered 2026-10-24 (10 days ago) via BlueDart (order record).
   Refund: eligible, it is within the 30-day window from delivery (POL-001 §1. Eligibility window).
sources: ['POL-001 §1. Eligibility window', 'POL-001 §4. Refund method and timing', 'POL-001 §6. How to request']
  model asked for tool: get_order_status
  authz   order=ORD-1001 allowed=True reason='order owner'
  model asked for tool: search_policy_documents
  retrieve POL-001#1(0.5419), POL-001#4(0.291), POL-004#4(0.2469), POL-001#6(0.2202)
```

1. The agent looks up the order, and the **backend** checks that the authenticated user may see it.
2. It retrieves the applicable refund-policy section (FAISS).
3. It combines both and answers with source references; every cited section is checked against what was actually retrieved.
4. The request, retrieval, tool calls, authorization decisions, latency, and errors are all in a trace (`GET /trace/{request_id}`).

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full diagram, trust boundaries, and module map.

```mermaid
flowchart LR
    C[Client + Bearer token] --> API[FastAPI]
    API --> AUTH[auth: token to Principal]
    AUTH --> SVC[AgentService]
    SVC -->|principal in session state| AG[ADK LlmAgent]
    AG <--> LLM{{Gemini / offline MockLlm}}
    AG --> T1[search_policy_documents] --> IDX[(FAISS)]
    AG --> T2[get_order_status] --> AZ[authz.can_access_order] --> DB[(SQLite)]
    AG --> T3[create_support_ticket: propose only]
    AG --> T4[schedule_followup: propose only]
    C -->|POST /confirm human click| API --> DB
    API --> CAL[Google Calendar API]
    SVC --> OBS[trace + JSON logs + citation check]
```

**The key decision:** the model never says *who* it is acting for. The authenticated user is written into ADK
session state by the backend from the verified bearer token; tools read it from there, and
`authz.can_access_order()` runs inside the tool on every call. A fully compromised model can request any
order; the backend still refuses.

## Quick start

Requires Python 3.11+ (developed and tested on 3.13.15, Windows 11).

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"

pytest                            # 137 tests, fully offline
python evals/run_evals.py         # 25-case evaluation, offline

python -m uvicorn agentops.api.main:app_factory --factory --port 8765
python scripts/demo.py --base http://127.0.0.1:8765
```

With no `GOOGLE_API_KEY` the service runs in **offline mock mode** (deterministic `MockLlm` + local hashed
embeddings) and `GET /health` says so (`"model_mode": "mock"`). To use real Gemini:

```bash
cp .env.example .env              # then set GOOGLE_API_KEY (from https://aistudio.google.com/apikey)
python -m uvicorn agentops.api.main:app_factory --factory --port 8765   # /health now reports model_mode: gemini
python evals/run_evals.py --mode gemini
```

Docker (`Dockerfile`, `docker-compose.yml`) is provided but was **not built or run** where this was authored.

## New capabilities (2026-11-04)

| Capability | What it does | Trust boundary |
|---|---|---|
| **Multimodal input** | Attach a PNG/JPEG/WebP to a chat turn. Gemini sees it; the agent describes damage in the ticket text. | Type sniffed from bytes (declared MIME must match), 4 MB cap, content-addressed by sha256, bytes never enter traces/logs, written to disk only after a human confirms the ticket. Text inside an image is treated as untrusted data. |
| **Google Workspace (Calendar)** | `schedule_followup` proposes an event; `POST /confirm` writes it through the Calendar API (or a dry-run audit log). | Same two-phase gate as tickets: user-bound, single-use, 10-min TTL. Requester identity is stamped by the backend. The service writes to one support calendar as itself and never holds a user's OAuth token. Confirmation kinds cannot be crossed. |
| **Keyless Vertex AI backend** | `GOOGLE_GENAI_USE_VERTEXAI=true` + project: Gemini and embeddings through the runtime service account. | `/health` reports `backend`. A stray API key is removed from the environment so traffic cannot silently leave Vertex. |
| **Agent Engine package** | `deploy/agent_engine/employee_assistant`: the same agent, tools and RAG, deployable with `adk deploy agent_engine` and registrable in Gemini Enterprise. | Runtime user id is trusted only when `AGENTOPS_TRUST_RUNNER_USER_ID=1`. |
| **Cloud-native logs** | JSON logs carry `severity`, `message` and `logging.googleapis.com/trace`. | Redaction unchanged. |
| **Cost guardrail** | `AGENTOPS_DAILY_CHAT_BUDGET` caps chat turns per UTC day (HTTP 429 after). | For a public demo URL. Process-local, so Cloud Run max-instances is 1. |

The compromised-model test now also makes the model try an injected calendar write: both injected proposals are
surfaced to the human and nothing is written. Screenshots: [photo ticket](docs/screenshots/photo-ticket-proposed.png),
[calendar follow-up](docs/screenshots/calendar-followup-proposed.png), [phone](docs/screenshots/mobile-photo-ticket.png).

## Deploying on Google Cloud

Scripts are written and the Agent Engine package loads under ADK's own `api_server` locally, but **no cloud
deployment has been run**. Step-by-step with costs and the Gemini Enterprise registration: [`docs/deploy.md`](docs/deploy.md).

```powershell
.\deploy\cloudrun.ps1 -Project YOUR_PROJECT -Public -Budget 200     # Cloud Run, keyless Vertex AI
.\deploy\agent_engine.ps1 -Project YOUR_PROJECT                     # Vertex AI Agent Engine
```

Zero-cost walkthrough (Free Trial, teardown, evidence): see `docs/deploy.md`. Terraform equivalent: `deploy/terraform/main.tf` (not applied or validated; Terraform was not installed).

### Web UI

Start the server (command above) and open **http://127.0.0.1:8765/**. A single page served by FastAPI, no build
step, built to Material 3 conventions rather than a chatbot template:

- **Top app bar** with product name, a live/mock status chip (so mock answers can never pass as Gemini), new chat,
  theme (system / light / dark), and a demo-user menu (Alice, Bob, Carol the support agent, Dan the admin).
- **Conversation**: greeting, suggestion pills, and a "Security scenarios" list (someone else's order, poisoned
  document, hostile message, ticket, out-of-scope). Answers render Gemini's markdown (headings, lists, tables,
  code) through a small sanitising renderer that builds DOM nodes; no HTML is ever parsed from model or document
  text. Each answer shows latency, tool-call count, citation chips, and a **Citations verified** mark (or the
  unverified ones).
- **Inspector** panel with Timeline / Tools / Raw tabs: status, request and trace IDs, then every step: model tool
  requests, retrieval hits with scores (flagged passages highlighted), **access granted / denied** with the
  reason, untrusted-content warnings, tool outcomes. On phones it opens as a sheet from "View trace".
- **Ticket proposals** appear as a card with Confirm / Dismiss; nothing is filed until Confirm.
- Only external requests: Roboto and Roboto Mono from Google Fonts (falls back to system fonts). Strict CSP.

| Answer with inspector | Denied access + flagged injection |
|---|---|
| ![Answer with trace](docs/screenshots/answer-with-trace.png) | ![Denied and flagged](docs/screenshots/denied-and-flagged.png) |

| Ticket needs human confirmation | Phone: inspector sheet |
|---|---|
| ![Ticket confirmation](docs/screenshots/ticket-confirmation.png) | ![Mobile inspector](docs/screenshots/mobile-inspector.png) |

Screenshots were taken against the offline mock server (hence the "Offline mock model" chip) by driving the page
in headless Chrome over the DevTools protocol: empty state, answer with trace, denied access, flagged injection,
ticket card, a markdown-formatted answer ([sample](docs/screenshots/markdown-answer.png)), dark theme, and the
phone layout (390 px) including the inspector sheet.

### API

| Endpoint | Purpose |
|---|---|
| `POST /chat` `{message, session_id?, image_base64?, image_mime_type?}` | One agent turn, optionally with a photo. Needs `Authorization: Bearer <token>` |
| `POST /confirm` `{confirmation_id}` | Human confirmation that executes a proposed ticket or calendar event |
| `GET /trace/{request_id}` | Execution trace; owner or admin only |
| `GET /health` | Liveness + which model/embedding mode is really running |

Demo users (synthetic): `tok-alice` (employee, owns ORD-1001/1002/1003), `tok-bob` (employee, ORD-2xxx),
`tok-carol` (support agent, IN-WEST), `tok-dan` (admin).

```bash
curl -s localhost:8765/chat -H "Authorization: Bearer tok-alice" -H "Content-Type: application/json" \
  -d '{"message":"Can order ORD-1001 be refunded, and what is its current delivery status?"}'
```

## Security model

| Threat | Control | Where | Test |
|---|---|---|---|
| Employee reads another user's order | `can_access_order()` runs in the tool on every call; principal from backend session state, never a model argument | `authz.py`, `tools/order_status.py` | `test_cross_user_access_denied`, `AUTHZ-01..03` |
| Order-ID enumeration | Not-found and not-permitted return the identical error | `tools/order_status.py` | `test_denied_and_missing_are_indistinguishable` |
| Indirect prompt injection via a poisoned document | Retrieved text wrapped in `UNTRUSTED_DOCUMENT` markers, trust-labelled, injection-like text flagged and logged; prompt says treat as data; **and the backend does not rely on the model obeying** | `tools/policy_search.py`, `agent/agent.py` | `test_injection_does_not_leak_orders_or_create_tickets` (uses a *compromised* model that obeys the injection), `INJ-01..03` |
| Direct prompt injection | Same backend authorization | `tools/order_status.py` | `INJ-03` |
| Model creates a ticket or calendar event on its own | Tools only **propose**; execution requires `POST /confirm` by the same user: single-use, 10-minute TTL, bound to user; ticket and calendar confirmations cannot be crossed | `tools/support_ticket.py`, `tools/ticket_tool.py`, `tools/followup_tool.py` | `test_ticket_*`, `test_confirmation_*`, `test_followup_*`, `test_confirmation_kinds_cannot_be_crossed` |
| Malicious or mislabelled image upload | Magic-byte sniffing, declared type must match, PNG/JPEG/WebP only, 4 MB cap, bytes never traced, saved only after confirmation | `attachments.py` | `test_attachments.py`, `test_image_validation_over_http` |
| Hijacked model writes to a user's calendar | Service writes to one designated support calendar as itself; no user OAuth token exists in the process; identity stamped by backend | `workspace.py` | `test_followup_created_only_after_confirmation_and_attributed_by_backend` |
| Model calls a tool it shouldn't | Agent built from an explicit allowlist + `before_tool_callback` re-check | `agent/agent.py`, `tools/registry.py` | built-in |
| Malformed / hostile tool arguments | pydantic validation (e.g. `^ORD-\d{4,6}$`), parameterized SQL | `tools/*.py`, `db.py` | `test_malformed_order_ids` (SQL-injection strings) |
| Hung or failing backend | Per-tool timeout; DB errors mapped to a safe message; internals never returned to the model | `tools/registry.py` | `test_tool_timeout_is_enforced`, `test_database_unavailable`, `FAIL-03` |
| Fabricated policy | Relevance floor returns "no policy found" for out-of-scope questions; prompt rule 1; citation verifier flags cited sections that were never retrieved | `rag/index.py`, `agent/citations.py` | `test_relevance_floor_*`, `FAIL-04`, `test_citations.py` |
| Fabricated order data | Order facts only from the tool result; backend computes `days_since_delivery` | `tools/order_status.py` | `FAIL-02`, `FAIL-03` |
| Secrets in logs | API key is a `SecretStr`; redaction filter scrubs keys/tokens/`Bearer` headers; raw user messages are never logged (only length) | `config.py`, `observability/logging.py` | `test_sensitive_values_never_reach_logs` |
| Trace snooping | Traces visible only to their owner or an admin; others get 404 | `api/main.py` | `test_other_users_cannot_read_a_trace_but_admin_can` |

## Observability

Every request produces a `Trace` (kept in a bounded in-memory store) with: `request_id`, `trace_id`, user/role,
ordered steps (model tool requests, retrieval with chunk IDs/scores/trust/flags, authorization decisions with
reasons, flagged content, unverified citations), tool outcomes with codes, total latency, and errors. Logs are
JSON lines carrying the same IDs. Example: `scripts/demo.py` prints a trace summary for each scenario.

## Evaluation

`evals/dataset.jsonl` has 25 cases: 5 policy, 5 order, 3 unauthorized, 3 prompt-injection, 5 failure/missing-info, 3 workspace, 1 multimodal.
Each records the **expected behavior** in prose; `evals/run_evals.py` records the **actual behavior** and evaluates
deterministic checks (answer content, tools used, authorization decisions from the trace, no ticket in the DB,
retrieved chunk, citation integrity). No LLM-as-judge. Output: `evals/results/results_<mode>.jsonl` and
`summary_<mode>.md`, committed.

**Latest offline run: 25/25 passed** (policy 5/5, order 5/5, unauthorized 3/3, injection 3/3, failure 5/5, workspace 3/3,
multimodal 1/1; p50 ≈ 23 ms, p95 ≈ 41 ms in-process). The 4 newest cases have **not** been run on live Gemini. Injection cases run against a *compromised* mock that obeys injected text.

**How we got there (the failures are the point):**

| Run | Result | What it revealed | Fix |
|---|---|---|---|
| 1 | 14/21 | Security checks all held. Retrieval ranked the wrong section first; an out-of-scope question ("parental leave") still returned unrelated policy text; the mock appended policy snippets to order answers | Stopword-aware embedder; relevance floor; mock cleanup |
| 2 | 21/21 | Passed, but running the live demo showed the answer citing `POL-001 §1` although the trace proved §1 was **never retrieved** (the mock hard-coded the citation; a real model could do the same) | Added the citation-integrity verifier and made it an always-on eval check |
| 3 | 18/21 | The new check failed two cases on quoted text that legitimately cross-references another document (false positive), and one cancelled-order case whose needed section was not retrieved (a real retrieval gap) | Verifier accepts document-level cross-references found in retrieved text (section-level stays strict); the mock now looks up the order first, then searches for the policy that status needs (sequential tool use) |
| 4 | 21/21 | Final | n/a |

**What this does and does not prove.** The offline results validate the pipeline and the **backend security
guarantees** (they hold even for a model that fully obeys an injection). They say **nothing about Gemini's
answer quality**: the `MockLlm` is a deterministic stand-in. Quality numbers require `--mode gemini` with a
real key; see the live-run section below for what that produced and why it is incomplete.

## Live Gemini run (2026-11-04)

What happened the first time a real key was used, in order:

1. **`gemini-2.5-flash` is retired** (HTTP 404 "no longer available"). The default is now `gemini-3.5-flash`, which
   is also ADK 2.13's built-in default. The quota response labels it `gemini-3.6-flash` internally.
2. **Core journey on live Gemini passed.** Alice's "Can ORD-1001 be refunded, and what is its delivery status?":
   the agent called `get_order_status` (authorized: order owner), then `search_policy_documents`, answered
   *delivered 2026-10-24, 10 days ago, eligible within the 30-day window*, and cited `POL-001 §1` and `§2`.
   The citation verifier found nothing unretrieved. Latency: 49.6 s.
3. **Why 49.6 s:** roughly 1 in 4 requests to the model returned `503 UNAVAILABLE` ("high demand") and ADK's
   `Gemini` wrapper does not retry by default. Fix: `make_gemini_model()` now sets `retry_options` for
   429/5xx (4 attempts, exponential backoff). A plain model call took 11–18 s on its own; most of the latency is
   upstream.
4. **Gemini embeddings calibrated.** Top-1 cosine for 10 in-scope questions: 0.702–0.792; for 8 off-topic
   questions (weather, recipes, stocks, parental leave…): 0.514–0.584. Floor set to 0.65.
5. **Full eval blocked by quota.** `python evals/run_evals.py --mode gemini` scored **4/21**: the first three
   policy cases and one order case passed, then every remaining case failed with
   `429 RESOURCE_EXHAUSTED` — `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value **20**. The 17 failures
   are the quota, not wrong answers; `evals/results/results_gemini.jsonl` is that run, kept as-is. Fixes made in
   response: turns now report `rate_limited` / `model_unavailable` instead of a generic error, the eval records
   each case's error code, and `--pace N` sleeps between cases.

To get a complete live eval: enable billing on the Google Cloud project behind the key (pay-as-you-go), or run
a ~7-case subset per day on the free tier. Neither has been done yet.

## Test results

```
$ pytest
137 passed
```

| File | Tests | Covers |
|---|---|---|
| `test_tools.py` | 28 | cross-user access, enumeration, malformed args, timeout, DB down, injection flagging, confirmation gate |
| `test_api.py` | 30 | auth, trace access control, confirm flow, validation, log redaction, CSP, image upload over HTTP, calendar flow, daily budget, sample route |
| `test_rag.py` | 16 | chunking, determinism, retrieval, relevance floor in/out of scope, index persistence |
| `test_agent.py` | 12 | end-to-end through real ADK loop, compromised-model injection (ticket and calendar), session hijack |
| `test_workspace.py` | 12 | calendar propose/confirm, user binding, kind crossing, time validation, Google Calendar request mapping and errors (stubbed network) |
| `test_attachments.py` | 9 | magic-byte sniffing, MIME mismatch, size cap, content addressing |
| `test_authz.py` | 8 | authorization rules |
| `test_config.py` | 8 | API key handling, secret not in repr, keyless Vertex env |
| `test_citations.py` | 6 | citation provenance checker |
| `test_db.py` | 4 | SQLite layer |
| `test_evals.py` | 2 | dataset minimums; all 25 eval cases pass |
| `test_agent_engine_entry.py` | 2 | deploy entry point imports and builds `root_agent`; runtime-user trust is opt-in |

Environment: Python 3.13.15, Windows 11, `google-adk 2.13.0`, `google-genai 2.34.0`, `faiss-cpu 1.15.1`,
`fastapi 0.162.0`, `pydantic 2.14.2`, `pytest 9.2.0`.

## What was and wasn't verified

| Verified (ran here) | Not verified |
|---|---|
| Full test suite and eval suite (offline) | **The full 21-case eval on live Gemini**: blocked by the free tier's 20 requests/day/model quota (see [Live Gemini run](#live-gemini-run-2026-11-04)) |
| Real ADK agent loop, tool dispatch, callbacks, session state (via `MockLlm`) | Any quality claim about Gemini beyond the handful of live turns described below |
| Live `gemini-3.5-flash` through the real ADK agent: the core journey answered correctly with verified citations | — |
| Gemini embeddings (`gemini-embedding-001`) with a relevance floor calibrated on 18 queries | — |
| Live uvicorn server + `scripts/demo.py` over HTTP | Docker build and `docker compose up` (Docker not installed) |
| Photo and calendar flows in a real browser (headless Chrome, mock model) | Calendar flow and the 4 new eval cases on live Gemini |
| One live Gemini photo turn (2026-11-04, `gemini-3.5-flash`, AI Studio key): described the synthetic cracked headband correctly, proposed the ticket, backend bound the image (17 s) | More than that one turn; no accuracy claim on real product photos |
| Agent Engine package loads and serves under ADK `api_server` locally | `adk deploy agent_engine`, Terraform, Gemini Enterprise registration: **never run** |
| Cloud Run deployment (2026-11-04): Dockerfile built by Cloud Build, `/health` reports `vertex-ai` and `cloud-run:` runtime, one authorized and one denied request answered correctly ([evidence](docs/deployment-evidence.md)); one photo-ticket turn proposed a ticket and waited for confirmation (screenshots [refund answer](docs/screenshots/cloud-run-refund.jpg), [denied](docs/screenshots/cloud-run-denied.jpg), [photo ticket](docs/screenshots/cloud-run-photo-ticket-proposed.jpg); manual screenshots, not scripted) | The 25-case eval against the deployment, steady-state latency (first request took 48 s), `docker compose up`, any load or availability claim |
| Calendar client request mapping and error handling against a stubbed HTTP session | A real write to a real Google Calendar |
| Authorization, injection, confirmation, redaction behavior | Concurrency, load, any production deployment |

## Limitations (security and otherwise)

- **Demo authentication.** Static bearer tokens in code. Production needs a real IdP (OIDC/JWT validation, expiry, rotation).
- **Injection defences are layered, not absolute.** Marker-wrapping, regex flagging, and prompt rules are heuristics a determined attacker can evade. The load-bearing control is backend authorization, which limits blast radius but cannot stop a manipulated model from *saying* misleading things within what the user is allowed to see.
- **A hijacked model can still *propose* a ticket** (e.g. `AUTO-ESCALATE...`). It is never created without `POST /confirm`, and the proposed text is returned to the user, but a careless human could confirm it.
- **Citation verifier checks provenance, not truth.** A retrieved section can still be misquoted.
- **Relevance floors are calibrated on small samples**: ~12 queries for the local embedder (margin 0.19 vs 0.22) and 18 for Gemini embeddings (in-scope ≥ 0.70, out-of-scope ≤ 0.59, floor 0.65). Regression-tested, not statistically validated.
- **Free-tier quota**: 20 requests/day/model. A single agent turn uses 2–3 model requests, so the live demo supports roughly 7–10 turns a day without billing.
- **Local embedder is lexical** (hashed bag-of-words); it cannot handle true paraphrase. It exists for offline reproducibility.
- **In-memory state.** Sessions, traces, and pending confirmations are lost on restart and not shared across replicas. Cloud Run is therefore pinned to one instance. A real deployment needs Firestore or Redis for confirmations and Agent Engine sessions.
- **Agent Engine runtime has no HTTP layer of ours**, so it trusts the runtime-provided user id (opt-in). Mapping Gemini Enterprise identities to demo principals is a placeholder, not an IdP integration.
- **Images are not scanned** for malware or sensitive content beyond type, size and the model's own handling.
- **No rate limiting, no per-user quotas, no TLS termination, no audit-log immutability.**
- **Synthetic "today" is fixed** (2026-11-03) so evals are reproducible.
- **Small corpus**: 6 documents, 30 chunks; FAISS flat index is exact search, not an approximate/scale design.

## Demo script

`scripts/demo.py` runs: (1) a normal request, (2) an unauthorized request (with a control showing the owner succeeds),
(3a) indirect prompt injection via the poisoned community FAQ, (3b) a hostile user message, (4) the ticket
confirmation gate including wrong-user and replay attempts. Each prints the answer plus a trace summary
(retrieval, authorization decisions, tool outcomes). In mock mode, the *compromised-model* proof is in
`tests/test_agent.py` rather than the live demo.

## Layout

```
src/agentops/   config, auth, authz, db, attachments, workspace, seed_data, rag/, tools/, agent/, observability/, api/
data/           synthetic policies (6 docs), sample image, seed script
deploy/         cloudrun.ps1, agent_engine.ps1, agent_engine/ (ADK package), terraform/
evals/          dataset.jsonl, run_evals.py, fixtures.py, results/
tests/          136 offline tests
scripts/demo.py demo against a running server
docs/           architecture.md, deploy.md, blog-security-model.md
```
