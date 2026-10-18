"""PR builder: review-ready PR package from a capsule; optional draft PR creation after explicit approval.

Never pushes to a repository other than the target's own `origin`, never force-pushes, never merges.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from oss_forge.analysis.repo_map import read_text
from oss_forge.security.execution_policy import run_command
from oss_forge.workflows.patch_guard import Worktree

TEMPLATE_PATHS = (".github/PULL_REQUEST_TEMPLATE.md", ".github/pull_request_template.md", "PULL_REQUEST_TEMPLATE.md",
                  "docs/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE/default.md")


@dataclass
class PRPackage:
    title: str
    body: str
    branch: str
    commit_message: str
    commands: list[str]
    template_used: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _title_style(repo: Path) -> str | None:
    """Detect an explicit PR-title convention such as `Fix: <summary> (#issue)` in contribution docs."""
    for doc in ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md"):
        text = read_text(repo, doc) or ""
        m = re.search(r"`([A-Za-z]+(?:\([\w-]+\))?:\s*<[^>]+>[^`]*)`\s*as the PR title|PR title[^`]*`([^`]+)`", text, re.I)
        if m:
            return m.group(1) or m.group(2)
    return None


def build_pr(capsule: dict, repo: Path, run_dir: Path) -> PRPackage:
    issue = capsule["issue"]
    number = issue.get("number")
    summary = (capsule.get("issue_summary") or issue.get("title") or "fix reported issue").rstrip(".")
    summary = summary[0].lower() + summary[1:] if summary else summary
    style = _title_style(repo)
    if style and "<" in style:
        title = re.sub(r"<[^>]+>", summary, style, count=1)
        title = re.sub(r"\s*\(#\s*<?issue>?\)", f" (#{number})" if number else "", title)
        title = title.replace("<issue>", str(number or "")).strip()
    else:
        title = f"Fix: {summary}" + (f" (#{number})" if number else "")
    files = capsule.get("changed_files", [])
    status = capsule["verification_status"]
    ev = {t["label"]: t for t in capsule.get("test_evidence", [])}

    def ev_line(label: str, text: str) -> str:
        t = ev.get(label)
        return f"- {text}: exit {t['exit_code']}, {t['passed']} passed / {t['failed']} failed ({t['runner']})" if t else ""

    body = [
        "## Problem", issue.get("title", ""), "",
        "## Root cause", capsule.get("root_cause") or "_See hypotheses in the attached capsule._", "",
        "## Changes"] + [f"- `{f['file']}` (+{f['added']}/-{f['removed']})" for f in files] + [
        "", "## Tests",
        f"- Added regression test `{capsule['regression_test']['path']}`" if capsule.get("regression_test") else "",
        ev_line("regression_before", "Regression test on base commit (expected to fail)"),
        ev_line("regression_after", "Regression test with this patch"),
        ev_line("suite_after", "Existing test suite with this patch"),
        "", "## Verification", f"**{status}** — {capsule.get('verification_summary', '')}",
        f"Base commit `{(capsule.get('base_commit') or '')[:12]}`; evidence in the OSS Forge Proof-of-Fix capsule.",
        "", "## Related issue", (f"Fixes #{number}" if number and status == "VERIFIED" else
                                 f"Refs #{number}" if number else issue.get("url") or "n/a"),
        "", "## Known limitations"] + [f"- {n}" for n in capsule.get("limitations", [])]
    template = ""
    for t in TEMPLATE_PATHS:
        text = read_text(repo, t)
        if text:
            template = t
            body += ["", "---", f"<!-- repository PR template ({t}) -->", text.strip()]
            break
    branch = f"oss-forge/{(f'issue-{number}-' if number else '')}{capsule['run_id'][-24:]}".lower()
    branch = re.sub(r"[^a-z0-9/_-]+", "-", branch)
    commit_message = f"{title}\n\n{capsule.get('root_cause', '')[:500]}".strip()
    patch_file = (run_dir / "patch.diff").as_posix()
    test_path = capsule["regression_test"]["path"] if capsule.get("regression_test") else ""
    commands = [
        f"git switch -c {branch} {capsule.get('base_commit')}",
        f"git apply {patch_file}",
    ]
    if test_path:
        commands.append(f"# copy {run_dir.as_posix()}/regression_test.py to {test_path}")
    commands += [f"git add -A && git commit -m \"{title}\"", f"git push -u origin {branch}",
                 f"gh pr create --draft --title \"{title}\" --body-file {(run_dir / 'pr.md').as_posix()}"]
    return PRPackage(title, "\n".join(line for line in body if line is not None), branch, commit_message, commands, template)


def create_draft_pr(repo: Path, capsule: dict, pkg: PRPackage, run_dir: Path) -> dict:
    """Commit patch + regression test on a new branch (separate worktree), push to origin, open a DRAFT PR.

    Callers must obtain explicit human approval first; the CLI requires --approve.
    """
    remote = run_command(["git", "config", "--get", "remote.origin.url"], repo).stdout.strip()
    if "github.com" not in remote:
        return {"created": False, "reason": "origin is not a GitHub remote"}
    if capsule["verification_status"] not in ("VERIFIED", "PARTIALLY_VERIFIED"):
        return {"created": False, "reason": f"refusing to open a PR for status {capsule['verification_status']}"}
    if run_command(["gh", "auth", "status"], repo, keep_credentials=True).exit_code != 0:
        return {"created": False, "reason": "gh CLI is not authenticated"}
    with Worktree(repo, capsule["base_commit"], "pr") as wt:
        steps = [wt.git("switch", "-c", pkg.branch)]
        ok, out = wt.apply_diff((run_dir / "patch.diff").read_text(encoding="utf-8"))
        if not ok:
            return {"created": False, "reason": f"patch does not apply: {out[-300:]}"}
        if capsule.get("regression_test"):
            dest = wt.path / capsule["regression_test"]["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(capsule["regression_test"]["code"].encode("utf-8"))
        steps.append(wt.git("add", "-A"))
        steps.append(wt.git("commit", "-m", pkg.commit_message))
        push = run_command(["git", "push", "-u", "origin", pkg.branch], wt.path, timeout=180, keep_credentials=True)
        if not push.ok:
            return {"created": False, "reason": f"push failed: {push.output[-400:]}"}
        body_file = run_dir / "pr.md"
        pr = run_command(["gh", "pr", "create", "--draft", "--title", pkg.title, "--body-file", str(body_file),
                          "--head", pkg.branch], wt.path, timeout=120, keep_credentials=True)
        if not pr.ok:
            return {"created": False, "reason": f"gh pr create failed: {pr.output[-400:]}", "branch_pushed": pkg.branch}
        return {"created": True, "url": pr.stdout.strip().splitlines()[-1], "branch": pkg.branch,
                "commit": wt.git("rev-parse", "HEAD").stdout.strip()}
