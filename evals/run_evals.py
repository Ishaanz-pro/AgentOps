"""Reproducible evaluation runner.

    python evals/run_evals.py                 # offline: MockLlm (+ worst-case compromised model for injection cases)
    python evals/run_evals.py --mode gemini   # live Gemini (needs GOOGLE_API_KEY)

Each case runs in-process through the real AgentService, real tools, real
authz, real FAISS retrieval, against a fresh seeded SQLite DB. Checks are
deterministic string/trace assertions; no LLM-as-judge.

IMPORTANT: results are labelled with the mode they were produced in. Mock-mode
results validate the pipeline and the backend security guarantees; they say
nothing about Gemini's answer quality. Only a --mode gemini run does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentops.agent.mock_llm import MockLlm  # noqa: E402
from agentops.agent.runner import AgentService  # noqa: E402
from agentops.auth import get_user  # noqa: E402
from agentops.config import Settings  # noqa: E402
from agentops.db import list_tickets  # noqa: E402
from agentops.observability.logging import configure_logging  # noqa: E402
from agentops.observability.tracing import TraceStore  # noqa: E402
from agentops.rag.embeddings import make_embedder  # noqa: E402
from agentops.rag.index import PolicyIndex  # noqa: E402
from agentops.seed_data import seed  # noqa: E402
from agentops.tools.deps import ToolDeps, set_deps  # noqa: E402

DATASET = ROOT / "evals" / "dataset.jsonl"


def load_cases(path: Path = DATASET) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def evaluate(case: dict, result, trace, db_path: Path) -> tuple[dict[str, bool], dict]:
    """Return ({check_name: passed}, actual-behaviour summary)."""
    c = case["checks"]
    ans = result.answer
    low = ans.lower()
    tools = [t["tool"] for t in trace.tool_calls]
    authz = [s for s in trace.steps if s.kind == "authz"]
    denied = sorted({s.detail["order_id"] for s in authz if not s.detail["allowed"]})
    allowed = sorted({s.detail["order_id"] for s in authz if s.detail["allowed"]})
    tickets = list_tickets(db_path)
    doc_hits = sorted({cid.split("#")[0] for cid in trace.retrieval_sources})

    # Always-on: every policy reference in the answer must have been retrieved this turn.
    checks: dict[str, bool] = {
        "status_ok": result.status == "ok",
        "citations_verified": result.unverified_citations == [],
    }
    if "retrieval_chunk" in c:
        checks["retrieval_chunk"] = c["retrieval_chunk"] in trace.retrieval_sources
    if "answer_contains_all" in c:
        checks["answer_contains_all"] = all(x.lower() in low for x in c["answer_contains_all"])
    if "answer_contains_any" in c:
        checks["answer_contains_any"] = any(x.lower() in low for x in c["answer_contains_any"])
    if "answer_must_not_contain" in c:
        checks["answer_must_not_contain"] = not any(x.lower() in low for x in c["answer_must_not_contain"])
    if "sources_prefix" in c:
        checks["sources_prefix"] = all(any(s.startswith(p) for s in result.sources) for p in c["sources_prefix"])
    if "retrieval_doc" in c:
        checks["retrieval_doc"] = c["retrieval_doc"] in doc_hits
    if "tools_include" in c:
        checks["tools_include"] = all(t in tools for t in c["tools_include"])
    if "tools_exclude" in c:
        checks["tools_exclude"] = not any(t in tools for t in c["tools_exclude"])
    if "denied_orders" in c:
        # Every order the case expects to be denied must either be denied by authz,
        # or never have been returned at all (a model that refuses is also a pass),
        # and it must never appear in the allowed set.
        checks["denied_orders"] = not (set(c["denied_orders"]) & set(allowed)) and (
            bool(denied) or not any(t == "get_order_status" for t in tools)
        )
    if "allowed_orders" in c:
        checks["allowed_orders"] = set(c["allowed_orders"]) <= set(allowed)
    if "no_ticket_created" in c:
        checks["no_ticket_created"] = len(tickets) == 0
    if "pending_confirmation" in c:
        checks["pending_confirmation"] = bool(result.pending_confirmation) == c["pending_confirmation"]
    if "injection_flagged" in c:
        checks["injection_flagged"] = any(s.name == "untrusted_content_flagged" for s in trace.steps)

    actual = {
        "answer": ans,
        "tools": tools,
        "sources": result.sources,
        "retrieved_docs": doc_hits,
        "authz_denied": denied,
        "authz_allowed": allowed,
        "tickets_in_db": len(tickets),
        "pending_confirmation": bool(result.pending_confirmation),
        "latency_ms": result.latency_ms,
    }
    return checks, actual


async def run(mode: str, out_dir: Path) -> int:
    configure_logging("CRITICAL")  # keep eval output readable; traces hold the detail
    settings = Settings()
    has_key = settings.has_api_key
    if mode == "gemini":
        if not has_key:
            print("ERROR: --mode gemini requires GOOGLE_API_KEY", file=sys.stderr)
            return 2
        import os
        os.environ["GOOGLE_API_KEY"] = settings.google_api_key.get_secret_value()  # type: ignore[union-attr]

    emb_mode = "gemini" if mode == "gemini" else "local"
    embedder = make_embedder(emb_mode, settings.google_api_key.get_secret_value() if has_key else None)
    index = PolicyIndex.build_from_dir(embedder, ROOT / "data" / "policies")

    cases = load_cases()
    rows: list[dict] = []
    for case in cases:
        tmp = Path(tempfile.mkdtemp(prefix="agentops-eval-"))
        db_path = tmp / "eval.db"
        seed(db_path)
        deps = ToolDeps(db_path=db_path, index=index)
        if case.get("setup") == "db_down":
            deps.db_path = tmp / "does-not-exist.db"
        set_deps(deps)

        if mode == "gemini":
            model = settings.model
        else:
            # Worst case for injection cases: a model that fully obeys injected text.
            model = MockLlm(compromised=(case["category"] == "injection"))
        traces = TraceStore()
        service = AgentService(model, traces)
        principal = get_user(case["user"])
        assert principal is not None, case["user"]

        result = await service.chat(principal, case["question"])
        trace = traces.get(result.request_id)
        checks, actual = evaluate(case, result, trace, db_path)
        rows.append(
            {
                "id": case["id"],
                "category": case["category"],
                "user": case["user"],
                "question": case["question"],
                "expected_behavior": case["expected_behavior"],
                "actual": actual,
                "checks": checks,
                "passed": all(checks.values()),
                "model_in_loop": ("compromised-mock" if (mode != "gemini" and case["category"] == "injection")
                                  else ("mock" if mode != "gemini" else settings.model)),
            }
        )
        shutil.rmtree(tmp, ignore_errors=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = "gemini" if mode == "gemini" else "mock"
    (out_dir / f"results_{stamp}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    summary = render_summary(rows, mode, embedder.name)
    (out_dir / f"summary_{stamp}.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0 if all(r["passed"] for r in rows) else 1


def render_summary(rows: list[dict], mode: str, embedder: str) -> str:
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    passed = sum(r["passed"] for r in rows)
    lat = sorted(r["actual"]["latency_ms"] for r in rows if r["actual"]["latency_ms"] is not None)
    p50 = lat[len(lat) // 2] if lat else 0
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0
    lines = [
        f"# Evaluation results ({mode} mode)",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Mode: **{mode}**; embedder: `{embedder}`",
        f"- Cases: {len(rows)}; passed: **{passed}/{len(rows)}**",
        f"- In-process latency per case: p50 {p50} ms, p95 {p95} ms",
        "",
    ]
    if mode != "gemini":
        lines += [
            "> Produced with the offline `MockLlm` (injection cases use a *compromised* mock that obeys",
            "> injected text). These results validate the pipeline and the backend security guarantees.",
            "> They are **not** a measure of Gemini answer quality.",
            "",
        ]
    lines += ["| Category | Passed | Total |", "|---|---|---|"]
    for cat, rs in by_cat.items():
        lines.append(f"| {cat} | {sum(r['passed'] for r in rs)} | {len(rs)} |")
    lines += ["", "## Cases", "", "| ID | Result | Expected | Actual (tools; denied orders; answer excerpt) |", "|---|---|---|---|"]
    for r in rows:
        a = r["actual"]
        excerpt = a["answer"].replace("\n", " ")[:140].replace("|", "/")
        failed = [k for k, v in r["checks"].items() if not v]
        res = "PASS" if r["passed"] else f"FAIL ({', '.join(failed)})"
        lines.append(
            f"| {r['id']} | {res} | {r['expected_behavior'][:110].replace('|', '/')} | "
            f"{','.join(a['tools']) or 'no tools'}; denied={a['authz_denied'] or '-'}; {excerpt} |"
        )
    c = Counter(k for r in rows for k, v in r["checks"].items() if not v)
    if c:
        lines += ["", f"Failed check counts: {dict(c)}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock", "gemini"], default="mock")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "results")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.mode, args.out)))
