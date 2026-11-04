"""Capture verifiable evidence that the service is running in the cloud. Writes docs/deployment-evidence.md.

    python scripts/cloud_evidence.py https://YOUR-URL [--token tok-alice]

Records only what the live service itself returns: /health (including the Cloud Run revision the platform
injected), and one authorized and one denied request. Nothing is typed in by hand.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "docs" / "deployment-evidence.md")
    a = ap.parse_args()
    base = a.url.rstrip("/")
    alice = {"Authorization": "Bearer tok-alice"}

    with httpx.Client(base_url=base, timeout=180) as c:
        health = c.get("/health").json()
        ok = c.post("/chat", json={"message": "Can order ORD-1001 be refunded, and what is its current delivery status?"}, headers=alice).json()
        denied = c.post("/chat", json={"message": "What is the status of order ORD-2001?"}, headers=alice).json()
        trace = c.get(f"/trace/{ok['request_id']}", headers=alice).json()

    if not health["runtime"].startswith("cloud-run:"):
        print(f"REFUSING: runtime is {health['runtime']!r}, not Cloud Run. Point this at the deployed URL.")
        return 1

    md = [
        "# Deployment evidence",
        "",
        f"Captured {datetime.now(timezone.utc).isoformat(timespec='seconds')} against `{base}`.",
        "Everything below is returned by the live service; nothing is edited by hand.",
        "",
        "## `/health`",
        "```json", json.dumps(health, indent=2), "```",
        "",
        "## Authorized request (Alice, her own order)",
        f"status `{ok['status']}`, latency {ok['latency_ms']} ms, sources {ok['sources']}, unverified citations {ok['unverified_citations']}",
        "", "> " + ok["answer"].replace("\n", "\n> "),
        "",
        "Tool calls: " + ", ".join(f"{t['tool']} ({'ok' if t['ok'] else t['code']})" for t in trace["tool_calls"]),
        "",
        "## Unauthorized request (Alice asks for Bob's order)",
        "> " + denied["answer"].replace("\n", "\n> "),
        "",
    ]
    a.out.write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {a.out}")
    print("runtime:", health["runtime"], "| backend:", health["backend"], "| model:", health["model"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
