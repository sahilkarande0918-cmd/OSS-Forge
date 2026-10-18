"""Integration tests: real git worktrees, real pytest runs, real patch application. Only Gemma is scripted."""
from pathlib import Path

import pytest
from conftest import FakeClient

from oss_forge.ai.gemma_provider import GemmaProvider
from oss_forge.ai.schemas import Edit, PatchProposal, RegressionTest
from oss_forge.security.execution_policy import TestSandbox
from oss_forge.workflows import reproduce as repro
from oss_forge.workflows.issue_detective import extract_facts, load_issue
from oss_forge.workflows.patch_guard import PatchGuard, Worktree
from oss_forge.workflows.solve import Forge

MEDIAN_TEST = '''from decimal import Decimal
from shopkit.models import Customer, Order, Product
from shopkit.reports import median_order_value


def _order(i, amount):
    o = Order(f"O{i}", Customer("C", "c@example.com", "CA"))
    o.add(Product(f"S{i}", "Thing", Decimal(amount)))
    return o


def test_median_even():
    assert median_order_value([_order(i, a) for i, a in enumerate(["10", "20", "30", "40"])]) == Decimal("25.00")
'''
MEDIAN_FIX = Edit(file="shopkit/reports.py",
                  search="    mid = len(values) // 2\n    return round_money(values[mid])",
                  replace="    mid = len(values) // 2\n    if len(values) % 2 == 0:\n"
                          "        return round_money((values[mid - 1] + values[mid]) / 2)\n    return round_money(values[mid])")
ISSUE = ("Sales report shows the wrong median order value\n\nWith four orders worth 10, 20, 30 and 40 the report "
         "prints median 30.00 but it should be 25.00.")


@pytest.fixture
def sandbox():
    return TestSandbox("host", allow_host_exec=True, timeout=120)  # trusted fixture repo


@pytest.fixture
def wt(shopkit_repo):
    head = (shopkit_repo / ".git").exists() and __import__("subprocess").run(
        ["git", "rev-parse", "HEAD"], cwd=shopkit_repo, capture_output=True, text=True).stdout.strip()
    with Worktree(shopkit_repo, head, "test") as w:
        yield w


def test_load_issue_variants(tmp_path):
    f = tmp_path / "bug.md"
    f.write_text("# Crash in parser\n\nTraceback ...\n")
    assert load_issue(str(f)).title == "Crash in parser"
    assert load_issue("median is wrong\nmore details").source == "text"
    with pytest.raises(ValueError):
        load_issue("#12", None)


def test_extract_facts():
    issue = load_issue('Boom\n\nTraceback (most recent call last):\n  File "shopkit/loyalty.py", line 29, in redeem_points\n'
                       'shopkit.loyalty.InsufficientPoints: customer C1 has only 3 points\n\nExpected: no error\n')
    facts = extract_facts(issue)
    assert facts.stack_frames == ["shopkit/loyalty.py:29 in redeem_points"]
    assert "InsufficientPoints" in " ".join(facts.error_messages) and facts.expected == "no error"


def test_reproduce_statuses(wt, sandbox):
    ok = repro.reproduce(wt.path, RegressionTest(test_file="tests/test_rg_median.py", code=MEDIAN_TEST,
                                                 asserts_behavior="median"), sandbox)
    assert ok.status == repro.REPRODUCED and ok.exit_code == 1, ok.output_tail
    passing = repro.reproduce(wt.path, RegressionTest(test_file="tests/test_rg_pass.py", asserts_behavior="x",
                                                      code="from shopkit.utils import slugify\n\ndef test_x():\n"
                                                           "    assert slugify('a b') == 'a-b'\n"), sandbox)
    assert passing.status == repro.NOT_REPRODUCED
    broken = repro.reproduce(wt.path, RegressionTest(test_file="tests/test_rg_broken.py", asserts_behavior="x",
                                                     code="from shopkit.reports import nope\n\ndef test_x():\n    nope()\n"), sandbox)
    assert broken.status == repro.INCONCLUSIVE
    missing = repro.reproduce(wt.path, RegressionTest(test_file="tests/test_rg_dep.py", asserts_behavior="x",
                                                      code="import numpy_not_installed_xyz\n\ndef test_x():\n    pass\n"), sandbox)
    assert missing.status == repro.ENVIRONMENT_BLOCKED
    evil = repro.reproduce(wt.path, RegressionTest(test_file="tests/test_rg_evil.py", asserts_behavior="x",
                                                   code="import subprocess\n\ndef test_x():\n    subprocess.run(['rm'])\n"), sandbox)
    assert evil.status == repro.INCONCLUSIVE and "forbidden" in evil.reason and evil.exit_code is None


def test_reproduce_blocked_without_sandbox(wt, monkeypatch):
    monkeypatch.setattr("oss_forge.security.execution_policy.docker_available", lambda: False)
    r = repro.reproduce(wt.path, RegressionTest(test_file="tests/test_rg_b.py", code=MEDIAN_TEST, asserts_behavior="m"),
                        TestSandbox("auto", allow_host_exec=False))
    assert r.status == repro.ENVIRONMENT_BLOCKED


