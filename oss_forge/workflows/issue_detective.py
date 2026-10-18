"""Issue Detective: load an issue from GitHub/file/text, extract hard facts, and ask Gemma 4 for
an interpretation, ranked root-cause hypotheses and a narrow regression test."""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from oss_forge.ai.gemma_provider import GemmaProvider, estimate_tokens, untrusted
from oss_forge.ai.schemas import Diagnosis, IssueInterpretation
from oss_forge.analysis.context_engine import EXC_LINE, ContextEngine, ContextPack, IssueSignals
from oss_forge.analysis.repo_map import RepoMap, read_text
from oss_forge.security.execution_policy import redact, run_command

GH_URL = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)")
MAX_ISSUE_TOKENS = 2500


@dataclass
class Issue:
    source: str                # github | file | text
    title: str
    body: str
    url: str = ""
    number: int | None = None
    repo: str = ""             # owner/name
    labels: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        parts = [self.title, self.body] + [f"Comment: {c}" for c in self.comments[:5]]
        text = redact("\n\n".join(p for p in parts if p))
        limit = MAX_ISSUE_TOKENS * 4
        return text if len(text) <= limit else text[:limit] + "\n[... issue truncated ...]"

    def to_dict(self) -> dict:
        return asdict(self)


def _owner_repo_from_remote(remote: str | None) -> str:
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", remote or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _fetch_github(owner_repo: str, number: int) -> Issue:
    """Use the authenticated gh CLI when available, else the public REST API."""
    r = run_command(["gh", "issue", "view", str(number), "--repo", owner_repo, "--json",
                     "title,body,comments,url,number,labels"], Path.cwd(), timeout=60, keep_credentials=True)
    if r.ok:
        d = json.loads(r.stdout)
        return Issue("github", d.get("title", ""), d.get("body") or "", d.get("url", ""), d.get("number"), owner_repo,
                     [lbl.get("name", "") for lbl in d.get("labels", [])],
                     [c.get("body", "") for c in d.get("comments", [])])
    base = f"https://api.github.com/repos/{owner_repo}/issues/{number}"
    req = urllib.request.Request(base, headers={"Accept": "application/vnd.github+json", "User-Agent": "oss-forge"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        d = json.load(resp)
    comments = []
    if d.get("comments"):
        with urllib.request.urlopen(urllib.request.Request(base + "/comments", headers=req.headers), timeout=30) as resp:
            comments = [c.get("body", "") for c in json.load(resp)]
    return Issue("github", d.get("title", ""), d.get("body") or "", d.get("html_url", ""), number, owner_repo,
                 [lbl.get("name", "") for lbl in d.get("labels", [])], comments)


def load_issue(spec: str, repo_map: RepoMap | None = None) -> Issue:
    """Accepts a GitHub issue URL, `#123`/`123` (uses the repo's GitHub remote), a .md/.txt file, or plain text."""
    spec = spec.strip()
    m = GH_URL.match(spec)
    if m:
        return _fetch_github(f"{m.group(1)}/{m.group(2)}", int(m.group(3)))
    if re.fullmatch(r"#?\d+", spec):
        owner_repo = _owner_repo_from_remote(repo_map.remote if repo_map else None)
        if not owner_repo:
            raise ValueError("issue number given but the repository has no GitHub remote; pass a full issue URL")
        return _fetch_github(owner_repo, int(spec.lstrip("#")))
    path = Path(spec)
    if len(spec) < 260 and path.suffix.lower() in (".md", ".txt") and path.is_file():
        text = path.read_text(encoding="utf-8")
        lines = text.strip().splitlines()
        title = lines[0].lstrip("# ").strip() if lines else path.stem
        return Issue("file", title, "\n".join(lines[1:]).strip(), url=str(path))
    lines = spec.splitlines()
    return Issue("text", lines[0][:120], "\n".join(lines[1:]).strip() if len(lines) > 1 else "")


# ----------------------------------------------------------------------------------------- facts
@dataclass
class IssueFacts:
    error_messages: list[str]
    exceptions: list[str]
    stack_frames: list[str]
    code_blocks: int
    expected: str
    actual: str
    repro_steps: list[str]
    mentioned_paths: list[str]


def extract_facts(issue: Issue) -> IssueFacts:
    text = issue.text
    sig = IssueSignals.from_text(text)

    def section(name: str) -> str:
        m = re.search(rf"(?im)^\W*{name}[^:\n]*[:\n]\s*(.+?)(?:\n\s*\n|\Z)", text, re.S)
        return m.group(1).strip()[:500] if m else ""

    errors = [m.group(0) for m in EXC_LINE.finditer(text)]
    return IssueFacts(
        error_messages=[e.strip()[:200] for e in errors][:5],
        exceptions=sorted(sig.exceptions),
        stack_frames=[f"{f}:{ln} in {fn}" for f, ln, fn in sig.frames][:10],
        code_blocks=text.count("```") // 2,
        expected=section("expected"),
        actual=section("actual"),
        repro_steps=re.findall(r"(?m)^\s*\d+[.)]\s+(.+)$", text)[:10],
        mentioned_paths=sorted(sig.paths),
    )


# ---------------------------------------------------------------------------------------- model steps
def _symbol_outline(rm: RepoMap, engine: ContextEngine, issue_text: str, max_tokens: int = 1500) -> str:
    """Compact list of candidate symbols for the interpretation step (deterministically pre-ranked)."""
    ranked = engine.score(IssueSignals.from_text(issue_text))
    names = [f"{s.file}:{s.line} {s.qualname}" for _, s, _ in ranked[:40]]
    seen = {n.split(" ")[-1] for n in names}
    for s in rm.symbols:
        if not s["is_test"] and s["kind"] in ("function", "class", "method") and s["qualname"] not in seen:
            names.append(f"{s['file']}:{s['line']} {s['qualname']}")
    out, used = [], 0
    for n in names:
        used += estimate_tokens(n) + 1
        if used > max_tokens:
            break
        out.append(n)
    return "\n".join(out)


def interpret_issue(issue: Issue, rm: RepoMap, engine: ContextEngine, provider: GemmaProvider) -> IssueInterpretation:
    facts = extract_facts(issue)
    prompt = (f"Repository languages: {', '.join(list(rm.languages)[:5])}; test frameworks: {', '.join(rm.test_frameworks) or 'none'}.\n"
              f"Candidate symbols (file:line qualname):\n{_symbol_outline(rm, engine, issue.text)}\n\n"
              f"Deterministically extracted facts: {json.dumps(asdict(facts))}\n\n"
              f"{untrusted('issue', issue.text)}\n\n"
              "Interpret the issue. List identifiers likely involved (prefer names from the candidate list), what "
              "behaviour is expected vs actual, and rate clarity: 'clear' if a concrete failing input/output or "
              "traceback is given, 'unclear' if there is not enough information to write a failing test.")
    return provider.generate_json("interpret_issue", prompt, IssueInterpretation, max_output_tokens=1500)


def conventions_excerpt(rm: RepoMap, max_chars: int = 600) -> str:
    root = Path(rm.root)
    parts = [f"test framework: {', '.join(rm.test_frameworks) or 'pytest'}", f"lint: {', '.join(rm.lint_tools) or 'none'}"]
    for doc in rm.contribution_docs[:1]:
        parts.append(f"{doc}: " + (read_text(root, doc) or "")[:max_chars].replace("\n", " "))
    return "; ".join(parts)


def existing_test_sample(rm: RepoMap, pack: ContextPack, max_lines: int = 30) -> str:
    """Head of one related existing test file so generated tests follow local conventions (imports, fixtures)."""
    candidates = [t["file"] for t in pack.related_tests] + [f for f in rm.test_files if f.endswith(".py")
                                                            and not f.endswith("conftest.py")]
    for f in candidates:
        text = read_text(Path(rm.root), f)
        if text:
            return f"# {f}\n" + "\n".join(text.splitlines()[:max_lines])
    return ""


def diagnose_issue(issue: Issue, interp: IssueInterpretation, rm: RepoMap, engine: ContextEngine, pack: ContextPack,
                   provider: GemmaProvider, max_expansion_rounds: int = 2, feedback: str = "") -> Diagnosis:
    """Hypotheses + regression test. The model may request missing definitions (progressive expansion)."""
    diagnosis = Diagnosis()
    for round_no in range(max_expansion_rounds + 1):
        prompt = (f"Issue interpretation: {interp.model_dump_json()}\n\n"
                  f"Conventions: {conventions_excerpt(rm)}\n"
                  f"Existing test style sample:\n```\n{existing_test_sample(rm, pack)}\n```\n\n"
                  f"Relevant code (ranked, with line numbers):\n{pack.render()}\n\n"
                  f"{untrusted('issue', issue.text)}\n\n"
                  "Tasks:\n1. Give 1-3 ranked root-cause hypotheses; each cites file, symbol and concrete evidence "
                  "from the code above, a confidence in [0,1], and how a test would validate it.\n"
                  "2. Write ONE narrow pytest regression test module that FAILS on the current code because of the "
                  "reported bug and PASSES once it is fixed. Use only inputs/outputs stated in the issue, import from "
                  "the package as the existing tests do, no network/filesystem/subprocess, no new dependencies. "
                  "Path: tests/test_oss_forge_regression.py (or the repo's test directory).\n"
                  "3. If the issue lacks the information needed to write such a test, set reproducible=false, explain "
                  "why, and omit the test. Do not guess at a bug the issue does not describe.\n"
                  + ("4. If a definition you need is missing, list it in need_more_context (max 3) instead of guessing.\n"
                     if round_no < max_expansion_rounds else "")
                  + (f"\nFeedback on your previous test:\n{feedback}\n" if feedback else ""))
        diagnosis = provider.generate_json("diagnose", prompt, Diagnosis, max_output_tokens=3000)
        if not diagnosis.need_more_context or round_no == max_expansion_rounds:
            break
        added = [engine.expand(pack, r.symbol, r.reason, r.file) for r in diagnosis.need_more_context[:3]]
        if not any(added):
            break
    return diagnosis
