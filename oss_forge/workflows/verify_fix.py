"""Proof-of-fix verification, independent of the run that produced the patch.

In a FRESH worktree at the recorded base commit:
  1. add the regression test; run it on the original code (must fail)
  2. run the existing suite on the original code (baseline, per-test outcomes via JUnit XML)
  3. apply the stored patch with `git apply` (must apply cleanly)
  4. run the regression test again (must pass) and the existing suite again (no new failures)
  5. check changed files == patch files + regression test, Python still parses, `git diff --check` is clean
Statuses: VERIFIED, PARTIALLY_VERIFIED, FAILED, INCONCLUSIVE, BLOCKED.
"""
from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from oss_forge.security.execution_policy import CommandResult, TestSandbox
from oss_forge.workflows.patch_guard import Worktree
from oss_forge.workflows.reproduce import tail

VERIFIED, PARTIALLY_VERIFIED, FAILED, INCONCLUSIVE, BLOCKED = (
    "VERIFIED", "PARTIALLY_VERIFIED", "FAILED", "INCONCLUSIVE", "BLOCKED")
JUNIT = ".oss-forge-junit.xml"


@dataclass
class TestRun:
    __test__ = False
    label: str
    command: list[str]
    exit_code: int | None
    duration_s: float
    runner: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    outcomes: dict[str, str] = field(default_factory=dict)
    output_tail: str = ""

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped


@dataclass
class Verification:
    status: str
    summary: str
    base_commit: str
    checks: dict[str, bool | None] = field(default_factory=dict)
    runs: list[TestRun] = field(default_factory=list)
    patch_applied: bool = False
    patch_apply_output: str = ""
    changed_files: list[str] = field(default_factory=list)
    unexpected_files: list[str] = field(default_factory=list)
    new_failures: list[str] = field(default_factory=list)
    fixed_tests: list[str] = field(default_factory=list)
    preexisting_failures: list[str] = field(default_factory=list)
    syntax_errors: list[str] = field(default_factory=list)
    whitespace_issues: str = ""
    sandbox: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def run(self, label: str) -> TestRun | None:
        return next((r for r in self.runs if r.label == label), None)


