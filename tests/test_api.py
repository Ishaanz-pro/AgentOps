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
        AGENTOPS_ATTACHMENTS_DIR=str(tmp_path / "attachments"),
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
    assert redact("key AQ.Ab8Exampleexampleexample_123-xyz done") == "key [REDACTED] done"
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
    assert "AgentOps" in html and "Employee Assistant" in html
    # the only external hosts are Google Fonts; model/document text is never injected as HTML
    import re

    hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", html)) - {"www.w3.org"}
    assert hosts <= {"fonts.googleapis.com", "fonts.gstatic.com"}, hosts
    assert "innerHTML" not in html and "document.write" not in html and "eval(" not in html


def test_ui_does_not_embed_any_real_secret(client):
    html = client.get("/").text
    assert "AIza" not in html and "GOOGLE_API_KEY" not in html


# ---------------- multimodal: image attachments ----------------

def _png_b64() -> str:
    import base64

    from fixtures import cracked_band_png

    return base64.b64encode(cracked_band_png()).decode()


def test_image_ticket_journey_over_http(client, settings):
    body = client.post("/chat", json={
        "message": "Please raise a ticket: my headphones from ORD-1001 arrived with a cracked band, photo attached",
        "image_base64": _png_b64(), "image_mime_type": "image/png",
    }, headers=ALICE).json()
    assert body["status"] == "ok"
    pc = body["pending_confirmation"]
    assert pc["kind"] == "ticket" and pc["attachment"]["mime_type"] == "image/png"
    assert "photo" in body["answer"].lower()
    trace = client.get(f"/trace/{body['request_id']}", headers=ALICE).json()
    img = [s for s in trace["steps"] if s["name"] == "image_received"]
    assert img and img[0]["detail"]["sha256"] == pc["attachment"]["sha256"]
    assert "data" not in img[0]["detail"]  # bytes never enter the trace
    assert not (settings.attachments_dir).exists()  # nothing on disk before confirmation

    ok = client.post("/confirm", json={"confirmation_id": pc["confirmation_id"]}, headers=ALICE).json()
    assert ok["kind"] == "ticket" and ok["attachment_sha256"] == pc["attachment"]["sha256"]
    saved = list(settings.attachments_dir.iterdir())
    assert len(saved) == 1 and saved[0].name == pc["attachment"]["sha256"] + ".png"
    t = list_tickets(settings.db_path)
    assert len(t) == 1 and t[0].attachment_sha256 == pc["attachment"]["sha256"]


@pytest.mark.parametrize("payload, status", [
    ({"image_base64": _png_b64(), "image_mime_type": "image/jpeg"}, 422),      # lies about the type
    ({"image_base64": "PHN2Zz48L3N2Zz4=", "image_mime_type": "image/png"}, 422),  # <svg></svg>
    ({"image_base64": _png_b64()}, 422),                                        # mime missing
    ({"image_base64": _png_b64(), "image_mime_type": "image/svg+xml"}, 422),    # type not allowed
])
def test_image_validation_over_http(client, payload, status):
    r = client.post("/chat", json={"message": "look at this", **payload}, headers=ALICE)
    assert r.status_code == status


# ---------------- Google Workspace: calendar follow-up ----------------

def test_followup_journey_over_http(client, settings):
    from agentops.db import list_calendar_events

    body = client.post("/chat", json={
        "message": "Schedule a follow-up call about ORD-1001 on 2026-11-05 at 10:30",
    }, headers=ALICE).json()
    pc = body["pending_confirmation"]
    assert pc["kind"] == "calendar_event" and pc["start_at"] == "2026-11-05T10:30:00+05:30"
    assert "NOT scheduled" in body["answer"]
    assert list_calendar_events(settings.db_path) == []

    assert client.post("/confirm", json={"confirmation_id": pc["confirmation_id"]}, headers=BOB).status_code == 404
    ok = client.post("/confirm", json={"confirmation_id": pc["confirmation_id"]}, headers=ALICE)
    assert ok.status_code == 200
    ev = ok.json()
    assert ev["kind"] == "calendar_event" and ev["provider"] == "dryrun" and ev["event_id"].startswith("evt-dryrun-")
    assert ev["title"].startswith("Follow-up") and ev["ticket_id"] is None
    rows = list_calendar_events(settings.db_path)
    assert len(rows) == 1 and rows[0].created_by == "u-alice"
    assert client.post("/confirm", json={"confirmation_id": pc["confirmation_id"]}, headers=ALICE).status_code == 404


def test_health_reports_backend_and_calendar(client):
    h = client.get("/health").json()
    assert h["backend"] == "none" and h["calendar"] == "dryrun"


def test_health_reports_cloud_run_runtime(client, monkeypatch):
    assert client.get("/health").json()["runtime"] == "local"
    monkeypatch.setenv("K_SERVICE", "agentops")
    monkeypatch.setenv("K_REVISION", "agentops-00007-abc")
    assert client.get("/health").json()["runtime"] == "cloud-run:agentops/agentops-00007-abc"


def test_sample_image_route_is_allowlisted(client):
    r = client.get("/samples/cracked_band.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert client.get("/samples/..%2F..%2Fpyproject.toml").status_code == 404
    assert client.get("/samples/other.png").status_code == 404


def test_daily_chat_budget_guardrail(tmp_path, policy_dir):
    s = Settings(AGENTOPS_MODE="mock", AGENTOPS_EMBEDDINGS="local", AGENTOPS_DB_PATH=str(tmp_path / "b.db"),
                 AGENTOPS_POLICY_DIR=str(policy_dir), AGENTOPS_INDEX_DIR=str(tmp_path / "i"), GOOGLE_API_KEY=None,
                 AGENTOPS_DAILY_CHAT_BUDGET=2)
    with TestClient(create_app(s)) as c:
        assert c.post("/chat", json={"message": "hi"}, headers=ALICE).status_code == 200
        assert c.post("/chat", json={"message": "hi"}, headers=BOB).status_code == 200
        r = c.post("/chat", json={"message": "hi"}, headers=ALICE)
        assert r.status_code == 429 and "budget" in r.json()["detail"]
        assert c.get("/health").status_code == 200  # health is never budgeted
    deps_mod._deps = None
