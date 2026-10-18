"""Execution safety: path confinement, secret redaction, scrubbed subprocesses, sandboxed test runs.

Repository code is untrusted. Test commands run either inside a network-less Docker container
or, only when the caller passes explicit human approval, on the host with a scrubbed environment.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_OUTPUT_CHARS = 20000
RUNNER_IMAGE = "oss-forge-runner:0.1"
RUNNER_DOCKERFILE = """FROM python:3.12-slim
RUN pip install --no-cache-dir "pytest>=8" && useradd -m runner
USER runner
WORKDIR /work
"""

# Executables OSS Forge itself may launch. Model output can never add to this list.
ALLOWED_EXECUTABLES = {"git", "gh", "python", "python3", "py", "docker", "node", "npm", "npx"}

SECRET_ENV_PATTERN = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)", re.I)
SECRET_FILE_NAMES = {".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519", "credentials", ".git-credentials"}
SECRET_FILE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}

_REDACTIONS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),                     # Google API keys
    re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"),                  # GitHub tokens
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"sk-[0-9A-Za-z_\-]{20,}"),                      # generic provider keys
    re.compile(r"AKIA[0-9A-Z]{16}"),                            # AWS access key ids
    re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}"),               # Slack tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?im)^([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*[=:]\s*)\S+$"),
]


class PolicyViolation(RuntimeError):
    pass


def redact(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    """Remove credential-looking values from text destined for logs, prompts or artifacts."""
    if not text:
        return text
    for secret in extra_secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[REDACTED]")
    for pattern in _REDACTIONS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


def configured_secrets() -> tuple[str, ...]:
    return tuple(v for k, v in os.environ.items() if SECRET_ENV_PATTERN.search(k) and v and len(v) >= 8)


def is_secret_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in SECRET_FILE_NAMES
        or (name.startswith(".env") and name != ".env.example")
        or path.suffix.lower() in SECRET_FILE_SUFFIXES
    )


def safe_path(root: Path, relative: str | Path) -> Path:
    """Resolve `relative` under `root`, rejecting absolute paths, traversal and escaping symlinks."""
    rel = Path(relative)
    if rel.is_absolute() or rel.drive or any(part == ".." for part in rel.parts):
        raise PolicyViolation(f"path escapes workspace: {relative}")
    root_resolved = root.resolve()
    candidate = (root_resolved / rel).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise PolicyViolation(f"path escapes workspace (symlink?): {relative}")
    if is_secret_file(candidate):
        raise PolicyViolation(f"refusing to touch secret-like file: {relative}")
    return candidate


def scrubbed_env(extra: dict | None = None) -> dict:
    """Environment for child processes with credential-like variables removed."""
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV_PATTERN.search(k)}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "GIT_TERMINAL_PROMPT": "0"})
    if extra:
        env.update(extra)
    return env


@dataclass
class CommandResult:
    argv: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    runner: str = "host"

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()

    def to_dict(self) -> dict:
        return asdict(self)


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return text[:half] + f"\n... [{len(text) - MAX_OUTPUT_CHARS} chars truncated] ...\n" + text[-half:]


def run_command(argv: list[str], cwd: Path, timeout: int = 120, env: dict | None = None,
                keep_credentials: bool = False) -> CommandResult:
    """Run a trusted argv (never a shell string). Output is truncated and redacted."""
    if not argv or not isinstance(argv, list):
        raise PolicyViolation("command must be a non-empty argv list")
    exe = Path(argv[0]).name.lower().removesuffix(".exe")
    if exe not in ALLOWED_EXECUTABLES and Path(argv[0]).resolve() != Path(sys.executable).resolve():
        raise PolicyViolation(f"executable not allowed: {argv[0]}")
    child_env = dict(os.environ) if keep_credentials else scrubbed_env()
    if env:
        child_env.update(env)
    start = time.perf_counter()
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=child_env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        code, out, err, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        code, timed_out = -1, True
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = f"TIMEOUT after {timeout}s"
    except FileNotFoundError as exc:
        code, out, err, timed_out = 127, "", f"executable not found: {exc}", False
    secrets = configured_secrets()
    return CommandResult(argv=[redact(a, secrets) for a in argv], cwd=str(cwd), exit_code=code,
                         stdout=redact(_truncate(out), secrets), stderr=redact(_truncate(err), secrets),
                         duration_s=round(time.perf_counter() - start, 3), timed_out=timed_out)


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                              timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@dataclass
class SandboxDecision:
    runner: str          # "docker" | "host" | "blocked"
    reason: str


class TestSandbox:
    """Chooses where untrusted test code runs and runs it there."""

    __test__ = False  # not a pytest test class

    def __init__(self, mode: str = "auto", allow_host_exec: bool = False, timeout: int = 300):
        self.mode, self.allow_host_exec, self.timeout = mode, allow_host_exec, timeout
        self._decision: SandboxDecision | None = None

    def decide(self) -> SandboxDecision:
        if self._decision:
            return self._decision
        if self.mode in ("auto", "docker") and docker_available():
            d = SandboxDecision("docker", "network-less Docker container (--network none, cpu/mem/pid limits)")
        elif self.mode == "docker":
            d = SandboxDecision("blocked", "OSS_FORGE_SANDBOX=docker but the Docker daemon is not reachable")
        elif self.allow_host_exec:
            d = SandboxDecision("host", "host execution explicitly approved (--allow-host-exec); env scrubbed")
        else:
            d = SandboxDecision("blocked", "no isolated sandbox available; re-run with --allow-host-exec after "
                                           "a human approves running this repository's code on the host")
        self._decision = d
        return d

    def _ensure_image(self) -> None:
        if subprocess.run(["docker", "image", "inspect", RUNNER_IMAGE], capture_output=True).returncode == 0:
            return
        subprocess.run(["docker", "build", "-t", RUNNER_IMAGE, "-"], input=RUNNER_DOCKERFILE.encode(),
                       capture_output=True, check=True, timeout=600)

    def run_pytest(self, worktree: Path, args: list[str]) -> CommandResult:
        decision = self.decide()
        pytest_args = ["-m", "pytest", "-p", "no:cacheprovider", *args]
        if decision.runner == "blocked":
            return CommandResult(argv=["python", *pytest_args], cwd=str(worktree), exit_code=-2, stdout="",
                                 stderr=f"BLOCKED: {decision.reason}", duration_s=0.0, runner="blocked")
        if decision.runner == "docker":
            self._ensure_image()
            argv = ["docker", "run", "--rm", "--network", "none", "--memory", "1g", "--cpus", "1",
                    "--pids-limit", "256", "--security-opt", "no-new-privileges",
                    "-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{worktree.resolve()}:/work", "-w", "/work", RUNNER_IMAGE, "python", *pytest_args]
            result = run_command(argv, cwd=worktree, timeout=self.timeout)
            result.runner = "docker"
            return result
        result = run_command([sys.executable, *pytest_args], cwd=worktree, timeout=self.timeout)
        result.runner = "host"
        return result
