"""Automatic bug reproduction: validate a generated regression test, run it on the ORIGINAL code, classify.

Statuses:
  REPRODUCED           the test fails on the original code with an assertion failure or the reported exception
  NOT_REPRODUCED       the test passes on the original code (the bug, as encoded, is not present)
  ENVIRONMENT_BLOCKED  no sandbox/approval, or a third-party dependency is missing
  INCONCLUSIVE         the test itself is broken (import/name/syntax errors, collected nothing, timed out)
"""
from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from oss_forge.ai.schemas import RegressionTest
from oss_forge.security.execution_policy import CommandResult, PolicyViolation, TestSandbox, safe_path

REPRODUCED, NOT_REPRODUCED, ENVIRONMENT_BLOCKED, INCONCLUSIVE = (
    "REPRODUCED", "NOT_REPRODUCED", "ENVIRONMENT_BLOCKED", "INCONCLUSIVE")
FORBIDDEN_CALLS = {"system", "popen", "Popen", "rmtree", "exec", "eval", "__import__", "urlopen", "fork",
                   "kill", "spawnv", "execv"}
FORBIDDEN_MODULES = {"subprocess", "socket", "requests", "httpx", "urllib", "shutil", "ctypes", "multiprocessing"}


@dataclass
class ReproResult:
    status: str
    reason: str
    test_file: str
    exit_code: int | None = None
    duration_s: float = 0.0
    runner: str = ""
    failure_type: str = ""
    output_tail: str = ""
    command: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def validate_test_code(test: RegressionTest) -> list[str]:
    """Static checks before any execution: parseable, defines tests, no process/network/filesystem side effects."""
    problems = []
    name = Path(test.test_file).name
    if not (name.startswith("test_") and name.endswith(".py")):
        problems.append("test file must be named test_*.py")
    try:
        tree = ast.parse(test.code)
    except SyntaxError as exc:
        return problems + [f"test code does not parse: {exc.msg} (line {exc.lineno})"]
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
               for n in ast.walk(tree)):
        problems.append("test module defines no test_* function")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            bad = [m for m in mods if m.split(".")[0] in FORBIDDEN_MODULES]
            if bad:
                problems.append(f"forbidden import in regression test: {bad}")
        elif isinstance(node, ast.Call):
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
            if called in FORBIDDEN_CALLS:
                problems.append(f"forbidden call in regression test: {called}()")
    return problems


def write_test(worktree: Path, test: RegressionTest) -> Path:
    rel = test.test_file.replace("\\", "/").lstrip("./")
    path = safe_path(worktree, rel)
    if path.exists():
        raise PolicyViolation(f"regression test must be a NEW file, {rel} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(test.code.encode("utf-8"))
    return path


def _failure_type(output: str) -> str:
    m = re.search(r"^(?:FAILED|ERROR) \S+ - (\w+(?:\.\w+)*)", output, re.M)
    if m:
        return m.group(1).split(".")[-1]
    m = re.search(r"^E\s+(\w+(?:Error|Exception|Exit)\w*)", output, re.M)
    return m.group(1) if m else ""


def classify(result: CommandResult, expected_exceptions: set[str] | None = None) -> tuple[str, str, str]:
    """Map a pytest run on ORIGINAL code to (status, reason, failure_type)."""
    out = result.output
    ftype = _failure_type(out)
    if result.runner == "blocked":
        return ENVIRONMENT_BLOCKED, result.stderr, ""
    if result.timed_out:
        return INCONCLUSIVE, "test run timed out", ""
    if result.exit_code == 0:
        return NOT_REPRODUCED, "regression test passes on the original code", ""
    missing = re.search(r"No module named '([\w.]+)'", out)
    if missing:
        return ENVIRONMENT_BLOCKED, f"missing module '{missing.group(1)}' (install project dependencies)", "ModuleNotFoundError"
    if result.exit_code in (2, 3, 4) or "error" in out.lower() and "collected 0 items" in out:
        return INCONCLUSIVE, "test could not be collected/run (pytest exit %s)" % result.exit_code, ftype
    if result.exit_code == 5:
        return INCONCLUSIVE, "no tests were collected", ""
    if result.exit_code == 1:
        if ftype in ("AssertionError", "Failed") or re.search(r"^E\s+assert ", out, re.M):
            return REPRODUCED, "assertion fails on the original code", ftype or "AssertionError"
        if expected_exceptions and ftype in expected_exceptions:
            return REPRODUCED, f"original code raises the reported {ftype}", ftype
        if ftype in ("ImportError", "NameError", "SyntaxError", "AttributeError", "TypeError", "fixture"):
            return INCONCLUSIVE, f"test errored with {ftype}; likely a broken test rather than the bug", ftype
        return INCONCLUSIVE, f"test failed with unexpected {ftype or 'error'}; not the reported symptom", ftype
    return INCONCLUSIVE, f"unexpected pytest exit code {result.exit_code}", ftype


def tail(text: str, lines: int = 60) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_regression_test(worktree: Path, test_rel: str, sandbox: TestSandbox) -> CommandResult:
    return sandbox.run_pytest(worktree, [test_rel, "-q", "--tb=short", "-rfE", "--no-header"])


def reproduce(worktree: Path, test: RegressionTest, sandbox: TestSandbox,
              expected_exceptions: set[str] | None = None) -> ReproResult:
    rel = test.test_file.replace("\\", "/").lstrip("./")
    problems = validate_test_code(test)
    if problems:
        return ReproResult(INCONCLUSIVE, "; ".join(problems), rel)
    try:
        write_test(worktree, test)
    except PolicyViolation as exc:
        return ReproResult(INCONCLUSIVE, str(exc), rel)
    result = run_regression_test(worktree, rel, sandbox)
    status, reason, ftype = classify(result, expected_exceptions)
    return ReproResult(status, reason, rel, result.exit_code, result.duration_s, result.runner, ftype,
                       tail(result.output), result.argv)
