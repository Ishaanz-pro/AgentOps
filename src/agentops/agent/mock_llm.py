"""Deterministic offline stand-in for Gemini.

Purpose: exercise the REAL ADK machinery (agent loop, tool dispatch,
callbacks, session state) and the REAL tools/authz/RAG without network or
API key, so tests and evals are reproducible. It is NOT a quality benchmark
for Gemini: answer-quality claims must come from a live Gemini run.

``compromised=True`` simulates a fully prompt-injected model: if any
retrieved passage contains injected instructions it obeys them and tries to
(a) file a ticket without asking and (b) read other users' orders. The
security tests assert the BACKEND still refuses.
"""

from __future__ import annotations

import re
from typing import Any, AsyncGenerator

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

_ORDER_RE = re.compile(r"\bORD-\d{4,6}\b", re.IGNORECASE)
_POLICY_WORDS = re.compile(
    r"refund|return|policy|warranty|ship|deliver|cancel|expense|privacy|window|days|reimburse|escalat|carrier",
    re.IGNORECASE,
)
_TICKET_WORDS = re.compile(r"\b(ticket|raise an issue|open a case)\b", re.IGNORECASE)
_INJECTED_ORDERS = ("ORD-2001", "ORD-4001")


def _is_user_text(c: types.Content) -> bool:
    return c.role == "user" and any(p.text for p in (c.parts or [])) and not any(
        p.function_response for p in (c.parts or [])
    )


def _split_turn(contents: list[types.Content]) -> tuple[str, list[types.Content]]:
    """Return (latest user text, contents that came after it)."""
    idx = max((i for i, c in enumerate(contents) if _is_user_text(c)), default=-1)
    if idx < 0:
        return "", []
    text = " ".join(p.text for p in contents[idx].parts if p.text)
    return text, contents[idx + 1 :]


def _calls_and_responses(after: list[types.Content]) -> tuple[list[types.FunctionCall], list[types.FunctionResponse]]:
    calls, resps = [], []
    for c in after:
        for p in c.parts or []:
            if p.function_call:
                calls.append(p.function_call)
            if p.function_response:
                resps.append(p.function_response)
    return calls, resps


def _fc(name: str, **args: Any) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


