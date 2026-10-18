"""End-to-end: full solve pipeline with real execution; only Gemma replies are scripted (FakeClient)."""
import json

from conftest import FakeClient
from test_workflows import ISSUE, MEDIAN_FIX, MEDIAN_TEST

from oss_forge.ai.gemma_provider import GemmaProvider
from oss_forge.cli import main
from oss_forge.security.execution_policy import TestSandbox
from oss_forge.workflows.capsule import ProofOfFixCapsule
from oss_forge.workflows.solve import Forge, verify_run
from oss_forge.workflows.verify_fix import FAILED, VERIFIED, verify_fix

INTERP = {"summary": "Median order value is wrong for an even number of orders", "expected_behavior": "25.00",
          "actual_behavior": "30.00", "keywords": ["median"], "suspected_symbols": ["median_order_value"],
          "clarity": "clear"}
DIAG = {"hypotheses": [{"file": "shopkit/reports.py", "symbol": "median_order_value", "explanation": "upper middle used",
                        "evidence": ["values[mid]"], "confidence": 0.9, "validation": "even-length test"}],
        "regression_test": {"test_file": "tests/test_oss_forge_regression.py", "code": MEDIAN_TEST,
                            "asserts_behavior": "median of 4 values"}}
PATCH = {"root_cause": "median_order_value returns the upper middle element for even-length input",
         "explanation": "average the two middle values", "edits": [MEDIAN_FIX.model_dump()]}


def _forge(cfg, repo, replies, issue=ISSUE):
    return Forge(cfg, repo, issue, provider=GemmaProvider(cfg, client=FakeClient(replies)), allow_host_exec=True)


def test_solve_end_to_end_verified(shopkit_repo, config, tmp_path):
    cfg = config.with_overrides(home=tmp_path / "home", sandbox="host", dry_run=False)
    forge = _forge(cfg, shopkit_repo, [INTERP, DIAG, PATCH])
    capsule = forge.solve()
    assert capsule["verification_status"] == VERIFIED, capsule["verification_summary"]
    checks = capsule["verification_checks"]
    assert checks["regression_fails_before"] and checks["regression_passes_after"] and checks["no_new_failures"]
    assert [f["file"] for f in capsule["changed_files"]] == ["shopkit/reports.py"]
    labels = [t["label"] for t in capsule["test_evidence"]]
    assert labels == ["reproduction", "regression_before", "suite_before", "regression_after", "suite_after"]
    run_dir = forge.store.root
    ProofOfFixCapsule.model_validate(json.loads((run_dir / "capsule.json").read_text()))
    assert "VERIFIED" in (run_dir / "capsule.md").read_text(encoding="utf-8")
    pr = (run_dir / "pr.md").read_text(encoding="utf-8")
    assert "## Root cause" in pr and "tests/test_oss_forge_regression.py" in pr
    assert json.loads((run_dir / "pr.json").read_text())["title"].startswith("Fix: ")
    # the user's checkout is untouched
    assert "values[mid - 1]" not in (shopkit_repo / "shopkit/reports.py").read_text()
    # independent re-verification from stored artifacts
    again = verify_run(cfg, forge.store.run_id, allow_host_exec=True)
    assert again["verification_status"] == VERIFIED
    # CLI pr (no --create) prints the package; --create without --approve is refused
    assert main(["pr", "--run-id", forge.store.run_id, "--out", str(cfg.home)]) == 0
    assert main(["pr", "--run-id", forge.store.run_id, "--out", str(cfg.home), "--create"]) == 2


def test_unclear_issue_abstains(shopkit_repo, config, tmp_path):
    cfg = config.with_overrides(home=tmp_path / "home", sandbox="host", dry_run=False)
    replies = [{"summary": "crash when loading orders", "clarity": "unclear",
                "missing_information": ["traceback", "orders file"]},
               {"hypotheses": [], "reproducible": False, "reason_if_not_reproducible": "no traceback or input data"}]
    forge = _forge(cfg, shopkit_repo, replies, "App crashes sometimes\n\nshopkit crashes sometimes when I load my orders.")
    capsule = forge.solve()
    assert capsule["verification_status"] == "INCONCLUSIVE" and not capsule["patch"]
    assert capsule["changed_files"] == [] and forge.provider.usage()["api_calls"] == 2


def test_dry_run_executes_nothing(shopkit_repo, config, tmp_path, monkeypatch):
    cfg = config.with_overrides(home=tmp_path / "home", dry_run=True)
    monkeypatch.setattr(TestSandbox, "run_pytest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("executed!")))
    capsule = _forge(cfg, shopkit_repo, [INTERP, DIAG, PATCH]).solve()
    assert capsule["verification_status"] == "INCONCLUSIVE" and "UNVERIFIED" in capsule["verification_summary"]
    assert capsule["patch"] and capsule["test_evidence"] == []


def test_verification_catches_wrong_file_fix(shopkit_repo):
    """Fixing checkout tax by changing compute_tax semantics passes the new test but breaks existing tests."""
    head = __import__("subprocess").run(["git", "rev-parse", "HEAD"], cwd=shopkit_repo, capture_output=True,
                                        text=True).stdout.strip()
    test = ("from decimal import Decimal\nfrom shopkit.checkout import checkout_total\n"
            "from shopkit.models import Customer, Order, Product\n\n\ndef test_tax():\n"
            "    o = Order('O1', Customer('C', 'c@x.com', 'CA'))\n    o.add(Product('T', 'TV', Decimal('200.00')))\n"
            "    assert checkout_total(o).tax == Decimal('14.50')\n")
    wrong = ("diff --git a/shopkit/pricing.py b/shopkit/pricing.py\n--- a/shopkit/pricing.py\n+++ b/shopkit/pricing.py\n"
             "@@ -22,6 +22,6 @@ def compute_tax(amount: Decimal, rate_percent: Decimal) -> Decimal:\n"
             '     """Tax owed on `amount` at `rate_percent` percent (e.g. 7.25 for 7.25%)."""\n'
             "     if rate_percent < 0:\n"
             '         raise ValueError("tax rate cannot be negative")\n'
             "-    return round_money(Decimal(amount) * Decimal(rate_percent) / Decimal(100))\n"
             "+    return round_money(Decimal(amount) * Decimal(rate_percent))\n"
             " \n \n")  # trailing context: git apply anchors hunks without it to end-of-file
    v = verify_fix(shopkit_repo, head, wrong, "tests/test_rg_tax.py", test, TestSandbox("host", allow_host_exec=True))
    assert v.patch_applied, v.patch_apply_output
    assert v.checks["regression_fails_before"] and v.checks["regression_passes_after"]
    assert v.status == FAILED and any("compute_tax_uses_percent" in n for n in v.new_failures)
