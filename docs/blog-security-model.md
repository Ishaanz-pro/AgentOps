# Don't trust the model: designing an enterprise agent whose security holds even when the LLM is hijacked

*Draft for a public post. Everything described is in this repo and covered by tests. It uses synthetic data and has not been deployed to production.*

Most agent demos secure the agent by asking it nicely. The system prompt says "never reveal other users' orders", and the
security review ends there. A prompt is a request, not a control. Any text the model reads, including a document it retrieved
or a photo a user uploaded, can contain instructions that compete with yours.

So I built an employee assistant on Google's Agent Development Kit and Gemini with one rule: **assume the model is already compromised, and make that survivable.**

## The three mistakes the design avoids

**1. Letting the model say who it is acting for.** If `get_order_status(order_id, user_id)` takes a user id, a hijacked model
can pass anyone's. Here the backend writes the authenticated user into ADK session state from the verified token. Tools read it
from `tool_context.state`. The model has no argument to set it.

**2. Authorizing in the prompt.** `can_access_order(principal, owner, region)` is a plain function that runs inside the tool on
every call. Employees see their own orders, support agents their region, admins everything. Denied and not-found return the same error, so IDs cannot be enumerated.

**3. Letting the model perform side effects.** `create_support_ticket` and `schedule_followup` only *propose*. The write happens when the human clicks Confirm, which sends `POST /confirm` as the same user. The confirmation is single-use, expires after ten minutes, is bound to the proposing user, and its kind is fixed at proposal time, so a ticket confirmation cannot be redeemed as a calendar write.

## Testing it against a worst case

The offline `MockLlm` has a `compromised` mode: when it sees injected text in a retrieved document, it obeys. It files an
escalation ticket, books a calendar event, and requests two other users' orders. The test asserts the outcome that matters:

- both foreign orders are denied by the backend,
- no ticket row exists, no calendar row exists,
- both injected proposals are surfaced to the user rather than silently dropped.

That test caught a real bug while I was extending the system: the trace stored only one pending proposal, so a second injected one would have been invisible to the user. It now keeps a list.

## What still goes wrong

- The model can still *say* misleading things within what the user may see. Authorization limits the blast radius; it does not make the model honest.
- A careless human can confirm a malicious proposal. The UI shows exactly what will be written to reduce this, which is mitigation, not a fix.
- Injection detection is a regex heuristic. The load-bearing control is authorization, not detection.
- A citation verifier checks that cited policy sections were actually retrieved this turn. It checks provenance, not truth.

## Multimodal and Workspace without new trust paths

Photos arrive as a base64 field. The server sniffs magic bytes, requires the declared type to match, caps size at 4 MB, content-addresses by sha256, keeps bytes out of logs and traces, and writes to disk only after a human confirms the ticket. Text inside an image is treated like a retrieved document: data, not instructions.

Calendar access follows the same gate. The service writes to one designated support calendar as itself, using its service account. It never holds a user's OAuth token, so a hijacked model has nothing to read or modify in anyone's personal calendar.

## What I did not verify

The full evaluation on live Gemini is incomplete: the free tier allows 20 requests per day per model, and my first live run reached 4 of 21 cases before quota ran out. The offline results prove the pipeline and the backend guarantees; they say nothing about Gemini's answer quality. Deployment to Cloud Run, Agent Engine and Gemini Enterprise is scripted but, at the time of writing, not run. (Update this paragraph with real results before publishing.)

## Takeaways for people deploying agents

1. Put identity in server-held state, not tool arguments.
2. Authorize inside the tool, as code, with unit tests that need no LLM.
3. Make every side effect two-phase, with a human in the second phase.
4. Test with a model you have deliberately compromised.
5. Report your evals honestly, including the incomplete ones.

Repo: *(add link once pushed)*
