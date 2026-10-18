"""Maintainer mode: read-only triage of an authorized repository's open issues.

Finds likely duplicates and related issues (TF-IDF cosine), estimates complexity from deterministic signals
(clarity markers + how localizable the issue is with the context engine), and produces a ranked list of
separate, individually reviewable contribution plans. It never opens PRs or comments on issues.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from oss_forge.analysis.context_engine import IDENT, ContextEngine, IssueSignals, split_words
from oss_forge.analysis.repo_map import RepoMap
from oss_forge.security.execution_policy import run_command

EASY_LABELS = {"good first issue", "bug", "easy", "help wanted", "beginner", "low-hanging fruit"}
HARD_LABELS = {"feature", "enhancement", "discussion", "question", "design", "epic", "breaking"}


@dataclass
class TriagedIssue:
    number: int
    title: str
    labels: list[str]
    complexity: str
    automatable: bool
    score: float
    signals: list[str] = field(default_factory=list)
    likely_files: list[str] = field(default_factory=list)
    duplicate_of: int | None = None
    related: list[int] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)


def fetch_open_issues(repo: Path, limit: int) -> list[dict]:
    r = run_command(["gh", "issue", "list", "--state", "open", "--limit", str(limit), "--json",
                     "number,title,body,labels,comments"], repo, timeout=120, keep_credentials=True)
    if not r.ok:
        raise RuntimeError(f"gh issue list failed: {r.output[-300:]}")
    return json.loads(r.stdout)


def _vector(text: str) -> Counter:
    return Counter(w for ident in IDENT.findall(text) for w in split_words(ident))


def _cosine(a: Counter, b: Counter, idf: dict[str, float]) -> float:
    dot = sum(a[t] * b[t] * idf.get(t, 1) ** 2 for t in a if t in b)
    na = math.sqrt(sum((v * idf.get(t, 1)) ** 2 for t, v in a.items()))
    nb = math.sqrt(sum((v * idf.get(t, 1)) ** 2 for t, v in b.items()))
    return dot / (na * nb) if na and nb else 0.0


def triage(issues: list[dict], rm: RepoMap, duplicate_threshold: float = 0.6, related_threshold: float = 0.3) -> list[TriagedIssue]:
    engine = ContextEngine(rm)
    texts = {i["number"]: f"{i['title']}\n{i.get('body') or ''}" for i in issues}
    vecs = {n: _vector(t) for n, t in texts.items()}
    df = Counter(t for v in vecs.values() for t in v)
    idf = {t: math.log(1 + len(vecs) / c) for t, c in df.items()}
    out = []
    for issue in sorted(issues, key=lambda i: i["number"]):
        n, text = issue["number"], texts[issue["number"]]
        labels = [lbl["name"] if isinstance(lbl, dict) else str(lbl) for lbl in issue.get("labels", [])]
        signals, score = [], 0.0
        sig = IssueSignals.from_text(text)
        if sig.frames or sig.exceptions:
            signals.append("has traceback/exception")
            score += 2
        if "```" in text or sig.literals:
            signals.append("has code or concrete values")
            score += 1
        if any(k in text.lower() for k in ("expected", "actual", "steps to reproduce", "repro")):
            signals.append("states expected/actual or repro steps")
            score += 1.5
        low = {lbl.lower() for lbl in labels}
        if low & EASY_LABELS:
            signals.append(f"labels {sorted(low & EASY_LABELS)}")
            score += 1
        if low & HARD_LABELS:
            signals.append(f"labels {sorted(low & HARD_LABELS)}")
            score -= 2
        ranked = engine.score(sig)
        top_files = list(dict.fromkeys(s.file for _, s, _ in ranked[:5]))[:3]
        if ranked and ranked[0][0] >= 8:
            signals.append(f"localizes to {top_files[:2]} (top score {ranked[0][0]:.1f})")
            score += 2
        elif not ranked:
            signals.append("no code symbols matched")
            score -= 1
        words = len(text.split())
        complexity = "small" if score >= 4 and words < 400 else "medium" if score >= 2 else "large" if words > 600 else "unknown"
        sims = sorted(((round(_cosine(vecs[n], vecs[m], idf), 2), m) for m in vecs if m != n), reverse=True)
        dup = next((m for s, m in sims if s >= duplicate_threshold and m < n), None)
        related = [m for s, m in sims if related_threshold <= s < duplicate_threshold][:3]
        t = TriagedIssue(n, issue["title"], labels, complexity, automatable=complexity == "small" and dup is None,
                         score=round(score, 2), signals=signals, likely_files=top_files, duplicate_of=dup, related=related)
        if dup:
            t.plan = [f"Likely duplicate of #{dup}: confirm with a maintainer before any work."]
        elif t.automatable:
            t.plan = [f"python -m oss_forge solve --repo {rm.root} --issue {n} --dry-run",
                      "Review the dry-run diff and regression test, then re-run with --execute (sandbox/approval).",
                      "Open a separate draft PR only after explicit approval: python -m oss_forge pr --run-id <id> --create --approve"]
        else:
            t.plan = ["Not recommended for automation: ask the reporter for a reproducible example or scope it first."]
        out.append(t)
    return sorted(out, key=lambda t: (not t.automatable, t.duplicate_of is not None, -t.score, t.number))


def to_markdown(items: list[TriagedIssue]) -> str:
    lines = ["# Maintainer triage", "", "| # | title | complexity | automatable | duplicate of | related | likely files |",
             "|---|---|---|---|---|---|---|"]
    for t in items:
        lines.append(f"| {t.number} | {t.title[:60]} | {t.complexity} | {'yes' if t.automatable else 'no'} | "
                     f"{t.duplicate_of or ''} | {', '.join(map(str, t.related))} | {', '.join(t.likely_files)} |")
    lines += ["", "Each automatable issue is a separate plan; nothing is submitted without approval."]
    return "\n".join(lines)


def to_dicts(items: list[TriagedIssue]) -> list[dict]:
    return [asdict(t) for t in items]
