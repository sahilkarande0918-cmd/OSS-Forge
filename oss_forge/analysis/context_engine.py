"""Issue-adaptive context engine.

Ranks symbols with deterministic, explainable signals (identifier matches, stack frames, error-message literals,
file mentions, lexical similarity, call/import graph propagation, git history) and packs AST-exact definitions
into a hard token budget. The model can then request one more definition at a time (progressive expansion).
"""
from __future__ import annotations

import math
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from oss_forge.ai.gemma_provider import estimate_tokens
from oss_forge.analysis.repo_map import RepoMap, read_text
from oss_forge.analysis.symbol_index import Symbol

STOPWORDS = set("""a an and are as at be been but by can could did do does doing for from get got had has have how i
if in into is it its just like make may me more my no not of on or our should so some such than that the their them
then there these they this to too use used using via was we were what when where which while who why will with would
you your expected actual behavior behaviour bug issue error fails failed failing wrong returns return result results
value values instead should when after before also please steps reproduce version python file line code works work
test tests call called calls true false none null new old""".split())
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
FRAME_PY = re.compile(r'File "([^"]+)", line (\d+), in ([\w<>]+)')
FRAME_JS = re.compile(r"at (?:([\w.$<>]+) )?\(?([^\s()]+\.[jt]sx?):(\d+):\d+\)?")
PATH_RE = re.compile(r"[\w./\\-]+\.(?:py|js|jsx|ts|tsx|mjs|cjs)\b")
QUOTED = re.compile(r"""["'`]([^"'`\n]{6,160})["'`]""")
EXC_RE = re.compile(r"\b([A-Z][A-Za-z]+(?:Error|Exception|Warning))\b")
# "pkg.mod.CustomError: message" lines that end Python tracebacks (custom names need not end in Error)
EXC_LINE = re.compile(r"(?m)^((?:[a-z_]\w*\.)+[A-Z]\w*|[A-Z]\w*(?:Error|Exception|Warning|Exit|Interrupt))(?::[ \t]*(.*))?$")

WEIGHTS = {
    "stack_frame": 15.0, "exact_name": 9.0, "model_suspect": 7.0, "error_literal": 9.0, "file_mentioned": 6.0,
    "file_stem": 3.0, "subword": 1.5, "lexical": 4.0, "graph": 0.35, "git_history": 1.5, "exception_raised": 3.0,
}