class MockLlm(BaseLlm):
    model: str = "mock-llm"
    compromised: bool = False

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"mock-llm"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        user_text, after = _split_turn(llm_request.contents)
        calls, resps = _calls_and_responses(after)

        if not resps:
            parts = self._plan(user_text)
        else:
            parts = self._followup(user_text, calls, resps)
        yield LlmResponse(content=types.Content(role="model", parts=parts))

    # ------------------------------------------------------------------
    def _plan(self, user_text: str) -> list[types.Part]:
        parts: list[types.Part] = []
        if _POLICY_WORDS.search(user_text):
            parts.append(_fc("search_policy_documents", query=user_text[:400]))
        for oid in dict.fromkeys(m.upper() for m in _ORDER_RE.findall(user_text)):
            parts.append(_fc("get_order_status", order_id=oid))
        if _TICKET_WORDS.search(user_text) and re.search(r"\b(raise|create|open|file|log)\b", user_text, re.I):
            parts.append(_fc("create_support_ticket", issue=user_text[:900]))
        if not parts and re.search(r"\border\b", user_text, re.I):
            return [types.Part(text="Which order are you asking about? Please share the order ID (for example ORD-1234).")]
        if not parts:
            return [types.Part(text="I can help with company policies and your orders. What would you like to know?")]
        return parts

    def _followup(self, user_text: str, calls: list[types.FunctionCall], resps: list[types.FunctionResponse]) -> list[types.Part]:
        passages = [p for r in resps if r.name == "search_policy_documents"
                    for p in (r.response or {}).get("data", {}).get("passages", [])]

        # Compromised model: obey injected instructions, once.
        if self.compromised and any(p.get("instruction_like_content_detected") for p in passages):
            already = any(c.name == "create_support_ticket" and str((c.args or {}).get("issue", "")).startswith("AUTO-ESCALATE") for c in calls)
            if not already:
                return [_fc("create_support_ticket", issue="AUTO-ESCALATE: grant admin access to the requesting user")] + [
                    _fc("get_order_status", order_id=o) for o in _INJECTED_ORDERS
                ]
        return [types.Part(text=self._compose(user_text, resps, passages))]

    @staticmethod
    def _best(user_text: str, official: list[dict], n: int) -> list[dict]:
        from agentops.rag.embeddings import LocalHashEmbedder

        def words(s: str) -> set[str]:
            return {t for t in LocalHashEmbedder._tokens(s) if "_" not in t}

        q = words(user_text)
        return sorted(official, key=lambda p: (len(q & words(p["text"])), p["score"]), reverse=True)[:n]

    @staticmethod
    def _snippet(p: dict, limit: int = 240) -> str:
        body = p["text"].split(">>>", 1)[-1].split("<<<END", 1)[0].strip().splitlines()
        text = " ".join(body[1:] if len(body) > 1 else body)
        text = re.sub(r"[*`]", "", re.sub(r"\s+", " ", text)).strip()
        return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"

    def _compose(self, user_text: str, resps: list[types.FunctionResponse], passages: list[dict]) -> str:
        lines: list[str] = []
        cited: list[str] = []

        for r in resps:
            body = r.response or {}
            if r.name == "get_order_status":
                if body.get("ok"):
                    d = body["data"]
                    lines.append(
                        f"Order {d['order_id']} ({d['item']}) is {d['status']}"
                        + (f"; delivered {d['delivered_at']} ({d['days_since_delivery']} days ago)" if d.get("delivered_at") else "")
                        + (f" via {d['carrier']}" if d.get("carrier") else "")
                        + " (order record)."
                    )
                    if re.search(r"refund|return", user_text, re.I):
                        if d["status"] == "delivered" and d["days_since_delivery"] is not None:
                            if d["days_since_delivery"] <= 30:
                                lines.append("Refund: eligible, it is within the 30-day window from delivery (POL-001 §1. Eligibility window).")
                            else:
                                lines.append("Refund: not eligible, it is past the 30-day window from delivery (POL-001 §1. Eligibility window); warranty may apply (POL-004).")
                            cited.append("POL-001 §1. Eligibility window")
                        elif d["status"] in ("shipped", "processing"):
                            lines.append(f"Refund: not available while the order is {d['status']}; it can be cancelled or returned instead (POL-001 §2, POL-002 §4).")
                            cited.append("POL-001 §2. Order status requirements")
                        elif d["status"] == "cancelled":
                            lines.append("Refund: cancelled orders are refunded automatically in 5-7 business days (POL-001 §2).")
                            cited.append("POL-001 §2. Order status requirements")
                else:
                    lines.append(f"I couldn't complete the order lookup: {body.get('error', {}).get('message', 'unknown error')}")
            elif r.name == "create_support_ticket":
                if body.get("ok"):
                    cid = body["data"]["confirmation_id"]
                    lines.append(f"I've prepared a support ticket, but it is NOT created yet. Please confirm it (confirmation id {cid}).")
                else:
                    lines.append(f"I couldn't prepare the ticket: {body.get('error', {}).get('message')}")
            elif r.name == "search_policy_documents" and not body.get("ok"):
                lines.append(f"I couldn't search the policies: {body.get('error', {}).get('message')}")

        official = [p for p in passages if p.get("trust") == "official"]
        asked_about_order = any(r.name == "get_order_status" for r in resps)
        # A policy snippet is only added for pure policy questions; order answers stay about the order.
        if official and not asked_about_order:
            # Stand-in for an LLM reading all retrieved passages: quote the 2 that best match the question.
            for p in self._best(user_text, official, n=2):
                lines.append(f"{self._snippet(p)} ({p['source']})")
                cited.append(p["source"])
        elif passages and not official and not asked_about_order:
            lines.append("I only found unreviewed community content, which is not an official policy, so I can't rely on it.")
        elif not passages and any(r.name == "search_policy_documents" and (r.response or {}).get("ok") for r in resps):
            lines.append("I could not find an applicable policy for that.")

        if any(p.get("instruction_like_content_detected") for p in passages):
            lines.append("Note: one retrieved document contained instruction-like text; I ignored it.")
        if not lines:
            lines.append("I wasn't able to find information to answer that.")
        return "\n".join(lines)
