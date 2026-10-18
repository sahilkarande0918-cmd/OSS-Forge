"""Proof-of-Fix Capsule: a portable, schema-validated evidence package for one attempted contribution.

Every success claim points at execution output (exit codes, per-test outcomes, durations, commit SHAs).
Secrets are redacted; only the touched definitions (not whole files) are included as source locations.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from oss_forge import __version__
from oss_forge.security.execution_policy import redact

CAPSULE_SCHEMA_VERSION = "1.0"


class SourceLocation(BaseModel):
    file: str
    symbol: str
    start_line: int
    end_line: int
    reason: str = ""


class TestEvidence(BaseModel):
    label: str = Field(description="regression_before | regression_after | suite_before | suite_after | reproduction")
    command: list[str]
    exit_code: int | None
    duration_s: float
    runner: str = Field(description="docker | host | blocked")
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    output_tail: str = ""


class ProofOfFixCapsule(BaseModel):
    schema_version: str = CAPSULE_SCHEMA_VERSION
    tool: str = f"oss-forge {__version__}"
    created_at: str
    run_id: str
    repository: dict = Field(description="id, remote, path name, branch")
    base_commit: str | None
    issue: dict = Field(description="source, title, url, number, labels (body excluded if very long)")
    verification_status: str = Field(description="VERIFIED | PARTIALLY_VERIFIED | FAILED | INCONCLUSIVE | BLOCKED")
    verification_summary: str
    verification_checks: dict = Field(default_factory=dict)
    reproduction: dict | None = None
    root_cause: str = ""
    hypotheses: list[dict] = Field(default_factory=list)
    source_locations: list[SourceLocation] = Field(default_factory=list)
    regression_test: dict | None = Field(default=None, description="path and full code of the generated test")
    patch: str = Field(default="", description="unified diff, applies with `git apply` to base_commit")
    changed_files: list[dict] = Field(default_factory=list, description="file, lines added, lines removed")
    test_evidence: list[TestEvidence] = Field(default_factory=list)
    new_failures: list[str] = Field(default_factory=list)
    preexisting_failures: list[str] = Field(default_factory=list)
    patch_attempts: int = 0
    model: str
    usage: dict = Field(default_factory=dict, description="api_calls, tokens (as reported by the API), latency")
    context: dict = Field(default_factory=dict, description="context tokens vs repository size, files selected")
    sandbox: str = ""
    limitations: list[str] = Field(default_factory=list)
    reproduction_commands: list[str] = Field(default_factory=list)


def capsule_json_schema() -> dict:
    return ProofOfFixCapsule.model_json_schema()


def build_limitations(status: str, verification: dict | None, sandbox: str) -> list[str]:
    notes = ["A passing regression test shows the reported symptom is gone for the tested inputs; it does not prove "
             "the issue is fixed for every input or platform. Review the diff."]
    if status != "VERIFIED":
        notes.append(f"Verification status is {status}: do not present this patch as a confirmed fix.")
    if sandbox.startswith("host"):
        notes.append("Tests ran on the host (approved), not in a container; results depend on the local environment.")
    if verification and verification.get("preexisting_failures"):
        notes.append(f"{len(verification['preexisting_failures'])} existing test(s) were already failing before the "
                     "patch; they could mask regressions in the same area.")
    if verification and verification.get("whitespace_issues"):
        notes.append("`git diff --check` reported whitespace issues.")
    notes.append("Root-cause explanation and hypotheses are model-generated and unverified beyond the tests listed.")
    return notes


def write_capsule(capsule: ProofOfFixCapsule, run_dir: Path) -> tuple[Path, Path]:
    data = json.loads(redact(capsule.model_dump_json(indent=2)))
    ProofOfFixCapsule.model_validate(data)
    json_path = run_dir / "capsule.json"
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    md_path = run_dir / "capsule.md"
    md_path.write_bytes(redact(render_markdown(capsule)).encode("utf-8"))
    return json_path, md_path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def render_markdown(c: ProofOfFixCapsule) -> str:
    badge = {"VERIFIED": "✅", "PARTIALLY_VERIFIED": "🟡", "FAILED": "❌", "INCONCLUSIVE": "❔", "BLOCKED": "⛔"}.get(
        c.verification_status, "")
    lines = [f"# Proof-of-Fix Capsule — {c.issue.get('title', '')}", "",
             f"**Status: {badge} {c.verification_status}** — {c.verification_summary}", "",
             f"- Run: `{c.run_id}` ({c.created_at}) · {c.tool}",
             f"- Repository: `{c.repository.get('id')}` {c.repository.get('remote') or ''}",
             f"- Base commit: `{c.base_commit}`",
             f"- Issue: {c.issue.get('url') or c.issue.get('source')}",
             f"- Model: `{c.model}` · API calls: {c.usage.get('api_calls')} · tokens in/out: "
             f"{c.usage.get('prompt_tokens')}/{c.usage.get('output_tokens')}",
             f"- Context: {c.context.get('context_tokens')} tokens from {c.context.get('selected_files')} file(s) "
             f"(repository ≈ {c.context.get('repo_tokens_estimate')} tokens)",
             f"- Sandbox: {c.sandbox}", "", "## Verification checks", ""]
    lines += [f"- {'✔' if v else ('✘' if v is False else '–')} {k}" for k, v in c.verification_checks.items()]
    if c.reproduction:
        lines += ["", "## Reproduction", "", f"{c.reproduction.get('status')}: {c.reproduction.get('reason')}"]
    if c.root_cause:
        lines += ["", "## Root cause (model explanation)", "", c.root_cause]
    if c.source_locations:
        lines += ["", "## Relevant source locations", ""]
        lines += [f"- `{s.file}:{s.start_line}-{s.end_line}` `{s.symbol}`" for s in c.source_locations]
    if c.test_evidence:
        lines += ["", "## Test evidence", "", "| run | runner | exit | passed | failed | errors | seconds |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f"| {t.label} | {t.runner} | {t.exit_code} | {t.passed} | {t.failed} | {t.errors} | {t.duration_s} |"
                  for t in c.test_evidence]
    if c.new_failures:
        lines += ["", "**New failures:** " + ", ".join(f"`{n}`" for n in c.new_failures)]
    if c.patch:
        lines += ["", "## Patch", "", "```diff", c.patch.rstrip(), "```"]
    if c.regression_test:
        lines += ["", f"## Regression test (`{c.regression_test.get('path')}`)", "", "```python",
                  c.regression_test.get("code", "").rstrip(), "```"]
    lines += ["", "## Limitations", ""] + [f"- {n}" for n in c.limitations]
    if c.reproduction_commands:
        lines += ["", "## Reproduce this evidence", "", "```bash", *c.reproduction_commands, "```"]
    return "\n".join(lines) + "\n"
