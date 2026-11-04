# Deployment evidence

Captured 2026-11-04T17:41:37+00:00 against `https://agentops-72n3irkweq-el.a.run.app`.
Everything below is returned by the live service; nothing is edited by hand.

## `/health`
```json
{
  "status": "ok",
  "model_mode": "gemini",
  "model": "gemini-3.5-flash",
  "backend": "vertex-ai",
  "embeddings": "gemini",
  "calendar": "dryrun",
  "runtime": "cloud-run:agentops/agentops-00001-mwh",
  "policy_chunks": 30,
  "database": "ok"
}
```

## Authorized request (Alice, her own order)
status `ok`, latency 48326.93 ms, sources ['POL-001 §1. Eligibility window', 'POL-001 §7. Exceptions', 'POL-001 §2. Order status requirements'], unverified citations []

> Based on the order record, the current delivery status of **ORD-1001** is **delivered**. It was delivered on October 24, 2026, via BlueDart, and it has been **10 days** since delivery.
> 
> **Can it be refunded?**
> Yes, it is eligible for a refund. According to company policy:
> * Delivered orders are eligible for a refund if requested within **30 calendar days** of the delivery date (**POL-001 §1. Eligibility window**).
> * Orders in the `delivered` status are eligible if they fall within this 30-day window (**POL-001 §2. Order status requirements**). 
> 
> Since it has only been 10 days since delivery, the order is well within the eligibility window.

Tool calls: get_order_status (ok), search_policy_documents (ok)

## Unauthorized request (Alice asks for Bob's order)
> I'm sorry, but I cannot access the details for order ORD-2001. It was either not found or you do not have permission to view it.

## Notes (added by the author, not returned by the service)
- The 48 s latency is the first request to a scaled-to-zero instance; it is not a steady-state figure. It was not
  profiled, so the split between cold start and model time is unknown.
- This shows one authorized and one denied request on Cloud Run with Vertex AI. It is not an evaluation: the 25-case
  live eval has not been run against this deployment.
