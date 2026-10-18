"""Benchmark harness: OSS Forge vs a bounded-context Gemma 4 baseline on the same cases, model and limits.

Two suites of measurements:

* context (no API calls, fully reproducible): does the selected context contain the ground-truth definition,
  and how many tokens does it cost?  Compared against two naive packers using the same token budget:
  path-order truncation (dump files until full) and keyword-grep whole files.
* live (Gemma 4 API): end-to-end issue resolution. Baseline = same model, same budgets, same edit format and
  patch guard, but the prompt is the issue + files in path order truncated to the budget, no repository map,
  no reproduction, no verification loop. Every result is graded by a HIDDEN oracle test neither system sees.

Usage:
  python -m oss_forge benchmark --suite smoke --mode context
  python -m oss_forge benchmark --suite smoke --mode live --allow-host-exec      # needs GEMINI_API_KEY
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from oss_forge.ai.gemma_provider import GemmaProvider, ProviderError, estimate_tokens, untrusted
from oss_forge.ai.schemas import PatchProposal
from oss_forge.analysis.context_engine import IDENT, STOPWORDS, ContextEngine, IssueSignals
from oss_forge.analysis.repo_map import build_repo_map, read_text
from oss_forge.config import Config
from oss_forge.security.execution_policy import TestSandbox
from oss_forge.workflows.patch_guard import PatchGuard, Worktree, scope_from_context
from oss_forge.workflows.solve import Forge, diff_file_stats
from oss_forge.workflows.verify_fix import verify_fix

BENCH_DIR = Path(__file__).resolve().parent
CASES_FILE = BENCH_DIR / "cases" / "cases.json"


def load_cases(suite: str) -> list[dict]:
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    ids = data["suites"].get(suite)
    if ids is None:
        raise ValueError(f"unknown suite {suite!r}; available: {sorted(data['suites'])}")
    by_id = {c["id"]: c for c in data["cases"]}
    return [by_id[i] for i in ids]


def materialize(case: dict, dest_root: Path) -> Path:
    """Copy the case repository into a fresh git repo with a single commit (deterministic author/date)."""
    src = CASES_FILE.parent / case["repo"]
    dest = dest_root / case["id"]
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    env_args = ["-c", "user.email=bench@oss-forge.local", "-c", "user.name=bench", "-c", "core.autocrlf=false"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "benchmark base",
                                                                "--date", "2026-01-01T00:00:00"]):
        subprocess.run(["git", *env_args, *args], cwd=dest, check=True, capture_output=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00"})
    return dest


def head_of(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------------------------- context suite
def _naive_path_order(rm, budget: int) -> tuple[list[str], int]:
    files, used = [], 0
    for f in rm.files:
        if not f.endswith(".py") or "/tests/" in f"/{f}" or f.startswith("tests/"):
            continue
        cost = estimate_tokens(read_text(Path(rm.root), f) or "")
        if used + cost > budget:
            break
        files.append(f)
        used += cost
    return files, used


def _naive_grep(rm, issue: str, budget: int) -> tuple[list[str], int]:
    words = {w.lower() for w in IDENT.findall(issue) if w.lower() not in STOPWORDS}
    scored = []
    for f in rm.files:
        if not f.endswith(".py") or f.startswith("tests/"):
            continue
        text = (read_text(Path(rm.root), f) or "").lower()
        hits = sum(text.count(w) for w in words)
        if hits:
            scored.append((-hits, f, estimate_tokens(text)))
    files, used = [], 0
    for _, f, cost in sorted(scored):
        if used + cost <= budget:
            files.append(f)
            used += cost
    return files, used


def _contains(rm, files: list[str], case: dict) -> bool:
    """Ground-truth definition fully present (whole-file packers include every definition in a file)."""
    return all(f in files for f in case["ground_truth_files"])


def run_context_suite(cases: list[dict], budget: int, workdir: Path) -> list[dict]:
    rows = []
    for case in cases:
        if not case["ground_truth_symbols"]:
            continue
        repo = materialize(case, workdir)
        rm = build_repo_map(repo)
        pack = ContextEngine(rm).build(IssueSignals.from_text(case["issue"]), budget)
        selected_symbols = {i.symbol.split(".")[-1] for i in pack.items}
        rank = next((n for n, r in enumerate(pack.ranking, 1) if r["symbol"].split(".")[-1] in case["ground_truth_symbols"]), None)
        path_files, path_tokens = _naive_path_order(rm, budget)
        grep_files, grep_tokens = _naive_grep(rm, case["issue"], budget)
        rows.append({
            "case": case["id"], "budget": budget, "repo_tokens": rm.total_tokens_estimate,
            "oss_forge": {"hit": all(s in selected_symbols for s in case["ground_truth_symbols"]),
                          "rank_of_ground_truth": rank, "tokens": pack.tokens, "files": pack.files()},
            "path_order": {"hit": _contains(rm, path_files, case), "tokens": path_tokens, "files": path_files},
            "keyword_grep": {"hit": _contains(rm, grep_files, case), "tokens": grep_tokens, "files": grep_files},
        })
    return rows


# ------------------------------------------------------------------------------------------- live suite
@dataclass
class LiveResult:
    case: str
    system: str
    status: str = ""                 # OSS Forge verification status / baseline: PATCHED | NO_PATCH | ERROR
    reproduced: bool | None = None
    patched_files: list[str] = field(default_factory=list)
    localized: bool | None = None    # patched a ground-truth file
    oracle_pass: bool | None = None  # hidden test passes and no existing test regresses
    unrelated_files: list[str] = field(default_factory=list)
    false_claim: bool = False        # claimed VERIFIED but oracle fails
    abstained: bool | None = None    # for unclear cases: produced no patch / no success claim
    api_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0
    runtime_s: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def grade(repo: Path, case: dict, diff: str, sandbox: TestSandbox) -> tuple[bool | None, list[str]]:
    """Apply the patch to a fresh worktree, add the hidden oracle test, require oracle + existing suite to pass."""
    if not case.get("oracle") or not diff:
        return None, []
    oracle = (CASES_FILE.parent / case["oracle"]).read_text(encoding="utf-8")
    v = verify_fix(repo, head_of(repo), diff, "tests/test_hidden_oracle.py", oracle, sandbox)
    return bool(v.checks.get("regression_passes_after") and v.checks.get("no_new_failures")), v.new_failures


def run_baseline(cfg: Config, repo: Path, case: dict, budget: int) -> tuple[LiveResult, str]:
    res = LiveResult(case["id"], "baseline")
    provider = GemmaProvider(cfg)
    start = time.perf_counter()
    rm = build_repo_map(repo)
    files, used = _naive_path_order(rm, budget)
    code = "\n\n".join(f"### {f}\n```python\n{read_text(repo, f)}\n```" for f in files)
    res.context_tokens = used
    prompt = (f"Repository files (path order, truncated to budget):\n{code}\n\n{untrusted('issue', case['issue'])}\n\n"
              "Fix the issue with minimal search/replace edits. `search` must be copied verbatim from the files above.")
    guard = PatchGuard(scope_from_context(files))
    diff, feedback, proposal = "", "", None
    try:
        with Worktree(repo, head_of(repo), "baseline") as wt:
            for _ in range(cfg.max_patch_attempts):
                p = prompt if proposal is None else (f"{prompt}\n\nYour previous patch was rejected:\n{feedback}\n"
                                                     "Return a corrected patch.")
                proposal = provider.generate_json("baseline_patch", p, PatchProposal, max_output_tokens=3000)
                result = guard.apply(wt, proposal)
                if result.applied:
                    diff = result.diff
                    break
                feedback = "\n".join(result.violations)
    except ProviderError as exc:
        res.error = str(exc)[:300]
    res.status = "ERROR" if res.error else ("PATCHED" if diff else "NO_PATCH")
    u = provider.usage()
    res.api_calls, res.prompt_tokens, res.output_tokens = u["api_calls"], u["prompt_tokens"], u["output_tokens"]
    res.runtime_s = round(time.perf_counter() - start, 2)
    return res, diff


def run_oss_forge(cfg: Config, repo: Path, case: dict, allow_host_exec: bool) -> tuple[LiveResult, str]:
    res = LiveResult(case["id"], "oss-forge")
    start = time.perf_counter()
    forge = Forge(cfg, repo, case["issue"], allow_host_exec=allow_host_exec)
    diff = ""
    try:
        capsule = forge.solve()
        res.status = capsule["verification_status"]
        res.reproduced = (capsule.get("reproduction") or {}).get("status") == "REPRODUCED"
        diff = capsule["patch"]
        res.context_tokens = capsule["context"].get("context_tokens", 0)
    except Exception as exc:  # record, never hide
        res.status, res.error = "ERROR", f"{type(exc).__name__}: {exc}"[:300]
    u = forge.provider.usage()
    res.api_calls, res.prompt_tokens, res.output_tokens = u["api_calls"], u["prompt_tokens"], u["output_tokens"]
    res.runtime_s = round(time.perf_counter() - start, 2)
    return res, diff


def finish(res: LiveResult, case: dict, repo: Path, diff: str, sandbox: TestSandbox) -> LiveResult:
    res.patched_files = [f["file"] for f in diff_file_stats(diff)]
    if case["ground_truth_files"]:
        res.localized = bool(set(res.patched_files) & set(case["ground_truth_files"])) if diff else False
        res.unrelated_files = sorted(set(res.patched_files) - set(case["ground_truth_files"]))
    res.oracle_pass, _ = grade(repo, case, diff, sandbox)
    if case["expect"] == "abstain":
        res.abstained = not diff and res.status not in ("VERIFIED", "PARTIALLY_VERIFIED")
    res.false_claim = res.status == "VERIFIED" and res.oracle_pass is False
    return res


def run_live_suite(cfg: Config, cases: list[dict], budget: int, workdir: Path, allow_host_exec: bool,
                   systems=("baseline", "oss-forge")) -> list[dict]:
    sandbox = TestSandbox(cfg.sandbox, allow_host_exec)
    if sandbox.decide().runner == "blocked":
        raise RuntimeError(sandbox.decide().reason)
    cfg = cfg.with_overrides(max_context_tokens=max(cfg.max_context_tokens, budget * 2), dry_run=False,
                             home=workdir / "home")
    rows = []
    for case in cases:
        for system in systems:
            repo = materialize(case, workdir / system)
            if system == "baseline":
                res, diff = run_baseline(cfg, repo, case, budget)
            else:
                res, diff = run_oss_forge(cfg.with_overrides(max_context_tokens=int(budget / 0.55)), repo, case,
                                          allow_host_exec)
            rows.append(finish(res, case, repo, diff, sandbox).to_dict())
    return rows


# ---------------------------------------------------------------------------------------------- report
def summarize_live(rows: list[dict]) -> dict:
    out = {}
    for system in sorted({r["system"] for r in rows}):
        rs = [r for r in rows if r["system"] == system]
        fixable = [r for r in rs if r["localized"] is not None]
        out[system] = {
            "cases": len(rs),
            "localization": f"{sum(bool(r['localized']) for r in fixable)}/{len(fixable)}",
            "oracle_verified_fixes": f"{sum(bool(r['oracle_pass']) for r in fixable)}/{len(fixable)}",
            "reproduced": f"{sum(bool(r['reproduced']) for r in fixable)}/{len(fixable)}" if system == "oss-forge" else "n/a",
            "abstained_on_unclear": f"{sum(bool(r['abstained']) for r in rs if r['abstained'] is not None)}/"
                                    f"{sum(1 for r in rs if r['abstained'] is not None)}",
            "false_success_claims": sum(r["false_claim"] for r in rs),
            "unrelated_file_edits": sum(len(r["unrelated_files"]) for r in rs),
            "api_calls": sum(r["api_calls"] for r in rs),
            "prompt_tokens": sum(r["prompt_tokens"] for r in rs),
            "output_tokens": sum(r["output_tokens"] for r in rs),
            "runtime_s": round(sum(r["runtime_s"] for r in rs), 1),
        }
    return out


def to_markdown(report: dict) -> str:
    lines = [f"# Benchmark: suite `{report['suite']}` ({report['mode']})", "",
             f"- Date: {report['created_at']}", f"- Model: `{report.get('model', 'n/a')}`",
             f"- Context budget: {report['budget']} tokens", f"- Command: `{report['command']}`", ""]
    if report["mode"] == "context":
        lines += ["| case | repo tokens | OSS Forge hit (rank) / tokens | path-order hit / tokens | keyword-grep hit / tokens |",
                  "|---|---|---|---|---|"]
        for r in report["rows"]:
            o, p, g = r["oss_forge"], r["path_order"], r["keyword_grep"]
            lines.append(f"| {r['case']} | {r['repo_tokens']} | {'yes' if o['hit'] else 'no'} (#{o['rank_of_ground_truth']}) / "
                         f"{o['tokens']} | {'yes' if p['hit'] else 'no'} / {p['tokens']} | "
                         f"{'yes' if g['hit'] else 'no'} / {g['tokens']} |")
    else:
        lines += ["## Summary", "", "| metric | " + " | ".join(report["summary"]) + " |",
                  "|---|" + "---|" * len(report["summary"])]
        metrics = next(iter(report["summary"].values())).keys()
        for m in metrics:
            lines.append(f"| {m} | " + " | ".join(str(report["summary"][s][m]) for s in report["summary"]) + " |")
        lines += ["", "## Per case", "", "| case | system | status | localized | oracle pass | unrelated | calls | "
                  "prompt tok | output tok | context tok | s |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in report["rows"]:
            lines.append(f"| {r['case']} | {r['system']} | {r['status']} | {r['localized']} | {r['oracle_pass']} | "
                         f"{len(r['unrelated_files'])} | {r['api_calls']} | {r['prompt_tokens']} | {r['output_tokens']} | "
                         f"{r['context_tokens']} | {r['runtime_s']} |")
            if r["error"]:
                lines.append(f"|  | error: {r['error'][:120]} |||||||||")
    return "\n".join(lines) + "\n"


def run(cfg: Config, suite: str, mode: str, budget: int, allow_host_exec: bool, out_dir: Path | None = None,
        command: str = "") -> dict:
    cases = load_cases(suite)
    out_dir = out_dir or BENCH_DIR / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="oss-forge-bench-"))
    try:
        if mode == "context":
            rows = run_context_suite(cases, budget, workdir)
            report = {"rows": rows}
        else:
            rows = run_live_suite(cfg, cases, budget, workdir, allow_host_exec)
            report = {"rows": rows, "summary": summarize_live(rows), "model": cfg.model}
    finally:
        _cleanup(workdir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report.update(suite=suite, mode=mode, budget=budget, created_at=stamp, command=command)
    (out_dir / f"{stamp}-{suite}-{mode}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = to_markdown(report)
    (out_dir / f"{stamp}-{suite}-{mode}.md").write_text(md, encoding="utf-8")
    (out_dir / f"latest-{mode}.md").write_text(md, encoding="utf-8")
    report["markdown"] = md
    return report


def _cleanup(workdir: Path) -> None:
    for repo in workdir.glob("**/.git"):
        subprocess.run(["git", "worktree", "prune"], cwd=repo.parent, capture_output=True)
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    import sys

    from oss_forge.cli import main
    raise SystemExit(main(["benchmark", *sys.argv[1:]]))
