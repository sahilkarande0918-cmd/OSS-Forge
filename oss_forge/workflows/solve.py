"""Run orchestration: map -> issue -> context -> Gemma diagnosis -> reproduction -> guarded patch attempts.

Every intermediate artifact is written to the run directory so each claim in the final report can be traced
back to a file produced by actual execution.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from oss_forge.ai.gemma_provider import GemmaProvider, untrusted
from oss_forge.ai.schemas import Diagnosis, IssueInterpretation, PatchProposal, RegressionTest
from oss_forge.analysis.context_engine import ContextEngine, ContextPack, IssueSignals
from oss_forge.analysis.repo_map import RepoMap, build_repo_map
from oss_forge.config import Config
from oss_forge.security.execution_policy import TestSandbox
from oss_forge.workflows import reproduce as repro
from oss_forge.workflows.issue_detective import (Issue, conventions_excerpt, diagnose_issue, extract_facts,
                                                 interpret_issue, load_issue)
from oss_forge.workflows.patch_guard import PatchGuard, PatchResult, Worktree, scope_from_context


def slugify(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n] or "issue"


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def create(cls, home: Path, title: str) -> "RunStore":
        run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{slugify(title, 30)}"
        return cls(home / "runs" / run_id)

    @classmethod
    def open(cls, home: Path, run_id: str) -> "RunStore":
        root = home / "runs" / run_id
        if not root.is_dir():
            raise FileNotFoundError(f"run not found: {root}")
        return cls(root)

    @property
    def run_id(self) -> str:
        return self.root.name

    def write_json(self, name: str, data) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return path

    def write_text(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def read_json(self, name: str):
        return json.loads((self.root / name).read_text(encoding="utf-8"))


@dataclass
class PatchAttempt:
    number: int
    proposal: dict
    guard: dict
    regression_exit_code: int | None = None
    regression_passed: bool = False
    feedback: str = ""


@dataclass
class Forge:
    config: Config
    repo: Path
    issue_spec: str
    provider: GemmaProvider | None = None
    allow_host_exec: bool = False
    store: RunStore | None = None
    log: list[str] = field(default_factory=list)

    # state
    rm: RepoMap | None = None
    issue: Issue | None = None
    engine: ContextEngine | None = None
    pack: ContextPack | None = None
    interpretation: IssueInterpretation | None = None
    diagnosis: Diagnosis | None = None
    repro_result: repro.ReproResult | None = None
    attempts: list[PatchAttempt] = field(default_factory=list)
    final_patch: PatchResult | None = None
    final_proposal: PatchProposal | None = None
    timings: dict = field(default_factory=dict)

    def __post_init__(self):
        self.repo = self.repo.resolve()
        self.provider = self.provider or GemmaProvider(self.config)
        self.sandbox = TestSandbox(self.config.sandbox, self.allow_host_exec)

    def say(self, msg: str) -> None:
        self.log.append(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}")

    def _timed(self, name: str, start: float) -> None:
        self.timings[name] = round(time.perf_counter() - start, 3)

    # ---------------------------------------------------------------------------------- analyse
    def analyze(self) -> None:
        t = time.perf_counter()
        self.rm = build_repo_map(self.repo, cache_dir=self.config.home / "cache")
        self._timed("repo_map", t)
        self.issue = load_issue(self.issue_spec, self.rm)
        self.store = self.store or RunStore.create(self.config.home, self.issue.title)
        self.say(f"repo map: {len(self.rm.files)} files, cache {'hit' if self.rm.cache_hit else 'miss'}")
        self.store.write_text("repo_map.md", self.rm.to_markdown())
        self.store.write_json("issue.json", {**self.issue.to_dict(), "facts": extract_facts(self.issue).__dict__})
        self.engine = ContextEngine(self.rm)

        t = time.perf_counter()
        self.interpretation = interpret_issue(self.issue, self.rm, self.engine, self.provider)
        self._timed("interpret", t)
        self.store.write_json("interpretation.json", self.interpretation.model_dump())
        self.say(f"interpretation: clarity={self.interpretation.clarity}; suspects={self.interpretation.suspected_symbols}")

        signals = IssueSignals.from_text(self.issue.text, self.interpretation.keywords,
                                         self.interpretation.suspected_symbols, self.interpretation.suspected_files)
        budget = max(1000, int(self.config.max_context_tokens * 0.55))  # leave room for issue, instructions, output
        t = time.perf_counter()
        self.pack = self.engine.build(signals, budget)
        self._timed("context", t)
        self.say(f"context: {len(self.pack.items)} definitions from {len(self.pack.files())} files, "
                 f"{self.pack.tokens} tokens (repo ~{self.rm.total_tokens_estimate})")

        t = time.perf_counter()
        self.diagnosis = diagnose_issue(self.issue, self.interpretation, self.rm, self.engine, self.pack, self.provider)
        self._timed("diagnose", t)
        self.store.write_json("context.json", self.pack.to_dict())
        self.store.write_json("diagnosis.json", self.diagnosis.model_dump())
        if self.diagnosis.regression_test:
            self.store.write_text("regression_test.py", self.diagnosis.regression_test.code)
        self.say(f"diagnosis: {len(self.diagnosis.hypotheses)} hypotheses; reproducible={self.diagnosis.reproducible}")

    # ------------------------------------------------------------------------------- reproduce
    def reproduce(self, wt: Worktree) -> repro.ReproResult:
        test = self.diagnosis.regression_test if self.diagnosis else None
        if not test or not self.diagnosis.reproducible:
            self.repro_result = repro.ReproResult(repro.INCONCLUSIVE, "model reported the issue is not reproducible "
                                                  f"from the information given: {self.diagnosis.reason_if_not_reproducible}",
                                                  "")
            return self.repro_result
        expected = IssueSignals.from_text(self.issue.text).exceptions
        result = repro.reproduce(wt.path, test, self.sandbox, expected)
        if result.status == repro.INCONCLUSIVE and self.provider.ledger.api_calls < self.config.max_api_calls - 2:
            # One focused regeneration: the test itself was broken (not the code under test).
            self.say(f"regression test inconclusive ({result.reason}); asking Gemma for a corrected test")
            (wt.path / result.test_file).unlink(missing_ok=True) if result.test_file else None
            self.diagnosis = diagnose_issue(self.issue, self.interpretation, self.rm, self.engine, self.pack,
                                            self.provider, max_expansion_rounds=0,
                                            feedback=f"{result.reason}\n{result.output_tail[-1500:]}")
            self.store.write_json("diagnosis.json", self.diagnosis.model_dump())
            if self.diagnosis.regression_test and self.diagnosis.reproducible:
                self.store.write_text("regression_test.py", self.diagnosis.regression_test.code)
                result = repro.reproduce(wt.path, self.diagnosis.regression_test, self.sandbox, expected)
        self.repro_result = result
        self.store.write_json("reproduction.json", result.to_dict())
        self.say(f"reproduction: {result.status} ({result.reason})")
        return result

    # ----------------------------------------------------------------------------------- patch
    def _patch_prompt(self) -> str:
        hyps = "\n".join(f"- [{h.confidence:.2f}] {h.file}::{h.symbol}: {h.explanation}"
                         for h in (self.diagnosis.hypotheses if self.diagnosis else []))
        test = self.diagnosis.regression_test
        evidence = (f"Regression test ({test.test_file}) fails on the original code:\n```python\n{test.code}\n```\n"
                    f"Failure output:\n```\n{self.repro_result.output_tail[-1500:]}\n```") if test and self.repro_result else ""
        allowed = sorted(scope_from_context(self.pack.files()))
        return (f"Issue summary: {self.interpretation.summary}\nExpected: {self.interpretation.expected_behavior}\n"
                f"Actual: {self.interpretation.actual_behavior}\n\nRoot-cause hypotheses:\n{hyps}\n\n{evidence}\n\n"
                f"Conventions: {conventions_excerpt(self.rm, 300)}\nAllowed files: {allowed}\n\n"
                f"Relevant code:\n{self.pack.render()}\n\n{untrusted('issue', self.issue.text)}\n\n"
                "Propose the smallest correct fix as search/replace edits. `search` must be copied verbatim from the "
                "code above (exact indentation, unique, a few lines). Do not edit tests, build files or files outside "
                "the allowed list. Do not refactor or reformat unrelated code. Fix the root cause so the regression "
                "test passes without breaking existing behaviour.")

    def _feedback_prompt(self, last: PatchProposal, feedback: str) -> str:
        """Retry prompt: only the previous edits, the focused error, and the touched definitions."""
        touched = {e.file for e in last.edits}
        code = "\n\n".join(i.render() for i in self.pack.items if i.file in touched) or self.pack.render()
        test = self.diagnosis.regression_test
        return (f"Issue summary: {self.interpretation.summary}\n\nYour previous patch:\n{last.model_dump_json()}\n\n"
                f"It was rejected/failed:\n```\n{feedback[-2500:]}\n```\n\n"
                + (f"Regression test:\n```python\n{test.code}\n```\n\n" if test else "")
                + f"Current code of the touched files:\n{code}\n\nAllowed files: {sorted(scope_from_context(self.pack.files()))}\n"
                "Return a corrected minimal patch (search blocks copied verbatim from the ORIGINAL code shown).")

    def patch_loop(self, wt: Worktree) -> PatchResult | None:
        guard = PatchGuard(scope_from_context(self.pack.files()))
        test_rel = self.repro_result.test_file if self.repro_result else ""
        proposal: PatchProposal | None = None
        feedback = ""
        for n in range(1, self.config.max_patch_attempts + 1):
            if self.provider.ledger.api_calls >= self.config.max_api_calls:
                self.say("API call budget exhausted; stopping patch attempts")
                break
            prompt = self._patch_prompt() if proposal is None else self._feedback_prompt(proposal, feedback)
            proposal = self.provider.generate_json(f"patch_attempt_{n}", prompt, PatchProposal, max_output_tokens=3000)
            for req in proposal.need_more_context[:2]:  # progressive expansion also allowed while patching
                if self.engine.expand(self.pack, req.symbol, req.reason, req.file):
                    guard.allowed |= scope_from_context(self.pack.files())
            result = guard.apply(wt, proposal)
            attempt = PatchAttempt(n, proposal.model_dump(), result.to_dict())
            self.attempts.append(attempt)
            if not result.applied:
                feedback = "Patch rejected by guard:\n" + "\n".join(result.violations)
                attempt.feedback = feedback
                self.say(f"attempt {n}: rejected ({'; '.join(result.violations)[:200]})")
                continue
            run = repro.run_regression_test(wt.path, test_rel, self.sandbox) if test_rel else None
            attempt.regression_exit_code = run.exit_code if run else None
            attempt.regression_passed = bool(run and run.ok)
            if attempt.regression_passed:
                self.final_patch, self.final_proposal = result, proposal
                self.store.write_text("patch.diff", result.diff)
                self.say(f"attempt {n}: applied {result.changed_files} (+{result.lines_added}/-{result.lines_removed}); "
                         "regression test passes")
                break
            feedback = "Regression test still fails after the patch:\n" + (run.output[-2500:] if run else "no test")
            attempt.feedback = feedback
            self.say(f"attempt {n}: applied but regression test still fails")
            wt.restore(result.changed_files)
        self.store.write_json("attempts.json", [a.__dict__ for a in self.attempts])
        return self.final_patch

    # ----------------------------------------------------------------------------------- solve
    def solve(self) -> dict:
        """Full workflow. Returns the capsule dict. Never touches the user's checkout."""
        from oss_forge.workflows.verify_fix import verify_fix
        self.analyze()
        if not self.rm.head:
            raise RuntimeError("target must be a git repository with at least one commit")
        dirty = [f for f in _git_lines(self.repo, "status", "--porcelain") if f]
        if dirty:
            self.say(f"note: {len(dirty)} uncommitted change(s) in the target checkout are left untouched and are "
                     "NOT part of the analysed base commit")
        status, summary = None, ""
        with Worktree(self.repo, self.rm.head, "solve") as wt:
            if self.config.dry_run:
                proposal = self.provider.generate_json("patch_proposal_dry_run", self._patch_prompt(), PatchProposal,
                                                       max_output_tokens=3000)
                result = PatchGuard(scope_from_context(self.pack.files())).apply(wt, proposal)
                self.attempts.append(PatchAttempt(1, proposal.model_dump(), result.to_dict()))
                if result.applied:
                    self.final_patch, self.final_proposal = result, proposal
                    self.store.write_text("patch.diff", result.diff)
                status = "INCONCLUSIVE"
                summary = ("dry run: no repository code was executed; the proposed patch is UNVERIFIED"
                           + ("" if result.applied else f" and was rejected by the patch guard ({result.violations[0]})"))
            else:
                r = self.reproduce(wt)
                if r.status == repro.REPRODUCED:
                    self.patch_loop(wt)
                    if not self.final_patch:
                        status, summary = "FAILED", ("bug reproduced, but no patch passed the regression test within "
                                                     f"{len(self.attempts)} attempt(s)")
                elif r.status == repro.ENVIRONMENT_BLOCKED:
                    status, summary = "BLOCKED", r.reason
                else:
                    status, summary = "INCONCLUSIVE", f"bug not confirmed ({r.status}): {r.reason}"
        verification = None
        if self.final_patch and not self.config.dry_run:
            test = self.diagnosis.regression_test
            self.say("independent verification in a fresh worktree")
            t = time.perf_counter()
            verification = verify_fix(self.repo, self.rm.head, self.final_patch.diff,
                                      self.repro_result.test_file if self.repro_result else None,
                                      test.code if test else None, self.sandbox)
            self._timed("verify", t)
            self.store.write_json("verification.json", verification.to_dict())
            self.say(f"verification: {verification.status} - {verification.summary}")
        meta = self._run_meta(dirty)
        self.store.write_json("run.json", meta)
        capsule = assemble_capsule(meta, verification.to_dict() if verification else None, status, summary)
        finalize_run(self.store, capsule, self.repo)
        return capsule

    def _run_meta(self, dirty: list[str]) -> dict:
        from oss_forge.workflows.capsule import now_iso
        test = self.diagnosis.regression_test if self.diagnosis else None
        test_path = (self.repro_result.test_file if self.repro_result and self.repro_result.test_file
                     else test.test_file if test else None)
        top = sorted(self.pack.items, key=lambda i: -i.score)[:8]
        locs = [{"file": i.file, "symbol": i.symbol, "start_line": i.start, "end_line": i.end,
                 "reason": "; ".join(i.reasons[:2])} for i in top]
        issue = self.issue.to_dict()
        issue["body"] = issue["body"][:4000]
        issue.pop("comments", None)
        root_cause = self.final_proposal.root_cause if self.final_proposal else (
            self.diagnosis.hypotheses[0].explanation if self.diagnosis and self.diagnosis.hypotheses else "")
        return {
            "run_id": self.store.run_id, "created_at": now_iso(), "repo_path": str(self.repo),
            "repository": {"id": self.rm.repo_id, "remote": self.rm.remote, "name": self.repo.name,
                           "branch": self.rm.branch},
            "base_commit": self.rm.head, "dirty_files_ignored": dirty[:50], "issue": issue,
            "issue_summary": self.interpretation.summary if self.interpretation else "",
            "dry_run": self.config.dry_run,
            "reproduction": self.repro_result.to_dict() if self.repro_result else None,
            "root_cause": root_cause,
            "hypotheses": [h.model_dump() for h in self.diagnosis.hypotheses] if self.diagnosis else [],
            "source_locations": locs,
            "regression_test": {"path": test_path, "code": test.code} if test and test_path else None,
            "patch": self.final_patch.diff if self.final_patch else "",
            "changed_files": diff_file_stats(self.final_patch.diff) if self.final_patch else [],
            "patch_attempts": len(self.attempts), "model": self.config.model, "usage": self.provider.usage(),
            "context": {**self.pack.metrics, "expansions": len(self.pack.expansions)},
            "timings": self.timings, "log": self.log,
        }


