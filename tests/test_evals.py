"""The evaluation suite runs as part of pytest (offline mock mode)."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_evals", ROOT / "evals" / "run_evals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dataset_meets_required_category_minimums():
    mod = _load_runner()
    cases = mod.load_cases()
    counts = {}
    for c in cases:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
        assert c["expected_behavior"] and c["checks"], c["id"]
    assert counts["policy"] >= 5
    assert counts["order"] >= 5
    assert counts["unauthorized"] >= 3
    assert counts["injection"] >= 3
    assert counts["failure"] >= 3
    assert len({c["id"] for c in cases}) == len(cases)


async def test_all_eval_cases_pass_offline(tmp_path):
    mod = _load_runner()
    code = await mod.run("mock", tmp_path)
    rows = [json.loads(l) for l in (tmp_path / "results_mock.jsonl").read_text(encoding="utf-8").splitlines()]
    failed = [(r["id"], [k for k, v in r["checks"].items() if not v]) for r in rows if not r["passed"]]
    assert failed == [], failed
    assert code == 0
    assert (tmp_path / "summary_mock.md").exists()
