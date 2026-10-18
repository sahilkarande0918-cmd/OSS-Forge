"""Patch Guard: isolated git worktrees and validated, minimal application of model-proposed edits.

Rules enforced on every proposal:
  * edits only files the model was shown (allowed scope) and never existing tests, manifests, CI or secrets
  * each `search` block must match exactly once; edits are text replacements, never shell commands
  * bounded size (files / changed lines); edited Python must still parse
  * the human's checkout is never modified: everything happens in a throwaway worktree
"""
from __future__ import annotations

import ast
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from oss_forge.ai.schemas import PatchProposal
from oss_forge.security.execution_policy import PolicyViolation, run_command, safe_path

PROTECTED = re.compile(r"(^|/)(\.github/|\.gitlab-ci|setup\.py$|setup\.cfg$|pyproject\.toml$|package(-lock)?\.json$|"
                       r"requirements[^/]*\.txt$|tox\.ini$|noxfile\.py$|conftest\.py$|Makefile$|Dockerfile$)")
TEST_FILE = re.compile(r"(^|/)(tests?/|__tests__/)|(^|/)test_[^/]+\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$")
CRLF, LF = "\r\n", "\n"


class Worktree:
    """A detached `git worktree` at a fixed commit, created outside the user's checkout."""

    def __init__(self, repo: Path, commit: str, label: str = "wt"):
        self.repo, self.commit = repo.resolve(), commit
        self.path = Path(tempfile.mkdtemp(prefix=f"oss-forge-{label}-"))
        r = run_command(["git", "worktree", "add", "--detach", "--force", str(self.path), commit], self.repo, timeout=120)
        if not r.ok:
            shutil.rmtree(self.path, ignore_errors=True)
            raise RuntimeError(f"git worktree add failed: {r.output[-500:]}")

    def git(self, *args: str, timeout: int = 60):
        return run_command(["git", *args], self.path, timeout=timeout)

    def changed_files(self) -> list[str]:
        out = self.git("status", "--porcelain", "--untracked-files=all").stdout
        return sorted(line[3:].strip().strip('"') for line in out.splitlines() if line.strip())

    def diff(self, paths: list[str] | None = None) -> str:
        self.git("add", "-N", ".")  # include new files in the diff without staging content
        return self.git("diff", "--no-color", "--", *(paths or [])).stdout

    def numstat(self) -> dict[str, tuple[int, int]]:
        self.git("add", "-N", ".")
        stats = {}
        for line in self.git("diff", "--numstat").stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0].isdigit():
                stats[parts[2]] = (int(parts[0]), int(parts[1]))
        return stats

    def restore(self, paths: list[str]) -> None:
        if paths:
            self.git("checkout", "--", *paths)

    def apply_diff(self, diff_text: str) -> tuple[bool, str]:
        patch_file = self.path / ".oss-forge.patch"
        patch_file.write_bytes(diff_text.encode("utf-8"))
        r = self.git("apply", "--whitespace=nowarn", str(patch_file))
        patch_file.unlink(missing_ok=True)
        return r.ok, r.output

    def remove(self) -> None:
        run_command(["git", "worktree", "remove", "--force", str(self.path)], self.repo, timeout=120)
        shutil.rmtree(self.path, ignore_errors=True)
        run_command(["git", "worktree", "prune"], self.repo)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.remove()


@dataclass
class PatchResult:
    applied: bool
    violations: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0
    diff: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def find_unique(text: str, search: str) -> tuple[int, int] | None:
    """Exact unique match, else a unique line-wise match ignoring trailing whitespace. Both use LF endings."""
    search = search.replace(CRLF, LF)
    if search and text.count(search) == 1:
        start = text.index(search)
        return start, start + len(search)
    lines = text.split(LF)
    want = search.strip(LF).split(LF)
    if not any(w.strip() for w in want):
        return None
    hits = [i for i in range(len(lines) - len(want) + 1)
            if all(lines[i + j].rstrip() == want[j].rstrip() for j in range(len(want)))]
    if len(hits) != 1:
        return None
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    return offsets[hits[0]], offsets[hits[0] + len(want)] - 1


