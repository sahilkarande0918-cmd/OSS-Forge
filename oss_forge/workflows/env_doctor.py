"""Environment doctor: how can this repository be set up and tested, and what is missing locally?

Produces a setup plan. Nothing here installs anything: installing a repository's dependencies executes its
code (setup.py, postinstall scripts), so such steps are marked `requires_approval`.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from oss_forge.analysis.repo_map import RepoMap, _load_json, _load_toml, read_text
from oss_forge.security.execution_policy import docker_available

ENV_VAR_PATTERNS = [
    re.compile(r"os\.environ(?:\.get)?\s*[\[(]\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"),
    re.compile(r"os\.getenv\(\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"),
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]{2,})"),
    re.compile(r"process\.env\[\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"),
]
# import name differs from distribution name for some common packages
IMPORT_ALIASES = {"pyyaml": "yaml", "beautifulsoup4": "bs4", "pillow": "PIL", "scikit-learn": "sklearn",
                  "python-dateutil": "dateutil", "opencv-python": "cv2", "attrs": "attr", "pyjwt": "jwt"}


@dataclass
class SetupStep:
    description: str
    command: str = ""
    requires_approval: bool = False
    reason: str = ""


@dataclass
class EnvReport:
    python_requirement: str = ""
    node_requirement: str = ""
    local_python: str = ""
    local_node: str = ""
    package_managers: list[str] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    missing_python_deps: list[str] = field(default_factory=list)
    test_frameworks: list[str] = field(default_factory=list)
    test_command: list[str] = field(default_factory=list)
    lint_tools: list[str] = field(default_factory=list)
    env_var_names: list[str] = field(default_factory=list)
    container_files: list[str] = field(default_factory=list)
    docker_sandbox: bool = False
    plan: list[SetupStep] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    ready_to_test: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = ["# Environment report",
                 f"- Python required: {self.python_requirement or 'unspecified'} (local {self.local_python})",
                 f"- Node required: {self.node_requirement or 'unspecified'} (local {self.local_node or 'not found'})",
                 f"- Package managers: {', '.join(self.package_managers) or 'none'}; lockfiles: {', '.join(self.lockfiles) or 'none'}",
                 f"- Test frameworks: {', '.join(self.test_frameworks) or 'none'}; command: `{' '.join(self.test_command) or 'n/a'}`",
                 f"- Lint/format: {', '.join(self.lint_tools) or 'none'}",
                 f"- Env vars referenced (names only): {', '.join(self.env_var_names) or 'none'}",
                 f"- Containers: {', '.join(self.container_files) or 'none'}; Docker sandbox available: {self.docker_sandbox}",
                 f"- Missing Python deps in current interpreter: {', '.join(self.missing_python_deps) or 'none'}",
                 f"- Ready to run tests: **{self.ready_to_test}**", "", "## Setup plan"]
        for i, s in enumerate(self.plan, 1):
            flag = " (requires approval)" if s.requires_approval else ""
            lines.append(f"{i}. {s.description}{flag}" + (f"\n   `{s.command}`" if s.command else "")
                         + (f"\n   _{s.reason}_" if s.reason else ""))
        if self.diagnostics:
            lines += ["", "## Diagnostics"] + [f"- {d}" for d in self.diagnostics]
        return "\n".join(lines)


def _version_of(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _requirement_names(lines: list[str]) -> list[str]:
    names = []
    for line in lines:
        line = line.split("#")[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._\-]*)", line)
        if m:
            names.append(m.group(1))
    return names


def _python_version_ok(spec: str) -> bool:
    m = re.search(r">=\s*(\d+)\.(\d+)", spec)
    return not m or sys.version_info[:2] >= (int(m.group(1)), int(m.group(2)))


def diagnose(rm: RepoMap) -> EnvReport:
    root = Path(rm.root)
    rep = EnvReport(local_python=sys.version.split()[0], local_node=_version_of(["node", "--version"]),
                    package_managers=rm.package_managers, test_frameworks=rm.test_frameworks,
                    test_command=rm.test_command, lint_tools=rm.lint_tools, docker_sandbox=docker_available())
    rep.lockfiles = [f for f in rm.manifests if f.endswith((".lock", "-lock.json", "-lock.yaml", "lock.yaml", ".lockb"))
                     or PurePosixPath(f).name in ("Pipfile.lock",)]
    pyproject = _load_toml(root, "pyproject.toml") if "pyproject.toml" in rm.manifests else {}
    project = pyproject.get("project", {})
    rep.python_requirement = project.get("requires-python", "") or \
        pyproject.get("tool", {}).get("poetry", {}).get("dependencies", {}).get("python", "")
    if not rep.python_requirement and (root / ".python-version").is_file():
        rep.python_requirement = (root / ".python-version").read_text(encoding="utf-8").strip()
    deps = list(project.get("dependencies", []))
    for f in rm.manifests:
        if PurePosixPath(f).name.startswith("requirements") and f.endswith(".txt"):
            deps += (read_text(root, f) or "").splitlines()
    setup_py = read_text(root, "setup.py") if "setup.py" in rm.manifests else ""
    if setup_py:
        m = re.search(r"python_requires\s*=\s*['\"]([^'\"]+)", setup_py)
        rep.python_requirement = rep.python_requirement or (m.group(1) if m else "")
        block = re.search(r"install_requires\s*=\s*\[([^\]]*)\]", setup_py, re.S)
        if block:
            deps += re.findall(r"['\"]([^'\"]+)['\"]", block.group(1))
    rep.dependencies = sorted(set(_requirement_names(deps)))
    for name in rep.dependencies:
        mod = IMPORT_ALIASES.get(name.lower(), name.replace("-", "_"))
        if importlib.util.find_spec(mod) is None:
            rep.missing_python_deps.append(name)

    pkg = _load_json(root, "package.json") if "package.json" in rm.manifests else {}
    rep.node_requirement = pkg.get("engines", {}).get("node", "") if pkg else ""
    if not rep.node_requirement and (root / ".nvmrc").is_file():
        rep.node_requirement = (root / ".nvmrc").read_text(encoding="utf-8").strip()

    names: set[str] = set()
    for f in rm.files:
        if f.endswith((".py", ".js", ".ts", ".jsx", ".tsx", ".mjs")) or PurePosixPath(f).name == ".env.example":
            text = read_text(root, f) or ""
            for pat in ENV_VAR_PATTERNS:
                names.update(pat.findall(text))
            if f.endswith(".env.example"):
                names.update(re.findall(r"^([A-Z][A-Z0-9_]{2,})\s*=", text, re.M))
    rep.env_var_names = sorted(names)
    rep.container_files = [f for f in rm.files if PurePosixPath(f).name in ("Dockerfile", "docker-compose.yml",
                           "docker-compose.yaml", "compose.yaml", "devcontainer.json", "Containerfile")]

    # ---- plan --------------------------------------------------------------------------------------------
    python_repo = any(fw in rm.test_frameworks for fw in ("pytest", "unittest")) or "Python" in rm.languages
    if python_repo:
        if rep.python_requirement and not _python_version_ok(rep.python_requirement):
            rep.diagnostics.append(f"local Python {rep.local_python} does not satisfy {rep.python_requirement}")
        rep.plan.append(SetupStep("Create an isolated virtual environment inside the run worktree",
                                  "python -m venv .venv", reason="isolated from the global interpreter"))
        if pyproject or setup_py:
            rep.plan.append(SetupStep("Install the project in editable mode", 'pip install -e ".[test]"  (or -e .)',
                                      requires_approval=True, reason="executes the repository's build backend/setup.py"))
        reqs = [f for f in rm.manifests if PurePosixPath(f).name.startswith("requirements")]
        for r in reqs:
            rep.plan.append(SetupStep(f"Install {r}", f"pip install -r {r}", requires_approval=True,
                                      reason="downloads third-party packages"))
        if "pytest" in rm.test_frameworks and importlib.util.find_spec("pytest") is None:
            rep.plan.append(SetupStep("Install pytest", "pip install pytest"))
    if pkg:
        manager = next((m for m in ("pnpm", "yarn", "bun") if m in rm.package_managers), "npm")
        rep.plan.append(SetupStep(f"Install Node dependencies with {manager}",
                                  "npm ci" if manager == "npm" and "package-lock.json" in rm.manifests else f"{manager} install",
                                  requires_approval=True, reason="runs package lifecycle scripts"))
        if not rep.local_node:
            rep.diagnostics.append("Node.js is required by package.json but `node` was not found on PATH")
    if rm.test_command:
        rep.plan.append(SetupStep("Run the test suite in the sandbox", " ".join(rm.test_command),
                                  requires_approval=not rep.docker_sandbox,
                                  reason="Docker sandbox" if rep.docker_sandbox else "no Docker: host execution needs approval"))
    else:
        rep.diagnostics.append("no test framework detected; regression tests will be created with pytest")
    if rep.env_var_names:
        rep.diagnostics.append("code references environment variables (values never read): "
                               + ", ".join(rep.env_var_names[:12]))
    if rep.missing_python_deps:
        rep.diagnostics.append("not importable in the current interpreter: " + ", ".join(rep.missing_python_deps[:12])
                               + " -> tests importing them will be ENVIRONMENT_BLOCKED until installed")
    rep.ready_to_test = bool(python_repo and not rep.missing_python_deps and importlib.util.find_spec("pytest")
                             and _python_version_ok(rep.python_requirement or ""))
    return rep


def to_json(rep: EnvReport) -> str:
    return json.dumps(rep.to_dict(), indent=2)