def split_words(identifier: str) -> list[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", identifier).lower().split("_")
    return [p for p in parts if len(p) > 2 and p not in STOPWORDS]


@dataclass
class IssueSignals:
    text: str
    identifiers: set[str] = field(default_factory=set)
    terms: Counter = field(default_factory=Counter)
    frames: list[tuple[str, int, str]] = field(default_factory=list)
    paths: set[str] = field(default_factory=set)
    literals: list[str] = field(default_factory=list)
    exceptions: set[str] = field(default_factory=set)
    model_symbols: set[str] = field(default_factory=set)
    model_files: set[str] = field(default_factory=set)

    @classmethod
    def from_text(cls, text: str, model_keywords=(), model_symbols=(), model_files=()) -> "IssueSignals":
        sig = cls(text=text)
        for f, line, func in FRAME_PY.findall(text):
            sig.frames.append((f.replace("\\", "/"), int(line), func))
        for func, f, line in FRAME_JS.findall(text):
            sig.frames.append((f.replace("\\", "/"), int(line), (func or "").split(".")[-1]))
        sig.paths = {p.replace("\\", "/").lstrip("./") for p in PATH_RE.findall(text)}
        sig.literals = [q.strip() for q in QUOTED.findall(text) if not IDENT.fullmatch(q.strip())]
        sig.exceptions = set(EXC_RE.findall(text)) | {name.split(".")[-1] for name, _ in EXC_LINE.findall(text)}
        sig.identifiers = {w for w in IDENT.findall(text) if w.lower() not in STOPWORDS}
        sig.identifiers |= {k for k in model_keywords if IDENT.fullmatch(k)}
        for w in list(sig.identifiers) + list(model_keywords):
            for sub in split_words(w):
                sig.terms[sub] += 1
        sig.model_symbols = {s.split(".")[-1].split("::")[-1].strip("()") for s in model_symbols if s}
        sig.model_files = {f.replace("\\", "/").lstrip("./") for f in model_files if f}
        return sig


@dataclass
class ContextItem:
    file: str
    symbol: str
    kind: str
    start: int
    end: int
    score: float
    reasons: list[str]
    code: str = ""
    tokens: int = 0

    def render(self) -> str:
        return (f"### {self.file}:L{self.start}-L{self.end} `{self.symbol}` ({self.kind})\n"
                f"```\n{self.code}\n```")


@dataclass
class ContextPack:
    items: list[ContextItem] = field(default_factory=list)
    related_tests: list[dict] = field(default_factory=list)
    ranking: list[dict] = field(default_factory=list)           # top-N scored symbols (selected or not)
    expansions: list[dict] = field(default_factory=list)
    budget_tokens: int = 0
    metrics: dict = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return sum(i.tokens for i in self.items)

    def files(self) -> list[str]:
        return sorted({i.file for i in self.items})

    def render(self) -> str:
        return "\n\n".join(i.render() for i in self.items)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tokens"] = self.tokens
        return d


class ContextEngine:
    def __init__(self, repo_map: RepoMap):
        self.rm = repo_map
        self.root = Path(repo_map.root)
        self.symbols: list[Symbol] = repo_map.symbol_objects()
        self.by_name: dict[str, list[Symbol]] = defaultdict(list)
        for s in self.symbols:
            self.by_name[s.name].append(s)
        self._lines: dict[str, list[str]] = {}
        self._doc_terms: dict[str, Counter] = {}
        self.files_read: set[str] = set()

    # -- source access ---------------------------------------------------------------------------------
    def lines(self, file: str) -> list[str]:
        if file not in self._lines:
            self._lines[file] = (read_text(self.root, file) or "").splitlines()
            self.files_read.add(file)
        return self._lines[file]

    def source(self, s: Symbol) -> str:
        return "\n".join(self.lines(s.file)[s.line - 1: s.end_line])

    def _terms(self, s: Symbol) -> Counter:
        if s.id not in self._doc_terms:
            text = self.source(s) if s.kind != "class" else f"{s.signature} {s.doc}"
            c = Counter()
            for w in IDENT.findall(text):
                c.update(split_words(w))
            self._doc_terms[s.id] = c
        return self._doc_terms[s.id]

    # -- scoring ---------------------------------------------------------------------------------------
    def _git_recent(self, sig: IssueSignals) -> dict[str, float]:
        """Files changed recently (weak signal) and in commits whose message mentions issue terms (stronger)."""
        try:
            out = subprocess.run(["git", "log", "-n", "60", "--name-only", "--format=@@%s"], cwd=self.root,
                                 capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace").stdout
        except (OSError, subprocess.TimeoutExpired):
            return {}
        boost: dict[str, float] = defaultdict(float)
        msg_terms: set[str] = set()
        for line in out.splitlines():
            if line.startswith("@@"):
                msg_terms = {w for ident in IDENT.findall(line) for w in split_words(ident)}
            elif line.strip():
                boost[line.strip()] = max(boost[line.strip()], 0.2 + (0.8 if msg_terms & set(sig.terms) else 0.0))
        return boost

    def score(self, sig: IssueSignals) -> list[tuple[float, Symbol, list[str]]]:
        candidates = [s for s in self.symbols if not s.is_test and s.kind != "variable"]
        n = max(1, len(candidates))
        df: Counter = Counter()
        for s in candidates:
            df.update(set(self._terms(s)))
        frame_funcs = {(PurePosixPath(f).name, func) for f, _, func in sig.frames}
        frame_lines = [(f, line) for f, line, _ in sig.frames]
        literal_files = {f for f, lits in self.rm.string_literals.items()
                         for lit in lits for q in sig.literals if len(q) >= 6 and (q in lit or lit in q)}
        git_boost = self._git_recent(sig)
        mentioned = {f for f in self.rm.files if any(f.endswith(p) or p.endswith(f) for p in sig.paths | sig.model_files)}
        stems = {PurePosixPath(p).stem.lower() for p in sig.paths} | {t for t in sig.terms}

        scores: dict[str, float] = {}
        reasons: dict[str, list[str]] = defaultdict(list)

        def add(s: Symbol, key: str, value: float, why: str):
            if value <= 0:
                return
            scores[s.id] = scores.get(s.id, 0.0) + value
            reasons[s.id].append(f"{why} (+{value:.1f})")

        for s in candidates:
            fname = PurePosixPath(s.file).name
            if (fname, s.name) in frame_funcs or any(s.file.endswith(f.split("/")[-1]) and s.line <= ln <= s.end_line
                                                     for f, ln in frame_lines):
                add(s, "stack_frame", WEIGHTS["stack_frame"], "in stack trace")
            if s.name in sig.identifiers or s.qualname in sig.identifiers:
                add(s, "exact_name", WEIGHTS["exact_name"], "name mentioned in issue")
            if s.name in sig.model_symbols or s.qualname in sig.model_symbols:
                add(s, "model_suspect", WEIGHTS["model_suspect"], "suspected by Gemma issue interpretation")
            if s.file in mentioned:
                add(s, "file_mentioned", WEIGHTS["file_mentioned"], "file mentioned")
            elif PurePosixPath(s.file).stem.lower() in stems:
                add(s, "file_stem", WEIGHTS["file_stem"], "file name matches issue term")
            name_words = set(split_words(s.name))
            overlap = name_words & set(sig.terms)
            if overlap:
                add(s, "subword", WEIGHTS["subword"] * len(overlap), f"name words {sorted(overlap)}")
            terms = self._terms(s)
            if terms and sig.terms:
                # TF-IDF cosine-ish similarity between issue terms and symbol body terms
                dot = sum(min(terms[t], 3) * math.log(1 + n / (1 + df[t])) * c for t, c in sig.terms.items() if t in terms)
                norm = math.sqrt(sum(min(v, 3) ** 2 for v in terms.values())) * math.sqrt(sum(v * v for v in sig.terms.values()))
                lex = dot / norm if norm else 0.0
                add(s, "lexical", round(WEIGHTS["lexical"] * min(1.0, lex), 2), "lexical similarity")
            if s.file in literal_files and any(q in self.source(s) for q in sig.literals if len(q) >= 6):
                add(s, "error_literal", WEIGHTS["error_literal"], "contains error message text")
            if sig.exceptions and any(f"raise {e}" in self.source(s) or f"throw new {e}" in self.source(s)
                                      for e in sig.exceptions):
                add(s, "exception_raised", WEIGHTS["exception_raised"], "raises reported exception")
            if s.file in git_boost:
                add(s, "git_history", round(WEIGHTS["git_history"] * git_boost[s.file], 2), "recent git history")

        # graph propagation: callers/callees and import neighbours of strong symbols get a share of their score
        imported_by = self.rm.imported_by()
        base = dict(scores)
        strong = sorted(base.items(), key=lambda kv: -kv[1])[:10]
        by_id = {s.id: s for s in candidates}
        for sid, val in strong:
            src = by_id.get(sid)
            if not src or val < 3:
                continue
            neighbours = [c for name in src.calls for c in self.by_name.get(name, []) if c.id in by_id]
            neighbours += [c for c in candidates if src.name in c.calls]
            neighbour_files = set(self.rm.imports.get(src.file, [])) | set(imported_by.get(src.file, []))
            for c in neighbours:
                if c.id != sid:
                    add(c, "graph", round(WEIGHTS["graph"] * val, 2), f"call-graph neighbour of {src.name}")
            for c in candidates:
                if c.file in neighbour_files and c.id != sid and c.id in base:
                    add(c, "graph", round(WEIGHTS["graph"] * 0.3 * val, 2), f"import neighbour of {src.file}")
        ranked = sorted(((v, by_id[k], reasons[k]) for k, v in scores.items()), key=lambda t: (-t[0], t[1].id))
        return ranked

    # -- packing ---------------------------------------------------------------------------------------
    def _item(self, s: Symbol, score: float, why: list[str], max_lines: int = 160) -> ContextItem:
        start, end = s.line, s.end_line
        if s.kind == "class" and end - start > 40:
            end = start + 25  # class headers only; methods are ranked separately
        if end - start > max_lines:
            end = start + max_lines
        lines = self.lines(s.file)[start - 1: end]
        if s.kind == "method" and "." in s.qualname:  # give the enclosing class line for orientation
            cls = next((c for c in self.by_name.get(s.qualname.split(".")[-2], []) if c.file == s.file), None)
            if cls:
                header = self.lines(s.file)[cls.line - 1]
                lines = [header, "    ..."] + lines
        code = "\n".join(lines)
        item = ContextItem(file=s.file, symbol=s.qualname, kind=s.kind, start=start, end=end, score=round(score, 2),
                           reasons=why, code=code)
        item.tokens = estimate_tokens(item.render())
        return item

    def build(self, sig: IssueSignals, budget_tokens: int, max_items: int = 25) -> ContextPack:
        ranked = self.score(sig)
        pack = ContextPack(budget_tokens=budget_tokens)
        pack.ranking = [{"symbol": s.qualname, "file": s.file, "line": s.line, "score": round(v, 2), "reasons": r}
                        for v, s, r in ranked[:30]]
        used: set[str] = set()
        top = ranked[0][0] if ranked else 0
        for value, s, why in ranked:
            if len(pack.items) >= max_items or value < max(1.0, 0.12 * top):
                break
            if any(s.file == i.file and i.start <= s.line and s.end_line <= i.end for i in pack.items):
                continue  # already covered by an enclosing definition
            item = self._item(s, value, why)
            if pack.tokens + item.tokens > budget_tokens:
                continue
            pack.items.append(item)
            used.add(s.id)
        selected = {i.symbol.split(".")[-1] for i in pack.items}
        for t in (s for s in self.symbols if s.is_test):
            hits = sorted(selected & set(t.calls))
            if hits:
                pack.related_tests.append({"file": t.file, "test": t.qualname, "line": t.line, "exercises": hits})
        pack.related_tests = pack.related_tests[:15]
        pack.metrics = {
            "repo_files": len(self.rm.files),
            "repo_tokens_estimate": self.rm.total_tokens_estimate,
            "symbols_scored": len([s for s in self.symbols if not s.is_test]),
            "symbols_with_signal": len(ranked),
            "files_inspected": len(self.files_read),
            "selected_items": len(pack.items),
            "selected_files": len(pack.files()),
            "context_tokens": pack.tokens,
            "context_fraction_of_repo": round(pack.tokens / max(1, self.rm.total_tokens_estimate), 4),
            "repo_map_cache_hit": self.rm.cache_hit,
        }
        return pack

    # -- progressive expansion -------------------------------------------------------------------------
    def expand(self, pack: ContextPack, symbol: str, reason: str, file: str = "", budget_extra: int = 1500) -> ContextItem | None:
        """Add exactly one requested definition to the pack (bounded), recording why it was requested."""
        name = symbol.split("(")[0].split(".")[-1].split("::")[-1].strip()
        matches = [s for s in self.by_name.get(name, []) if not file or s.file.endswith(file.lstrip("./"))]
        if not matches and "." in symbol:
            matches = [s for s in self.symbols if s.qualname == symbol]
        record = {"requested": symbol, "file": file, "reason": reason[:300], "added": False}
        pack.expansions.append(record)
        for s in matches:
            if any(i.file == s.file and i.start == s.line for i in pack.items):
                record["note"] = "already in context"
                return None
            item = self._item(s, 0.0, [f"requested by model: {reason[:120]}"])
            if item.tokens > budget_extra:
                record["note"] = f"definition too large ({item.tokens} tokens)"
                return None
            pack.items.append(item)
            record.update(added=True, location=f"{s.file}:{s.line}", tokens=item.tokens)
            return item
        record["note"] = "symbol not found"
        return None
