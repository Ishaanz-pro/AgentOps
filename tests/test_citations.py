from agentops.agent.citations import unverified_citations

RETRIEVED = ["POL-001 §1. Eligibility window", "POL-002 §4. Cancellation"]


def test_retrieved_sections_verify():
    ans = "Eligible within 30 days (POL-001 §1. Eligibility window). Cancel via POL-002 §4."
    assert unverified_citations(ans, RETRIEVED) == []


def test_unretrieved_section_of_retrieved_document_is_flagged():
    # POL-001 was retrieved, but only §1: citing §6 is hallucinated provenance.
    assert unverified_citations("See POL-001 §6.", RETRIEVED) == ["POL-001 §6"]


def test_unretrieved_document_is_flagged():
    assert unverified_citations("Warranty may apply (POL-004).", RETRIEVED) == ["POL-004"]


def test_document_level_mention_of_retrieved_doc_is_fine():
    assert unverified_citations("Per POL-001, refunds take time.", RETRIEVED) == []


def test_no_citations_no_findings():
    assert unverified_citations("Your order is delivered.", []) == []


def test_results_are_deduplicated_and_sorted():
    ans = "POL-009 §2, POL-009 §2, POL-003"
    assert unverified_citations(ans, RETRIEVED) == ["POL-003", "POL-009 §2"]