def parse_junit(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {}
    outcomes = {}
    for case in root.iter("testcase"):
        node = f"{case.get('classname', '')}::{case.get('name', '')}"
        tags = {child.tag for child in case}
        outcomes[node] = "failed" if "failure" in tags else "error" if "error" in tags else \
            "skipped" if "skipped" in tags else "passed"
    return outcomes


def _pytest(wt: Worktree, sandbox: TestSandbox, label: str, args: list[str]) -> TestRun:
    junit = wt.path / JUNIT
    junit.unlink(missing_ok=True)
    res: CommandResult = sandbox.run_pytest(wt.path, [*args, "-q", "--tb=short", "--no-header", f"--junitxml={JUNIT}"])
    outcomes = parse_junit(junit)
    junit.unlink(missing_ok=True)
    counts = {k: sum(1 for v in outcomes.values() if v == k) for k in ("passed", "failed", "error", "skipped")}
    return TestRun(label, res.argv, res.exit_code, res.duration_s, res.runner, counts["passed"], counts["failed"],
                   counts["error"], counts["skipped"], outcomes, tail(res.output, 40))


def verify_fix(repo: Path, base_commit: str, patch_diff: str, regression_test_rel: str | None,
               regression_test_code: str | None, sandbox: TestSandbox, run_existing: bool = True) -> Verification:
    decision = sandbox.decide()
    v = Verification(INCONCLUSIVE, "", base_commit, sandbox=f"{decision.runner}: {decision.reason}")
    if decision.runner == "blocked":
        v.status, v.summary = BLOCKED, f"tests cannot run: {decision.reason}"
        return v
    rt = regression_test_rel.replace("\\", "/") if regression_test_rel else None
    with Worktree(repo, base_commit, "verify") as wt:
        if rt and regression_test_code:
            (wt.path / rt).parent.mkdir(parents=True, exist_ok=True)
            (wt.path / rt).write_bytes(regression_test_code.encode("utf-8"))
            v.runs.append(_pytest(wt, sandbox, "regression_before", [rt]))
        if run_existing:
            v.runs.append(_pytest(wt, sandbox, "suite_before", [f"--ignore={rt}"] if rt else []))

        ok, out = wt.apply_diff(patch_diff)
        v.patch_applied, v.patch_apply_output = ok, out[-1000:]
        if not ok:
            v.status, v.summary = FAILED, "stored patch does not apply to the base commit"
            v.checks["patch_applies"] = False
            return v
        if rt and regression_test_code:
            v.runs.append(_pytest(wt, sandbox, "regression_after", [rt]))
        if run_existing:
            v.runs.append(_pytest(wt, sandbox, "suite_after", [f"--ignore={rt}"] if rt else []))

        v.changed_files = wt.changed_files()
        patch_files = {line[6:].strip() for line in patch_diff.splitlines() if line.startswith("+++ b/")}
        v.unexpected_files = sorted(set(v.changed_files) - patch_files - ({rt} if rt else set()))
        for f in v.changed_files:
            if f.endswith(".py") and (wt.path / f).is_file():
                try:
                    ast.parse((wt.path / f).read_bytes().decode("utf-8"))
                except SyntaxError as exc:
                    v.syntax_errors.append(f"{f}: {exc.msg} (line {exc.lineno})")
        v.whitespace_issues = wt.git("diff", "--check").stdout.strip()[:1000]

    return _decide(v, rt is not None and regression_test_code is not None, run_existing)


def _decide(v: Verification, has_regression: bool, run_existing: bool) -> Verification:
    before, after = v.run("regression_before"), v.run("regression_after")
    s_before, s_after = v.run("suite_before"), v.run("suite_after")
    if s_before and s_after:
        v.preexisting_failures = sorted(k for k, o in s_before.outcomes.items() if o in ("failed", "error"))
        v.new_failures = sorted(k for k, o in s_after.outcomes.items()
                                if o in ("failed", "error") and s_before.outcomes.get(k) == "passed")
        v.fixed_tests = sorted(k for k, o in s_after.outcomes.items()
                               if o == "passed" and s_before.outcomes.get(k) in ("failed", "error"))
    env_blocked = any(r.runner == "blocked" or "No module named" in r.output_tail for r in v.runs)
    v.checks = {
        "patch_applies": v.patch_applied,
        "regression_fails_before": (before.exit_code == 1 and before.failed > 0) if before else None,
        "regression_passes_after": (after.exit_code == 0 and after.passed > 0) if after else None,
        "existing_suite_ran": bool(s_after and s_after.total > 0) if run_existing else None,
        "no_new_failures": (not v.new_failures) if s_before and s_after and s_after.total else None,
        "only_expected_files_changed": not v.unexpected_files,
        "syntax_ok": not v.syntax_errors,
        "whitespace_ok": not v.whitespace_issues,
    }
    c = v.checks
    if env_blocked and not (after and after.passed):
        v.status, v.summary = BLOCKED, "test environment is missing dependencies; see run outputs"
    elif c["regression_passes_after"] is False or c["no_new_failures"] is False or not c["only_expected_files_changed"] \
            or not c["syntax_ok"]:
        problems = [k for k in ("regression_passes_after", "no_new_failures", "only_expected_files_changed", "syntax_ok")
                    if c[k] is False]
        v.status, v.summary = FAILED, "verification failed: " + ", ".join(problems)
    elif has_regression and c["regression_fails_before"] and c["regression_passes_after"] and c["no_new_failures"] \
            and c["existing_suite_ran"]:
        v.status = VERIFIED
        v.summary = (f"regression test fails on {v.base_commit[:10]} and passes with the patch; "
                     f"{s_after.passed}/{s_after.total} existing tests pass with no new failures")
    elif c["regression_passes_after"] or (c["existing_suite_ran"] and c["no_new_failures"]):
        missing = [k for k in ("regression_fails_before", "existing_suite_ran", "no_new_failures") if not c.get(k)]
        v.status, v.summary = PARTIALLY_VERIFIED, "some evidence is missing: " + ", ".join(missing or ["regression test"])
    else:
        v.status, v.summary = INCONCLUSIVE, "no test evidence could be collected"
    return v
