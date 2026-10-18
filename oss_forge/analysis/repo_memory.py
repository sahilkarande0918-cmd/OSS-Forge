"""Persistent repository memory: compact, deterministic knowledge reused across runs.

Stored per repository identity in `$OSS_FORGE_HOME/memory/<repo_id>.json`. Each fact records the git blob
hashes of the files it depends on; facts whose files changed are reported as stale and not reused.
No credentials, environment values or source bodies are stored.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from oss_forge.analysis.context_engine import IDENT, split_words
from oss_forge.analysis.repo_map import RepoMap
from oss_forge.security.execution_policy import redact, run_command

MAX_ENTRIES = 50


def blob_hashes(repo: Path, files: list[str]) -> dict[str, str]:
    if not files:
        return {}
    out = run_command(["git", "ls-files", "-s", "--", *files], repo).stdout
    return {line.split("\t", 1)[1]: line.split()[1] for line in out.splitlines() if "\t" in line}


@dataclass
class RepoMemory:
    path: Path
    data: dict

    @classmethod
    def load(cls, home: Path, repo_id: str) -> "RepoMemory":
        path = home / "memory" / f"{repo_id}.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "repo_id": repo_id, "architecture": {}, "conventions": {}, "important_symbols": [],
            "verified_fixes": [], "failures": [], "updated_at": None}
        return cls(path, data)

    def save(self) -> None:
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(redact(json.dumps(self.data, indent=2)), encoding="utf-8")

    # -- learning --------------------------------------------------------------------------------------
    def learn_repo(self, rm: RepoMap) -> None:
        rev = rm.imported_by()
        call_counts: dict[str, int] = {}
        for s in rm.symbols:
            for c in s["calls"]:
                call_counts[c] = call_counts.get(c, 0) + 1
        important = sorted((s for s in rm.symbols if not s["is_test"] and s["kind"] in ("function", "class", "method")),
                           key=lambda s: (-call_counts.get(s["name"], 0), s["file"], s["line"]))[:25]
        self.data["head"] = rm.head
        self.data["remote"] = rm.remote
        self.data["architecture"] = {
            "languages": dict(list(rm.languages.items())[:6]), "modules": rm.modules,
            "entry_points": rm.entry_points[:10],
            "hub_files": [f for f, _ in sorted(rev.items(), key=lambda kv: -len(kv[1]))[:8]],
        }
        self.data["conventions"] = {"test_command": rm.test_command, "test_frameworks": rm.test_frameworks,
                                    "lint_tools": rm.lint_tools, "contribution_docs": rm.contribution_docs,
                                    "ci_files": rm.ci_files}
        self.data["important_symbols"] = [{"symbol": s["qualname"], "file": s["file"], "line": s["line"],
                                           "callers": call_counts.get(s["name"], 0)} for s in important]

    def record_run(self, repo: Path, capsule: dict) -> None:
        files = [f["file"] for f in capsule.get("changed_files", [])] or \
                [loc["file"] for loc in capsule.get("source_locations", [])[:3]]
        entry = {"run_id": capsule["run_id"], "issue": capsule["issue"].get("title", "")[:200],
                 "status": capsule["verification_status"], "base_commit": capsule.get("base_commit"),
                 "files": files, "symbols": [loc["symbol"] for loc in capsule.get("source_locations", [])[:3]],
                 "root_cause": (capsule.get("root_cause") or "")[:300],
                 "regression_test": (capsule.get("regression_test") or {}).get("path"),
                 "file_hashes": blob_hashes(repo, files)}
        key = "verified_fixes" if capsule["verification_status"] == "VERIFIED" else "failures"
        if key == "failures":
            entry["summary"] = capsule.get("verification_summary", "")[:300]
        self.data[key] = ([e for e in self.data[key] if e["run_id"] != entry["run_id"]] + [entry])[-MAX_ENTRIES:]

    # -- recall ----------------------------------------------------------------------------------------
    def fresh_entries(self, repo: Path, key: str) -> tuple[list[dict], list[dict]]:
        fresh, stale = [], []
        for e in self.data.get(key, []):
            current = blob_hashes(repo, list(e.get("file_hashes", {})))
            (fresh if current == e.get("file_hashes", {}) else stale).append(e)
        return fresh, stale

    def hints_for(self, repo: Path, issue_text: str, limit: int = 3) -> list[str]:
        """Prior fixes/failures whose wording overlaps the new issue and whose files are unchanged."""
        terms = {w for ident in IDENT.findall(issue_text) for w in split_words(ident)}
        scored = []
        for key in ("verified_fixes", "failures"):
            fresh, _ = self.fresh_entries(repo, key)
            for e in fresh:
                words = {w for ident in IDENT.findall(f"{e['issue']} {' '.join(e['symbols'])}") for w in split_words(ident)}
                overlap = len(terms & words)
                if overlap >= 2:
                    scored.append((overlap, key, e))
        hints = []
        for _, key, e in sorted(scored, key=lambda t: -t[0])[:limit]:
            kind = "verified fix" if key == "verified_fixes" else f"earlier attempt ({e['status']})"
            hints.append(f"{kind}: '{e['issue']}' touched {e['files']} ({'; '.join(e['symbols'])}). {e['root_cause']}")
        return hints

    def summary(self, repo: Path) -> dict:
        vf, vs = self.fresh_entries(repo, "verified_fixes")
        ff, fs = self.fresh_entries(repo, "failures")
        return {"repo_id": self.data["repo_id"], "updated_at": self.data.get("updated_at"),
                "architecture": self.data.get("architecture"), "conventions": self.data.get("conventions"),
                "important_symbols": self.data.get("important_symbols", [])[:10],
                "verified_fixes": {"fresh": len(vf), "stale": len(vs)}, "failures": {"fresh": len(ff), "stale": len(fs)}}
