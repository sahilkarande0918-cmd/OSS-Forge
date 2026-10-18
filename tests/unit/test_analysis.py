import json
from pathlib import Path

import pytest
from conftest import make_repo

from oss_forge.analysis.context_engine import ContextEngine, IssueSignals
from oss_forge.analysis.repo_map import build_repo_map
from oss_forge.analysis.symbol_index import index_javascript, index_python
from oss_forge.workflows.env_doctor import diagnose

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "benchmarks/cases/cases.json").read_text())["cases"]


@pytest.fixture
def shopkit(shopkit_repo):
    return shopkit_repo


def test_python_symbols():
    idx = index_python("pkg/mod.py", "import os\nfrom .a import b\n\nclass K(Base):\n    def m(self):\n"
                                     "        return helper(1)\n\ndef helper(x):\n    '''Doc.'''\n    raise ValueError('bad input value')\n")
    kinds = {s.qualname: s.kind for s in idx.symbols}
    assert kinds == {"K": "class", "K.m": "method", "helper": "function"}
    assert "helper" in next(s for s in idx.symbols if s.qualname == "K.m").calls
    assert ".a" in idx.imports and "bad input value" in idx.string_literals


def test_javascript_symbols():
    src = ("import { x } from './util';\nexport class Cart {\n  total(items) {\n    return items.length;\n  }\n}\n"
           "export const sum = (a, b) => a + b;\nfunction helper() {\n  return 1;\n}\n")
    idx = index_javascript("src/cart.js", src)
    names = {s.qualname: (s.kind, s.line, s.end_line) for s in idx.symbols}
    assert names["Cart"] == ("class", 2, 6) and names["Cart.total"][0] == "method"
    assert "sum" in names and names["helper"] == ("function", 8, 10)
    assert idx.imports == ["./util"]


def test_repo_map_facts_and_cache(shopkit, tmp_path):
    rm = build_repo_map(shopkit, cache_dir=tmp_path)
    assert not rm.cache_hit and rm.head
    assert "Python" in rm.languages and "pyproject.toml" in rm.manifests
    assert rm.test_frameworks == ["pytest"] and rm.test_command == ["python", "-m", "pytest"]
    assert "tests/test_shop.py" in rm.test_files and "CONTRIBUTING.md" in rm.contribution_docs
    assert any("shopkit.cli:main" in e for e in rm.entry_points)
    assert "shopkit/pricing.py" in rm.imports["shopkit/checkout.py"]
    assert any(s["qualname"] == "Inventory.reserve" for s in rm.symbols)
    assert "# Repository map" in rm.to_markdown()
    again = build_repo_map(shopkit, cache_dir=tmp_path)
    assert again.cache_hit and again.fingerprint == rm.fingerprint
    utils = shopkit / "shopkit" / "utils.py"
    original = utils.read_bytes()
    try:
        utils.write_bytes(original + b"\n# touched\n")
        assert not build_repo_map(shopkit, cache_dir=tmp_path).cache_hit  # invalidated by file change
    finally:
        utils.write_bytes(original)


def test_repo_map_skips_secrets_and_vendored(tmp_path):
    repo = make_repo(tmp_path / "r", {"app.py": "def f():\n    pass\n", ".env": "KEY=x",
                                      "node_modules/x/index.js": "function a(){}", "big.bin": "x"})
    rm = build_repo_map(repo)
    assert rm.files == ["app.py"]


@pytest.mark.parametrize("case", [c for c in CASES if c["ground_truth_symbols"]], ids=lambda c: c["id"])
def test_context_engine_localizes_benchmark_cases(shopkit, case):
    rm = build_repo_map(shopkit)
    engine = ContextEngine(rm)
    pack = engine.build(IssueSignals.from_text(case["issue"]), budget_tokens=2500)
    top3 = [r["symbol"].split(".")[-1] for r in pack.ranking[:3]]
    assert case["ground_truth_symbols"][0] in top3, pack.ranking[:5]
    assert set(case["ground_truth_files"]) <= set(pack.files())
    assert pack.tokens <= 2500 and pack.metrics["context_tokens"] < rm.total_tokens_estimate


def test_context_stack_trace_and_expansion(shopkit):
    rm = build_repo_map(shopkit)
    engine = ContextEngine(rm)
    issue = 'Traceback (most recent call last):\n  File "shopkit/loyalty.py", line 29, in redeem_points\nInsufficientPoints'
    pack = engine.build(IssueSignals.from_text(issue), budget_tokens=1200)
    assert pack.ranking[0]["symbol"] == "redeem_points"
    assert "round_money" in {i.symbol for i in pack.items}  # pulled in as call-graph neighbour
    assert engine.expand(pack, "round_money", "dup") is None and pack.expansions[-1]["note"] == "already in context"
    item = engine.expand(pack, "parse_date", "need date formats")
    assert item and item.file == "shopkit/utils.py" and pack.expansions[-1]["added"]
    assert engine.expand(pack, "does_not_exist", "x") is None


def test_env_doctor(shopkit):
    rep = diagnose(build_repo_map(shopkit))
    assert rep.python_requirement == ">=3.10" and rep.test_command == ["python", "-m", "pytest"]
    assert any(s.requires_approval for s in rep.plan) and "Setup plan" in rep.to_markdown()
    assert rep.ready_to_test