def _git_lines(repo: Path, *args: str) -> list[str]:
    from oss_forge.security.execution_policy import run_command
    return run_command(["git", *args], repo).stdout.splitlines()


def diff_file_stats(diff: str) -> list[dict]:
    stats: dict[str, dict] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            name = line[6:].strip()
            current = stats.setdefault(name, {"file": name, "added": 0, "removed": 0})
        elif current and line.startswith("+") and not line.startswith("+++"):
            current["added"] += 1
        elif current and line.startswith("-") and not line.startswith("---"):
            current["removed"] += 1
    return list(stats.values())


def assemble_capsule(meta: dict, verification: dict | None, fallback_status: str | None, fallback_summary: str) -> dict:
    from oss_forge.workflows.capsule import ProofOfFixCapsule, build_limitations
    status = verification["status"] if verification else (fallback_status or "INCONCLUSIVE")
    summary = verification["summary"] if verification else fallback_summary
    sandbox = verification["sandbox"] if verification else ""
    evidence = []
    r = meta.get("reproduction")
    if r and r.get("command"):
        evidence.append({"label": "reproduction", "command": r["command"], "exit_code": r["exit_code"],
                         "duration_s": r["duration_s"], "runner": r["runner"], "output_tail": r["output_tail"]})
    for run in (verification or {}).get("runs", []):
        evidence.append({k: run[k] for k in ("label", "command", "exit_code", "duration_s", "runner", "passed",
                                             "failed", "errors", "skipped", "output_tail")})
    limitations = build_limitations(status, verification, sandbox)
    if meta.get("dirty_files_ignored"):
        limitations.append("The target checkout had uncommitted changes; they were not part of the analysed commit.")
    test_path = (meta.get("regression_test") or {}).get("path")
    run_dir = f"<OSS_FORGE_HOME>/runs/{meta['run_id']}"
    commands = [f"python -m oss_forge verify --run-id {meta['run_id']} --repo <path-to-repo> --execute",
                "# or manually:", f"git worktree add --detach ../verify {meta.get('base_commit')}"]
    if test_path:
        commands += [f"cp {run_dir}/regression_test.py ../verify/{test_path}",
                     f"cd ../verify && python -m pytest {test_path}   # expected: FAIL (bug present)"]
    if meta.get("patch"):
        commands += [f"git apply {run_dir}/patch.diff", "python -m pytest   # expected: PASS"]
    capsule = ProofOfFixCapsule(
        created_at=meta["created_at"], run_id=meta["run_id"], repository=meta["repository"],
        base_commit=meta.get("base_commit"), issue=meta["issue"], verification_status=status,
        verification_summary=summary, verification_checks=(verification or {}).get("checks", {}),
        reproduction=r, root_cause=meta.get("root_cause", ""), hypotheses=meta.get("hypotheses", []),
        source_locations=meta.get("source_locations", []), regression_test=meta.get("regression_test"),
        patch=meta.get("patch", ""), changed_files=meta.get("changed_files", []), test_evidence=evidence,
        new_failures=(verification or {}).get("new_failures", []),
        preexisting_failures=(verification or {}).get("preexisting_failures", []),
        patch_attempts=meta.get("patch_attempts", 0), model=meta["model"], usage=meta.get("usage", {}),
        context=meta.get("context", {}), sandbox=sandbox, limitations=limitations, reproduction_commands=commands)
    data = capsule.model_dump()
    data["issue_summary"] = meta.get("issue_summary", "")
    return data