class PatchGuard:
    def __init__(self, allowed_files: set[str], max_files: int = 3, max_changed_lines: int = 80,
                 allow_test_files: set[str] | None = None):
        self.allowed = {f.replace("\\", "/") for f in allowed_files}
        self.max_files, self.max_changed_lines = max_files, max_changed_lines
        self.allow_test_files = allow_test_files or set()

    def check_scope(self, proposal: PatchProposal) -> list[str]:
        problems = []
        files = {e.file.replace("\\", "/").lstrip("./") for e in proposal.edits}
        if not proposal.edits:
            problems.append("proposal contains no edits")
        if len(files) > self.max_files:
            problems.append(f"touches {len(files)} files (limit {self.max_files}); keep the fix minimal")
        for f in sorted(files):
            if f in self.allow_test_files:
                continue
            if TEST_FILE.search(f):
                problems.append(f"{f}: editing existing tests is not allowed (fix the code, not the test)")
            elif PROTECTED.search(f):
                problems.append(f"{f}: build/CI/dependency files are protected")
            elif f not in self.allowed:
                problems.append(f"{f}: outside the allowed scope (files shown to the model: {sorted(self.allowed)})")
        for e in proposal.edits:
            if e.search == e.replace:
                problems.append(f"{e.file}: no-op edit")
        return problems

    def apply(self, wt: Worktree, proposal: PatchProposal) -> PatchResult:
        violations = self.check_scope(proposal)
        if violations:
            return PatchResult(False, violations)
        uses_crlf: dict[str, bool] = {}
        new_texts: dict[str, str] = {}
        for e in proposal.edits:
            rel = e.file.replace("\\", "/").lstrip("./")
            try:
                path = safe_path(wt.path, rel)
            except PolicyViolation as exc:
                return PatchResult(False, [str(exc)])
            if not path.is_file():
                return PatchResult(False, [f"{rel}: file does not exist"])
            if rel not in new_texts:
                raw = path.read_bytes().decode("utf-8")
                uses_crlf[rel] = CRLF in raw
                new_texts[rel] = raw.replace(CRLF, LF)
            span = find_unique(new_texts[rel], e.search)
            if span is None:
                count = new_texts[rel].count(e.search.replace(CRLF, LF))
                return PatchResult(False, [f"{rel}: search block matched {count} times (must match exactly once). "
                                           "Copy the exact current text, including indentation."])
            new_texts[rel] = new_texts[rel][:span[0]] + e.replace.replace(CRLF, LF) + new_texts[rel][span[1]:]
        for rel, text in new_texts.items():
            if rel.endswith(".py"):
                try:
                    ast.parse(text)
                except SyntaxError as exc:
                    return PatchResult(False, [f"{rel}: edit produces invalid Python: {exc.msg} (line {exc.lineno})"])
        for rel, text in new_texts.items():
            (wt.path / rel).write_bytes((text.replace(LF, CRLF) if uses_crlf[rel] else text).encode("utf-8"))
        stats = wt.numstat()
        touched = [stats.get(rel, (0, 0)) for rel in new_texts]
        added, removed = sum(a for a, _ in touched), sum(r for _, r in touched)
        if added + removed > self.max_changed_lines:
            wt.restore(list(new_texts))
            return PatchResult(False, [f"patch changes {added + removed} lines (limit {self.max_changed_lines}); "
                                       "propose a smaller, targeted fix"])
        return PatchResult(True, [], sorted(new_texts), added, removed, wt.diff(sorted(new_texts)))


def scope_from_context(files: list[str]) -> set[str]:
    return {f for f in files if not TEST_FILE.search(f) and not PROTECTED.search(f)}


def is_test_path(path: str) -> bool:
    return bool(TEST_FILE.search(PurePosixPath(path).as_posix()))
