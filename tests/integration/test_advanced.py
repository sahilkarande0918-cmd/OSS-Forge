"""Repository memory, CI triage, maintainer mode and the benchmark harness mechanics."""
import importlib.util
import sys
from pathlib import Path

from conftest import FakeClient, git
from test_e2e import DIAG, INTERP, PATCH

from oss_forge.ai.gemma_provider import GemmaProvider
from oss_forge.analysis.repo_map import build_repo_map
from oss_forge.analysis.repo_memory import RepoMemory
from oss_forge.workflows import ci_repair, maintainer
from oss_forge.workflows.solve import Forge

ROOT = Path(__file__).resolve().parents[2]


def test_memory_records_runs_and_detects_staleness(shopkit_repo, config, tmp_path):
    cfg = config.with_overrides(home=tmp_path / "home", sandbox="host", dry_run=False)
    forge = Forge(cfg, shopkit_repo, "Median order value wrong for even orders: 10,20,30,40 gives 30 not 25",
                  provider=GemmaProvider(cfg, client=FakeClient([INTERP, DIAG, PATCH])), allow_host_exec=True)
    capsule = forge.solve()
    assert capsule["verification_status"] == "VERIFIED"
    mem = RepoMemory.load(cfg.home, forge.rm.repo_id)
    assert mem.data["verified_fixes"][0]["files"] == ["shopkit/reports.py"]
    assert mem.data["conventions"]["test_command"] == ["python", "-m", "pytest"]
    hints = mem.hints_for(shopkit_repo, "median order value is wrong again for even counts")
    assert hints and "shopkit/reports.py" in hints[0]
    # change the file in a new commit -> the fact becomes stale and is not reused
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(shopkit_repo), str(clone))
    reports = clone / "shopkit/reports.py"
    reports.write_text(reports.read_text() + "\n# changed\n")
    git(clone, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qam", "change")
    assert mem.hints_for(clone, "median order value is wrong again for even counts") == []
    assert mem.summary(clone)["verified_fixes"] == {"fresh": 0, "stale": 1}


def test_ci_triage_classifies_environment_vs_code():
    env_log = ("build\tInstall\t2026-01-01T00:00:00.0Z ERROR: Could not find a version that satisfies the requirement foo==9\n"
               "build\tInstall\t2026-01-01T00:00:01.0Z ##[error]Process completed with exit code 1.\n")
    t = ci_repair.triage_log(env_log, "file", ["shopkit/reports.py"])
    assert t.classification == "environment" and t.failed_steps
    code_log = ("test\tRun pytest\tFAILED tests/test_reports.py::test_median - AssertionError: assert 30 == 25\n"
                'test\tRun pytest\t  File "shopkit/reports.py", line 22, in median_order_value\n'
                "test\tRun pytest\t##[error]Process completed with exit code 1.\n")
    t = ci_repair.triage_log(code_log, "file", ["shopkit/reports.py"])
    assert t.classification.startswith("code (touches") and t.failing_tests == ["tests/test_reports.py"]
    assert t.correlated_files == ["shopkit/reports.py"]
    t = ci_repair.triage_log("token ghp_abcdefghijklmnopqrstuvwxyz123456 leaked\n", "file", [])
    assert "ghp_" not in t.excerpt


def test_maintainer_triage(shopkit_repo):
    issues = [
        {"number": 1, "title": "Median order value wrong for even counts", "labels": [{"name": "bug"}],
         "body": "Expected 25.00, actual 30.00 for orders 10, 20, 30, 40 in median_order_value."},
        {"number": 2, "title": "Wrong median order value with even number of orders", "labels": [],
         "body": "median_order_value returns 30.00, expected 25.00 for 10 20 30 40 orders."},
        {"number": 3, "title": "Support for multiple warehouses", "labels": [{"name": "enhancement"}],
         "body": "It would be great to plan a redesign of storage to support many warehouses and regions. " * 5},
    ]
    items = {t.number: t for t in maintainer.triage(issues, build_repo_map(shopkit_repo))}
    assert items[1].automatable and "shopkit/reports.py" in items[1].likely_files
    assert items[2].duplicate_of == 1 and not items[2].automatable
    assert not items[3].automatable
    assert "Maintainer triage" in maintainer.to_markdown(list(items.values()))


def test_benchmark_harness_mechanics(config, tmp_path, monkeypatch):
    """Runs the live-suite code path with scripted replies to check grading; results go to tmp, not results/."""
    spec = importlib.util.spec_from_file_location("bench_under_test", ROOT / "benchmarks/benchmark_runner.py")
    bench = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bench
    spec.loader.exec_module(bench)
    replies = {"baseline": [PATCH] * 3, "oss-forge": [INTERP, DIAG, PATCH]}
    real = bench.GemmaProvider

    def scripted(cfg, client=None):
        import inspect
        caller = "baseline" if any(f.function == "run_baseline" for f in inspect.stack()) else "oss-forge"
        return real(cfg, client=FakeClient(list(replies[caller])))
    monkeypatch.setattr(bench, "GemmaProvider", scripted)
    monkeypatch.setattr("oss_forge.workflows.solve.GemmaProvider", scripted)
    case = next(c for c in bench.load_cases("smoke") if c["id"] == "A-median-logic")
    monkeypatch.setattr(bench, "load_cases", lambda suite: [case])
    report = bench.run(config.with_overrides(sandbox="host"), "smoke", "live", 2500, True, tmp_path / "results")
    rows = {r["system"]: r for r in report["rows"]}
    assert rows["oss-forge"]["status"] == "VERIFIED" and rows["oss-forge"]["oracle_pass"] is True
    # path-order context at 2500 tokens never shows shopkit/reports.py, so the guard rejects the baseline's edit
    assert rows["baseline"]["status"] == "NO_PATCH" and rows["baseline"]["localized"] is False
    assert rows["baseline"]["api_calls"] == 3 and rows["baseline"]["oracle_pass"] is None
    assert report["summary"]["oss-forge"]["false_success_claims"] == 0
    assert (tmp_path / "results" / "latest-live.md").is_file()
