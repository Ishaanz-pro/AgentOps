"""HTTP-level tests: auth, trace access control, confirmation flow, validation, log hygiene."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from agentops.api.main import create_app
from agentops.config import Settings
from agentops.db import list_tickets
from agentops.observability.logging import JsonFormatter, RedactionFilter, redact
from agentops.tools import deps as deps_mod

ALICE = {"Authorization": "Bearer tok-alice"}
BOB = {"Authorization": "Bearer tok-bob"}
DAN = {"Authorization": "Bearer tok-dan"}


@pytest.fixture
def settings(tmp_path, policy_dir) -> Settings:
    return Settings(
        AGENTOPS_MODE="mock",
        AGENTOPS_EMBEDDINGS="local",
        AGENTOPS_DB_PATH=str(tmp_path / "api.db"),
        AGENTOPS_POLICY_DIR=str(policy_dir),
        AGENTOPS_INDEX_DIR=str(tmp_path / "index"),
        GOOGLE_API_KEY=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c
    deps_mod._deps = None


def test_health_reports_real_mode(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert body["model_mode"] == "mock" and "offline" in body["model"]
    assert body["policy_chunks"] > 10


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}])
def test_chat_requires_valid_bearer_token(client, headers):
    r = client.post("/chat", json={"message": "hello"}, headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_core_journey_over_http(client):
    r = client.post(
        "/chat",
        json={"message": "Can order ORD-1001 be refunded, and what is its current delivery status?"},
        headers=ALICE,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "eligible" in body["answer"] and "delivered" in body["answer"].lower()
    assert any(s.startswith("POL-001") for s in body["sources"])
    assert r.headers["x-request-id"] == body["request_id"]


def test_trace_contains_full_execution_record(client):
    rid = client.post("/chat", json={"message": "Can order ORD-1001 be refunded?"}, headers=ALICE).json()["request_id"]
    t = client.get(f"/trace/{rid}", headers=ALICE).json()
    assert t["request_id"] == rid and t["trace_id"].startswith("trc-")
    assert t["status"] == "ok" and t["latency_ms"] > 0
    assert {c["tool"] for c in t["tool_calls"]} == {"search_policy_documents", "get_order_status"}
    assert t["retrieval_sources"]
    kinds = {s["kind"] for s in t["steps"]}
    assert {"retrieval", "authz", "agent"} <= kinds


def test_other_users_cannot_read_a_trace_but_admin_can(client):
    rid = client.post("/chat", json={"message": "Can order ORD-1001 be refunded?"}, headers=ALICE).json()["request_id"]
    assert client.get(f"/trace/{rid}", headers=BOB).status_code == 404
    assert client.get(f"/trace/{rid}", headers=DAN).status_code == 200
    assert client.get(f"/trace/{rid}").status_code == 401
    assert client.get("/trace/req-doesnotexist", headers=ALICE).status_code == 404


def test_cross_user_access_over_http(client):
    body = client.post("/chat", json={"message": "What is the status of ORD-2001?"}, headers=ALICE).json()
    assert "docking" not in body["answer"].lower()
    assert "permitted to view" in body["answer"]


def test_ticket_requires_explicit_confirmation(client, settings):
    body = client.post(
        "/chat", json={"message": "Please raise a ticket: my headphones arrived with a cracked band"}, headers=ALICE
    ).json()
    cid = body["pending_confirmation"]["confirmation_id"]
    assert list_tickets(settings.db_path) == []

    # another user cannot confirm it
    assert client.post("/confirm", json={"confirmation_id": cid}, headers=BOB).status_code == 404
    assert list_tickets(settings.db_path) == []

    ok = client.post("/confirm", json={"confirmation_id": cid}, headers=ALICE)
    assert ok.status_code == 200 and ok.json()["ticket_id"].startswith("TCK-")
    assert len(list_tickets(settings.db_path)) == 1

    # single use
    assert client.post("/confirm", json={"confirmation_id": cid}, headers=ALICE).status_code == 404


def test_confirm_requires_auth_and_valid_shape(client):
    assert client.post("/confirm", json={"confirmation_id": "cfm-abcd"}).status_code == 401
    assert client.post("/confirm", json={"confirmation_id": "../../etc"}, headers=ALICE).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [{}, {"message": ""}, {"message": "x" * 2001}, {"message": "hi", "session_id": "bad id!"}, {"message": 123}],
)
def test_input_validation(client, payload):
    assert client.post("/chat", json=payload, headers=ALICE).status_code == 422


def test_sensitive_values_never_reach_logs(client, capsys):
    secret_msg = "my personal note: please look at ORD-1001 refund eligibility SECRET-PHRASE-42"
    client.post("/chat", json={"message": secret_msg}, headers=ALICE)
    out = capsys.readouterr().out
    assert out.strip(), "expected structured logs on stdout"
    assert "tok-alice" not in out
    assert "SECRET-PHRASE-42" not in out  # raw user message is never logged
    for line in out.strip().splitlines():
        rec = json.loads(line)  # every line is valid JSON
        assert "event" in rec
    events = {json.loads(l)["event"] for l in out.strip().splitlines()}
    assert {"request_received", "tool_invoked", "request_completed"} <= events


def test_redaction_helpers():
    assert redact("key AIzaSyA1234567890abcdefghijklmnop done") == "key [REDACTED] done"
    assert redact("Authorization: Bearer abc.def-123") == "Authorization: [REDACTED]"
    assert redact({"t": "tok-bob", "n": ["tok-dan"]}) == {"t": "[REDACTED]", "n": ["[REDACTED]"]}

    rec = logging.LogRecord("x", logging.INFO, "", 0, "using tok-alice", (), None)
    rec.extra_field = "AIzaSyA1234567890abcdefghijklmnop"
    assert RedactionFilter().filter(rec)
    line = JsonFormatter().format(rec)
    assert "tok-alice" not in line and "AIza" not in line


def test_gemini_mode_without_key_fails_fast(tmp_path, policy_dir):
    s = Settings(AGENTOPS_MODE="gemini", AGENTOPS_EMBEDDINGS="local", AGENTOPS_DB_PATH=str(tmp_path / "x.db"),
                 AGENTOPS_POLICY_DIR=str(policy_dir), AGENTOPS_INDEX_DIR=str(tmp_path / "i"), GOOGLE_API_KEY=None)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        with TestClient(create_app(s)):
            pass


def test_ui_page_is_served_with_strict_csp(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    html = r.text
    assert "Enterprise AgentOps" in html
    # no external resources, and model/document text is never injected as HTML
    assert "http://" not in html and "https://" not in html
    assert "innerHTML" not in html and "document.write" not in html and "eval(" not in html


def test_ui_does_not_embed_any_real_secret(client):
    html = client.get("/").text
    assert "AIza" not in html and "GOOGLE_API_KEY" not in html
