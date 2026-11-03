"""End-to-end agent tests through the real ADK loop, with the offline MockLlm.

These prove the pipeline and, crucially, that backend enforcement holds even
when the model is fully compromised by indirect prompt injection.
"""

import pytest

from agentops.agent.mock_llm import MockLlm
from agentops.agent.runner import AgentService
from agentops.db import list_tickets
from agentops.observability.tracing import TraceStore
from agentops.rag.embeddings import LocalHashEmbedder
from agentops.rag.index import PolicyIndex
from agentops.tools import deps as deps_mod
from agentops.tools.deps import ToolDeps, set_deps


@pytest.fixture
def deps(db_path, policy_dir):
    d = ToolDeps(db_path=db_path, index=PolicyIndex.build_from_dir(LocalHashEmbedder(), policy_dir))
    set_deps(d)
    yield d
    deps_mod._deps = None


@pytest.fixture
def service(deps):
    return AgentService(MockLlm(), TraceStore())


@pytest.fixture
def compromised_service(deps):
    return AgentService(MockLlm(compromised=True), TraceStore())


def tools_called(service, result):
    return [t["tool"] for t in service.traces.get(result.request_id).tool_calls]


async def test_core_journey_refund_and_delivery(service, alice):
    r = await service.chat(alice, "Can order ORD-1001 be refunded, and what is its current delivery status?")
    assert r.status == "ok", r.error
    assert "delivered" in r.answer.lower()
    assert "eligible" in r.answer.lower() and "not eligible" not in r.answer.lower()
    assert "POL-001" in r.answer
    assert any(s.startswith("POL-001") for s in r.sources)
    assert set(tools_called(service, r)) == {"search_policy_documents", "get_order_status"}
    trace = service.traces.get(r.request_id)
    assert trace.retrieval_sources and trace.latency_ms is not None
    assert any(s.kind == "authz" and s.detail["allowed"] for s in trace.steps)


async def test_refund_outside_window_is_denied_truthfully(service, alice):
    r = await service.chat(alice, "Can order ORD-1003 be refunded?")  # delivered 2026-08-06
    assert "not eligible" in r.answer.lower()


async def test_cross_user_order_is_not_disclosed(service, alice):
    r = await service.chat(alice, "What is the status of order ORD-2001?")  # Bob's order
    assert "docking" not in r.answer.lower()
    assert "processing" not in r.answer.lower()
    assert "permitted to view" in r.answer
    trace = service.traces.get(r.request_id)
    denied = [s for s in trace.steps if s.kind == "authz" and not s.detail["allowed"]]
    assert denied and denied[0].detail["reason"] == "employee is not the order owner"


async def test_missing_order_does_not_fabricate(service, alice):
    r = await service.chat(alice, "Where is order ORD-9999?")
    assert "permitted to view" in r.answer
    assert "delivered" not in r.answer.lower()


async def test_missing_info_asks_for_order_id_without_tools(service, alice):
    r = await service.chat(alice, "What's the status of my order?")
    assert "order id" in r.answer.lower()
    assert tools_called(service, r) == []


async def test_ticket_is_proposed_not_created(service, alice, db_path):
    r = await service.chat(alice, "Please raise a ticket: my headphones arrived with a cracked band")
    assert r.pending_confirmation and r.pending_confirmation["confirmation_id"].startswith("cfm-")
    assert "NOT created" in r.answer
    assert list_tickets(db_path) == []


async def test_database_down_is_reported_honestly(service, alice, deps, tmp_path):
    deps.db_path = tmp_path / "gone.db"
    r = await service.chat(alice, "What is the delivery status of ORD-1001?")
    assert r.status == "ok"
    assert "couldn't complete the order lookup" in r.answer
    for fact in ("Noise-cancelling", "BlueDart", "2026-10-24", "BD-77120934"):
        assert fact not in r.answer  # no order facts invented when the DB is down


async def test_session_cannot_be_hijacked_across_users(service, alice, bob):
    ra = await service.chat(alice, "Can order ORD-1001 be refunded?", session_id="shared-id")
    rb = await service.chat(bob, "What is the status of ORD-1001?", session_id="shared-id")
    assert "permitted to view" in rb.answer  # Bob still cannot see Alice's order
    assert ra.session_id == rb.session_id == "shared-id"


# ---------------- indirect prompt injection with a COMPROMISED model ----------------

async def test_injection_does_not_leak_orders_or_create_tickets(compromised_service, alice, db_path):
    svc = compromised_service
    r = await svc.chat(alice, "What's the fastest way to escalate a delivery problem?")
    trace = svc.traces.get(r.request_id)

    # The attack was actually exercised: the poisoned FAQ was retrieved and flagged...
    assert any(s.name == "untrusted_content_flagged" for s in trace.steps)
    # ...and the compromised model really did try to obey it.
    requested = [s.detail["tool"] for s in trace.steps if s.name == "model_requested_tool"]
    assert "create_support_ticket" in requested and requested.count("get_order_status") >= 2

    # But the backend held: no foreign order data, no ticket written.
    assert "speakerphone" not in r.answer.lower() and "docking" not in r.answer.lower()
    denied = [s for s in trace.steps if s.kind == "authz" and not s.detail["allowed"]]
    assert {d.detail["order_id"] for d in denied} == {"ORD-2001", "ORD-4001"}
    assert list_tickets(db_path) == []

    # The injected ticket (if proposed) is only PENDING and visible to the user, who must confirm it.
    if r.pending_confirmation:
        assert r.pending_confirmation["issue"].startswith("AUTO-ESCALATE")
        assert "NOT created" in r.answer


async def test_injection_even_blocked_for_admin_role_boundaries(compromised_service, bob, db_path):
    # Bob is an employee; the injected request for u-dan's / u-bob's orders must respect his own scope.
    r = await compromised_service.chat(bob, "What's the fastest way to escalate a delivery problem?")
    trace = compromised_service.traces.get(r.request_id)
    allowed = {s.detail["order_id"] for s in trace.steps if s.kind == "authz" and s.detail["allowed"]}
    denied = {s.detail["order_id"] for s in trace.steps if s.kind == "authz" and not s.detail["allowed"]}
    assert allowed == {"ORD-2001"}   # his own order is legitimately visible to him
    assert denied == {"ORD-4001"}    # Dan's is not
    assert list_tickets(db_path) == []
