# Architecture

## Request flow

```mermaid
flowchart TD
    C[Client<br/>Bearer token] -->|POST /chat| API[FastAPI<br/>api/main.py]
    API -->|authenticate token| AUTH[auth.py<br/>token -> Principal]
    AUTH -->|Principal| SVC[AgentService<br/>agent/runner.py]
    SVC -->|create session<br/>state: principal_user_id| SES[(ADK session state<br/>set by backend only)]
    SVC -->|run_async| AG[ADK LlmAgent<br/>agent/agent.py]
    AG <-->|prompt + tool calls| LLM{{Gemini API<br/>or offline MockLlm}}
    AG -->|before_tool_callback<br/>allowlist| GUARD[ALLOWED_TOOLS]
    GUARD --> T1[search_policy_documents]
    GUARD --> T2[get_order_status]
    GUARD --> T3[create_support_ticket<br/>PROPOSE only]
    T1 --> IDX[(FAISS index<br/>30 policy chunks)]
    IDX -.->|UNTRUSTED passages<br/>wrapped + flagged| AG
    T2 -->|principal from session state<br/>NOT from model args| AUTHZ[authz.py<br/>can_access_order]
    AUTHZ -->|allowed| DB[(SQLite orders)]
    AUTHZ -->|denied: generic error| AG
    T3 --> PEND[ConfirmationStore<br/>single-use, TTL, user-bound]
    SVC --> TR[Trace + JSON logs<br/>request_id, trace_id, steps, latency]
    SVC --> CIT[citation verifier]
    SVC -->|answer, sources| C
    C -->|POST /confirm<br/>same user| API
    API -->|confirm_and_create| PEND
    PEND -->|human-confirmed| DB2[(SQLite tickets)]
```

## Trust boundaries

| Boundary | Trusted? | Why |
|---|---|---|
| Bearer token -> `Principal` | Yes, backend | Resolved in `auth.py` before the agent runs |
| ADK session state `principal_user_id` | Yes | Written only by `AgentService._ensure_session` |
| Tool arguments (`order_id`, `query`, `issue`) | **No** | Model-chosen; validated with pydantic, then authorized |
| Model output | **No** | Can be wrong or manipulated; cannot widen privileges |
| Retrieved policy text | **No** | May contain injected instructions; wrapped, flagged, treated as data |
| Tool results | Yes (shape) | Uniform `{ok, data|error}`; internals never leaked |

## The key design decision

The model never supplies *who* it is acting for. `get_order_status(order_id, authenticated_user)` takes
`authenticated_user` from backend-controlled session state (`resolve_principal(tool_context)`), and
`can_access_order()` runs inside the tool on every call. A fully prompt-injected model can ask for any
order it likes; the backend still refuses. `tests/test_agent.py` proves this with a *compromised* model
that obeys the injected text.

## Module map

| Module | Responsibility |
|---|---|
| `config.py` | Env settings; API key is a `SecretStr` |
| `auth.py` / `authz.py` | Token -> Principal; pure authorization rules |
| `db.py` / `seed_data.py` | SQLite orders + tickets; synthetic data |
| `rag/` | Section chunker, embedders (Gemini / offline hash), FAISS index with relevance floor |
| `tools/registry.py` | Allowlist, principal resolution, `run_guarded` (validation, timeout, error mapping) |
| `tools/policy_search.py` | Retrieval tool; wraps passages as untrusted, flags injection-like text |
| `tools/order_status.py` | Authorized order lookup; deny == not-found (no enumeration) |
| `tools/ticket_tool.py` / `support_ticket.py` | Two-phase ticket: propose (model) -> confirm (human via API) |
| `agent/agent.py` | ADK `LlmAgent`, instructions, call-time allowlist guard |
| `agent/runner.py` | Per-turn orchestration, session scoping, trace, citation check |
| `agent/citations.py` | Flags cited policy sections that were not retrieved |
| `agent/mock_llm.py` | Deterministic offline model (+ "compromised" mode for security tests) |
| `observability/` | Trace objects/store; JSON logs with redaction |
| `api/` | FastAPI app, schemas |