def finalize_run(store: RunStore, capsule: dict, repo: Path) -> None:
    from oss_forge.workflows.capsule import ProofOfFixCapsule, write_capsule
    from oss_forge.workflows.pr_builder import build_pr
    write_capsule(ProofOfFixCapsule.model_validate({k: v for k, v in capsule.items() if k != "issue_summary"}),
                  store.root)
    pkg = build_pr(capsule, repo, store.root)
    store.write_text("pr.md", pkg.body)
    store.write_json("pr.json", pkg.to_dict())


def verify_run(config: Config, run_id: str, repo: Path | None = None, allow_host_exec: bool = False) -> dict:
    """Re-run proof-of-fix verification for a stored run and refresh its capsule."""
    from oss_forge.workflows.verify_fix import verify_fix
    store = RunStore.open(config.home, run_id)
    meta = store.read_json("run.json")
    repo = (repo or Path(meta["repo_path"])).resolve()
    if not meta.get("patch"):
        raise RuntimeError("this run produced no patch to verify")
    test = meta.get("regression_test") or {}
    verification = verify_fix(repo, meta["base_commit"], meta["patch"], test.get("path"), test.get("code"),
                              TestSandbox(config.sandbox, allow_host_exec))
    store.write_json("verification.json", verification.to_dict())
    capsule = assemble_capsule(meta, verification.to_dict(), None, "")
    finalize_run(store, capsule, repo)
    return capsule
