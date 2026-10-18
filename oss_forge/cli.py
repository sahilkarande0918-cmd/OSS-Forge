"""OSS Forge command-line interface. Every command supports --json for machine-readable output."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path

from oss_forge import __version__
from oss_forge.config import Config, ConfigError

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED = 0, 1, 2


def emit(args, data: dict, human: str) -> None:
    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(human)


def load_config(args) -> Config:
    cfg = Config.from_env()
    overrides = {
        "model": getattr(args, "model", None),
        "max_api_calls": getattr(args, "max_api_calls", None),
        "max_context_tokens": getattr(args, "max_context_tokens", None),
        "max_patch_attempts": getattr(args, "max_patch_attempts", None),
        "home": Path(args.out) if getattr(args, "out", None) else None,
    }
    if getattr(args, "dry_run", None) is not None:
        overrides["dry_run"] = args.dry_run
    return cfg.with_overrides(**overrides)


# ---------------------------------------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    from oss_forge.security.execution_policy import docker_available, run_command
    cfg = load_config(args)
    checks = []

    def check(name, ok, detail, required=True):
        checks.append({"check": name, "ok": bool(ok), "required": required, "detail": detail})

    check("python", sys.version_info >= (3, 10), platform.python_version())
    git = shutil.which("git")
    check("git", git, run_command(["git", "--version"], Path.cwd()).stdout.strip() if git else "not found")
    gh = shutil.which("gh")
    gh_auth = gh and run_command(["gh", "auth", "status"], Path.cwd(), keep_credentials=True).exit_code == 0
    check("github-cli", gh_auth, "authenticated" if gh_auth else ("installed, not authenticated" if gh else "not found"),
          required=False)
    check("docker-sandbox", docker_available(), "daemon reachable" if docker_available() else
          "unavailable: test execution needs --allow-host-exec (human approval)", required=False)
    check("gemini-api-key", cfg.has_api_key, "configured (value hidden)" if cfg.has_api_key else
          "missing: copy .env.example to .env and set GEMINI_API_KEY")
    check("model", cfg.model.startswith("gemma-4"), cfg.model)

    if args.live and cfg.has_api_key:
        from oss_forge.ai.gemma_provider import GemmaProvider, ProviderError
        from oss_forge.ai.schemas import Ping
        provider = GemmaProvider(cfg)
        try:
            reply = provider.generate_json("doctor:ping", 'Reply with {"ok": true, "message": "<one short '
                                           'sentence naming yourself>"}', Ping, max_output_tokens=256)
            check("live-inference", reply.ok, f"{cfg.model}: {reply.message[:120]} | usage={provider.usage()}")
        except ProviderError as exc:
            check("live-inference", False, str(exc))

    ok = all(c["ok"] for c in checks if c["required"])
    lines = [f"OSS Forge {__version__} doctor"] + [
        f"  [{'ok' if c['ok'] else ('!!' if c['required'] else '--')}] {c['check']}: {c['detail']}" for c in checks]
    emit(args, {"ok": ok, "config": cfg.public_dict(), "checks": checks}, "\n".join(lines))
    return EXIT_OK if ok else EXIT_ERROR


# ------------------------------------------------------------------------------------- validate-skill
def cmd_validate_skill(args) -> int:
    from oss_forge.skill_validator import validate_skill
    errors = validate_skill(Path(args.path))
    emit(args, {"ok": not errors, "errors": errors},
         "skill is valid" if not errors else "skill is INVALID:\n" + "\n".join(f"  - {e}" for e in errors))
    return EXIT_OK if not errors else EXIT_ERROR


# ------------------------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable JSON output")
    common.add_argument("--verbose", "-v", action="store_true")
    common.add_argument("--out", help="output directory for runs/caches (default: OSS_FORGE_HOME or .oss-forge)")
    common.add_argument("--model", help="override OSS_FORGE_MODEL (gemma-4-26b-a4b-it | gemma-4-31b-it)")
    common.add_argument("--max-api-calls", type=int)
    common.add_argument("--max-context-tokens", type=int)
    dry = common.add_mutually_exclusive_group()
    dry.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                     help="analyse only; never execute repository code or apply patches")
    dry.add_argument("--execute", dest="dry_run", action="store_false",
                     help="allow sandboxed test execution and patch application in isolated worktrees")

    parser = argparse.ArgumentParser(prog="oss-forge", description="Verified open-source contributions with Gemma 4.")
    parser.add_argument("--version", action="version", version=f"oss-forge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", parents=[common], help="check tools, credentials and sandbox availability")
    p.add_argument("--live", action="store_true", help="also make one minimal real Gemma 4 inference call")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("validate-skill", parents=[common], help="validate a SKILL.md against the Agent Skills spec")
    p.add_argument("path", nargs="?", default=str(Path(__file__).resolve().parent.parent / ".agents/skills/oss-forge"))
    p.set_defaults(func=cmd_validate_skill)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
