"""CI failure recovery: fetch logs, find failed steps, separate environment from code failures, correlate
with the files a run modified, re-run the failing tests locally (sandboxed) and record it in the capsule.

Remote workflows are never re-run or modified. Without GitHub access, pass a saved log file (dry run).
Automatic patch generation for CI failures is not implemented; Gemma's suggested fix is advisory.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from oss_forge.ai.gemma_provider import GemmaProvider, untrusted
from oss_forge.ai.schemas import CIFailureAnalysis
from oss_forge.security.execution_policy import TestSandbox, redact, run_command
from oss_forge.workflows.patch_guard import Worktree
from oss_forge.workflows.reproduce import tail

ENV_PATTERNS = [
    (r"No module named '([\w.]+)'", "missing Python module"), (r"command not found", "missing tool"),
    (r"Could not find a version that satisfies", "dependency resolution"), (r"npm ERR! (?:code )?E\w+", "npm install"),
    (r"(?:ETIMEDOUT|ECONNRESET|Connection (?:reset|refused)|Read timed out|Temporary failure in name resolution)", "network"),
    (r"No space left on device", "runner disk"), (r"API rate limit exceeded", "rate limit"),
    (r"The operation was canceled|exceeded the maximum execution time", "timeout/cancelled"),
]
CODE_PATTERNS = [
    (r"^FAILED (\S+?)(?:::\S+)? - (\w+)", "test failure"), (r"^E\s+(AssertionError|assert .*)", "assertion"),
    (r"SyntaxError: .+", "syntax error"), (r"^(\S+\.py):(\d+):\d+: ([A-Z]\d{3,4}) ", "lint"),
    (r"error TS\d+:", "typescript"), (r"Traceback \(most recent call last\)", "exception"),
]


@dataclass
class CITriage:
    source: str
    failed_steps: list[str] = field(default_factory=list)
    environment_signals: list[str] = field(default_factory=list)
    code_signals: list[str] = field(default_factory=list)
    failing_tests: list[str] = field(default_factory=list)
    files_in_log: list[str] = field(default_factory=list)
    correlated_files: list[str] = field(default_factory=list)
    classification: str = "unknown"
    excerpt: str = ""
    model_analysis: dict | None = None
    local_rerun: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_failed_log(repo: Path, pr: str | None = None, run_id: str | None = None) -> str:
    """`gh run view --log-failed` for a run id, or the latest failed run of a PR's head branch."""
    if not run_id and pr:
        r = run_command(["gh", "pr", "view", pr, "--json", "headRefName", "-q", ".headRefName"], repo,
                        keep_credentials=True)
        branch = r.stdout.strip()
        r = run_command(["gh", "run", "list", "--branch", branch, "--status", "failure", "--limit", "1",
                         "--json", "databaseId", "-q", ".[0].databaseId"], repo, keep_credentials=True)
        run_id = r.stdout.strip()
    if not run_id:
        raise RuntimeError("no failed CI run found (need --ci-run, --pr with a failed run, or --log FILE)")
    r = run_command(["gh", "run", "view", run_id, "--log-failed"], repo, timeout=180, keep_credentials=True)
    if not r.ok:
        raise RuntimeError(f"could not fetch CI log: {r.output[-300:]}")
    return r.stdout


def triage_log(log: str, source: str, modified_files: list[str]) -> CITriage:
    log = redact(log)
    t = CITriage(source=source)
    lines = log.splitlines()
    for line in lines:
        clean = re.sub(r"^[^\t]+\t[^\t]+\t", "", line)    # strip `gh` "job<TAB>step<TAB>" prefixes
        clean = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ", "", clean)
        if "##[error]" in clean:
            t.failed_steps.append(clean.split("##[error]", 1)[1].strip()[:200])
        for pat, label in ENV_PATTERNS:
            m = re.search(pat, clean)
            if m:
                t.environment_signals.append(f"{label}: {m.group(0)[:120]}")
        for pat, label in CODE_PATTERNS:
            m = re.search(pat, clean)
            if m:
                t.code_signals.append(f"{label}: {clean.strip()[:160]}")
                if label == "test failure":
                    t.failing_tests.append(m.group(1))
    t.files_in_log = sorted(set(re.findall(r"([\w./-]+\.(?:py|js|ts|tsx|jsx))(?::\d+|\")", log)))[:30]
    t.correlated_files = sorted(f for f in t.files_in_log if any(f.endswith(m) or m.endswith(f) for m in modified_files))
    t.failing_tests = sorted(set(t.failing_tests))
    t.environment_signals = sorted(set(t.environment_signals))[:10]
    t.code_signals = list(dict.fromkeys(t.code_signals))[:15]
    if t.environment_signals and not t.code_signals:
        t.classification = "environment"
    elif t.code_signals and t.correlated_files:
        t.classification = "code (touches modified files)"
    elif t.code_signals:
        t.classification = "code (outside modified files: possibly pre-existing or flaky)"
    elif t.environment_signals:
        t.classification = "environment"
    error_idx = next((i for i, line in enumerate(lines) if re.search(r"##\[error\]|FAILED |Error", line)), len(lines))
    t.excerpt = "\n".join(lines[max(0, error_idx - 40): error_idx + 40])[-6000:]
    return t


def analyze_with_gemma(t: CITriage, provider: GemmaProvider, modified_files: list[str]) -> CIFailureAnalysis:
    prompt = (f"Deterministic triage: {t.classification}; failing tests {t.failing_tests}; env signals "
              f"{t.environment_signals}; files modified by the PR: {modified_files}.\n\n"
              f"{untrusted('ci-log', t.excerpt)}\n\nClassify the failure (code, test, environment, flaky, unknown), "
              "name the failed step, the related files, and the smallest correction. Do not propose shell commands "
              "that change CI configuration or credentials.")
    return provider.generate_json("ci_analysis", prompt, CIFailureAnalysis, max_output_tokens=1200)


def rerun_locally(repo: Path, base_commit: str, patch: str, tests: list[str], sandbox: TestSandbox) -> dict:
    with Worktree(repo, base_commit, "ci") as wt:
        ok, out = wt.apply_diff(patch) if patch else (True, "")
        if not ok:
            return {"ran": False, "reason": f"patch does not apply: {out[-200:]}"}
        targets = [t for t in tests if (wt.path / t.split("::")[0]).exists()] or []
        res = sandbox.run_pytest(wt.path, [*targets, "-q", "--tb=short", "--no-header"])
        return {"ran": res.runner != "blocked", "targets": targets or ["<full suite>"], "exit_code": res.exit_code,
                "runner": res.runner, "output_tail": tail(res.output, 40)}