def test_patch_guard_rules(wt):
    guard = PatchGuard({"shopkit/reports.py", "shopkit/utils.py"})
    bad = [
        PatchProposal(root_cause="x", explanation="x", edits=[Edit(file="tests/test_shop.py", search="def", replace="x")]),
        PatchProposal(root_cause="x", explanation="x", edits=[Edit(file="shopkit/checkout.py", search="a", replace="b")]),
        PatchProposal(root_cause="x", explanation="x", edits=[Edit(file="pyproject.toml", search="a", replace="b")]),
        PatchProposal(root_cause="x", explanation="x", edits=[Edit(file="../evil.py", search="a", replace="b")]),
    ]
    for proposal in bad:
        assert not guard.apply(wt, proposal).applied
    not_unique = guard.apply(wt, PatchProposal(root_cause="x", explanation="x",
                                               edits=[Edit(file="shopkit/reports.py", search="return", replace="return ")]))
    assert not not_unique.applied and "matched" in not_unique.violations[0]
    syntax = guard.apply(wt, PatchProposal(root_cause="x", explanation="x", edits=[
        Edit(file="shopkit/reports.py", search="    mid = len(values) // 2", replace="    mid = len(values) //")]))
    assert not syntax.applied and "invalid Python" in syntax.violations[0]
    assert wt.changed_files() == []  # nothing written by rejected proposals
    good = guard.apply(wt, PatchProposal(root_cause="x", explanation="x", edits=[MEDIAN_FIX]))
    assert good.applied and good.changed_files == ["shopkit/reports.py"] and "+        return round_money(" in good.diff


def test_patch_guard_tolerates_crlf_and_trailing_space(tmp_path):
    """A repository that commits CRLF files: edits written with LF still match and CRLF is preserved."""
    import subprocess
    repo = tmp_path / "crlf"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/util.py").write_bytes(b"import re\r\n\r\n\r\ndef slug(t):\r\n    return re.sub(r'[^a-z]+', '-', t)\r\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "i"]):
        subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=repo, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    with Worktree(repo, head) as w:
        fix = Edit(file="pkg/util.py", search="    return re.sub(r'[^a-z]+', '-', t)   \n",
                   replace="    return re.sub(r'[^a-z0-9]+', '-', t)\n")
        r = PatchGuard({"pkg/util.py"}).apply(w, PatchProposal(root_cause="x", explanation="x", edits=[fix]))
        assert r.applied and (r.lines_added, r.lines_removed) == (1, 1), r.violations
        data = (w.path / "pkg/util.py").read_bytes()
        assert b"[^a-z0-9]+" in data and data.count(b"\r\n") == 5


def test_forge_analyze_reproduce_and_patch_feedback_loop(shopkit_repo, config, tmp_path):
    replies = [
        {"summary": "median wrong for even counts", "expected_behavior": "25.00", "actual_behavior": "30.00",
         "keywords": ["median", "order", "value"], "suspected_symbols": ["median_order_value"], "clarity": "clear"},
        {"hypotheses": [{"file": "shopkit/reports.py", "symbol": "median_order_value", "explanation": "uses upper middle",
                         "evidence": ["values[mid]"], "confidence": 0.9, "validation": "even-length test"}],
         "regression_test": {"test_file": "tests/test_oss_forge_regression.py", "code": MEDIAN_TEST,
                             "asserts_behavior": "median of 4 values"}},
        # attempt 1: wrong search text -> guard rejects; attempt 2: applies but wrong; attempt 3: correct
        {"root_cause": "x", "explanation": "x", "edits": [{"file": "shopkit/reports.py", "search": "mid = len(v)", "replace": "y"}]},
        {"root_cause": "x", "explanation": "x", "edits": [{"file": "shopkit/reports.py", "search": "values[mid]",
                                                            "replace": "values[mid - 1]"}]},
        {"root_cause": "upper middle element used", "explanation": "average the two middles",
         "edits": [MEDIAN_FIX.model_dump()]},
    ]
    client = FakeClient(replies)
    cfg = config.with_overrides(home=tmp_path / "home", max_patch_attempts=3, sandbox="host")
    forge = Forge(cfg, shopkit_repo, ISSUE, provider=GemmaProvider(cfg, client=client), allow_host_exec=True)
    forge.analyze()
    assert "shopkit/reports.py" in forge.pack.files()
    with Worktree(forge.repo, forge.rm.head, "t") as w:
        assert forge.reproduce(w).status == repro.REPRODUCED
        patch = forge.patch_loop(w)
    assert patch and patch.changed_files == ["shopkit/reports.py"]
    assert [a.guard["applied"] for a in forge.attempts] == [False, True, True]
    assert [a.regression_passed for a in forge.attempts] == [False, False, True]
    assert "Regression test still fails" in client.prompts[4] and len(client.prompts[4]) < len(client.prompts[2])
    assert (forge.store.root / "patch.diff").is_file() and forge.provider.usage()["api_calls"] == 5
    assert "<untrusted" in client.prompts[0]
