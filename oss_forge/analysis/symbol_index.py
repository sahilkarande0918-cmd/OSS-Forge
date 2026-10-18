"""Symbol extraction: Python via the `ast` module, JavaScript/TypeScript via a brace-aware scanner."""
from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field


@dataclass
class Symbol:
    name: str
    qualname: str
    kind: str              # function | method | class | variable
    file: str
    line: int
    end_line: int
    signature: str = ""
    doc: str = ""
    calls: list[str] = field(default_factory=list)
    is_test: bool = False

    @property
    def id(self) -> str:
        return f"{self.file}::{self.qualname}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FileIndex:
    path: str
    language: str
    lines: int
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)        # raw module specifiers
    string_literals: list[str] = field(default_factory=list)  # for matching error messages
    has_main: bool = False
    parse_error: str = ""


# ------------------------------------------------------------------------------------------------ Python
def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:  # pragma: no cover - very old/odd syntax
        args = "..."
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args}){ret}"


def index_python(path: str, source: str) -> FileIndex:
    idx = FileIndex(path=path, language="python", lines=source.count("\n") + 1)
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        idx.parse_error = f"{type(exc).__name__}: {exc}"
        return idx
    fname = path.rsplit("/", 1)[-1]
    is_test_file = fname.startswith("test_") or fname == "conftest.py" or path.endswith("_test.py") \n        or "/tests/" in f"/{path}"

    def calls_in(node: ast.AST) -> list[str]:
        names = {_call_name(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
        return sorted(n for n in names if n)

    def visit(body, prefix: str, in_class: bool):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{node.name}"
                start = min([d.lineno for d in node.decorator_list] + [node.lineno])
                idx.symbols.append(Symbol(
                    name=node.name, qualname=qual, kind="method" if in_class else "function", file=path,
                    line=start, end_line=node.end_lineno or node.lineno, signature=_signature(node),
                    doc=(ast.get_docstring(node) or "").split("\n")[0][:200], calls=calls_in(node),
                    is_test=is_test_file and node.name.startswith("test")))
            elif isinstance(node, ast.ClassDef):
                qual = f"{prefix}{node.name}"
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                idx.symbols.append(Symbol(
                    name=node.name, qualname=qual, kind="class", file=path,
                    line=min([d.lineno for d in node.decorator_list] + [node.lineno]),
                    end_line=node.end_lineno or node.lineno, signature=f"class {node.name}({bases})",
                    doc=(ast.get_docstring(node) or "").split("\n")[0][:200],
                    is_test=is_test_file and node.name.startswith("Test")))
                visit(node.body, qual + ".", True)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not prefix:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and (t.id.isupper() or not t.id.startswith("_")):
                        idx.symbols.append(Symbol(name=t.id, qualname=t.id, kind="variable", file=path,
                                                  line=node.lineno, end_line=node.end_lineno or node.lineno))

    visit(tree.body, "", False)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            idx.imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            idx.imports.append(mod)
            idx.imports += [f"{mod}.{a.name}" if mod.strip(".") else f"{mod}{a.name}" for a in node.names]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and 6 <= len(node.value) <= 200:
            idx.string_literals.append(node.value)
        elif isinstance(node, ast.If) and "__name__" in ast.unparse(node.test) and "__main__" in ast.unparse(node.test):
            idx.has_main = True
    return idx


# ------------------------------------------------------------------------------------- JavaScript / TS
_JS_PATTERNS = [
    ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*"
                            r"(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)")),
    ("method", re.compile(r"^\s+(?:public\s+|private\s+|protected\s+|static\s+|async\s+|readonly\s+)*"
                          r"([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\{\s*$")),
]
_JS_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]""", re.M)
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "constructor"}
_JS_STRING = re.compile(r"""(['"`])((?:\\.|(?!\1).){6,200})\1""")


def _block_end(lines: list[str], start: int) -> int:
    """Line index where the brace block opened at/after `start` closes (strings ignored roughly)."""
    depth, opened = 0, False
    for i in range(start, len(lines)):
        stripped = re.sub(r"""(['"`])(?:\\.|(?!\1).)*\1""", "", lines[i].split("//")[0])
        for ch in stripped:
            if ch == "{":
                depth, opened = depth + 1, True
            elif ch == "}":
                depth -= 1
                if opened and depth <= 0:
                    return i
        if not opened and i > start and stripped.rstrip().endswith(";"):
            return i
    return len(lines) - 1


def index_javascript(path: str, source: str) -> FileIndex:
    lang = "typescript" if path.endswith((".ts", ".tsx")) else "javascript"
    lines = source.splitlines()
    idx = FileIndex(path=path, language=lang, lines=len(lines))
    is_test_file = bool(re.search(r"\.(test|spec)\.[jt]sx?$", path)) or "__tests__/" in path
    class_stack: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        while class_stack and i > class_stack[-1][1]:
            class_stack.pop()
        for kind, pattern in _JS_PATTERNS:
            m = pattern.match(line)
            if not m or m.group(1) in _JS_KEYWORDS:
                continue
            if kind == "method" and not class_stack:
                continue
            end = _block_end(lines, i)
            name = m.group(1)
            qual = f"{class_stack[-1][0]}.{name}" if kind == "method" else name
            idx.symbols.append(Symbol(name=name, qualname=qual, kind=kind, file=path, line=i + 1, end_line=end + 1,
                                      signature=line.strip()[:160], is_test=is_test_file))
            if kind == "class":
                class_stack.append((name, end))
            break
    # test cases: it("...") / test("...")
    for i, line in enumerate(lines):
        m = re.match(r"""^\s*(?:it|test)\s*\(\s*['"`](.+?)['"`]""", line)
        if m:
            idx.symbols.append(Symbol(name=m.group(1)[:80], qualname=m.group(1)[:80], kind="function", file=path,
                                      line=i + 1, end_line=_block_end(lines, i) + 1, is_test=True))
    idx.imports = _JS_IMPORT.findall(source)
    idx.string_literals = [m.group(2) for m in _JS_STRING.finditer(source)][:500]
    return idx


INDEXERS = {".py": index_python, ".js": index_javascript, ".jsx": index_javascript, ".mjs": index_javascript,
            ".cjs": index_javascript, ".ts": index_javascript, ".tsx": index_javascript}


def index_file(path: str, source: str) -> FileIndex | None:
    for suffix, fn in INDEXERS.items():
        if path.endswith(suffix):
            return fn(path, source)
    return None
