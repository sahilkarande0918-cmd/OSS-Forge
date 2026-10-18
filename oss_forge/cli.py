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


# ------------------------------------------------------------------------------------- map / context / env
def _repo_map(args, cfg):
    from oss_forge.analysis.repo_map import build_repo_map
    return build_repo_map(Path(args.repo), cache_dir=cfg.home / "cache", use_cache=not args.no_cache)


def _read_issue_arg(value: str) -> str:
    p = Path(value)
    return p.read_text(encoding="utf-8") if p.is_file() else value


def cmd_map(args) -> int:
    cfg = load_config(args)
    rm = _repo_map(args, cfg)
    if args.json:
        data = rm.to_dict()
        if not args.full:
            data["symbols"] = data["symbols"][:200]
            data.pop("string_literals")
        print(json.dumps(data, indent=2))
    else:
        print(rm.to_markdown())
        print(f"\n(cache {'hit' if rm.cache_hit else 'miss'}, {rm.build_seconds}s)")
    return EXIT_OK


def cmd_context(args) -> int:
    from oss_forge.analysis.context_engine import ContextEngine, IssueSignals
    cfg = load_config(args)
    rm = _repo_map(args, cfg)
    pack = ContextEngine(rm).build(IssueSignals.from_text(_read_issue_arg(args.issue)), args.budget or cfg.max_context_tokens)
    if args.json:
        print(json.dumps(pack.to_dict(), indent=2))
    else:
        print("## Ranking\n" + "\n".join(f"{r['score']:6.1f}  {r['file']}:{r['line']} {r['symbol']}  <- "
                                         + "; ".join(r["reasons"][:3]) for r in pack.ranking[:12]))
        print(f"\n## Selected context ({pack.tokens} tokens / budget {pack.budget_tokens})\n" + pack.render())
        print("\n## Metrics\n" + json.dumps(pack.metrics, indent=2))
    return EXIT_OK


def cmd_env(args) -> int:
    from oss_forge.workflows.env_doctor import diagnose
    cfg = load_config(args)
    rep = diagnose(_repo_map(args, cfg))
    emit(args, rep.to_dict(), rep.to_markdown())
    return EXIT_OK


# ------------------------------------------------------------------------------------------- analyze
def _forge(args, cfg):
    from oss_forge.workflows.solve import Forge
    return Forge(cfg, Path(args.repo), args.issue, allow_host_exec=args.allow_host_exec)


def _require_key(cfg) -> bool:
    if cfg.has_api_key:
        return True
    print("GEMINI_API_KEY is not set: copy .env.example to .env and add your key "
          "(https://aistudio.google.com/apikey). Deterministic commands (map, context, env) work without it.",
          file=sys.stderr)
    return False


def cmd_analyze(args) -> int:
    from oss_forge.ai.gemma_provider import ProviderError
    from oss_forge.workflows.patch_guard import Worktree
    cfg = load_config(args)
    if not _require_key(cfg):
        return EXIT_BLOCKED
    forge = _forge(args, cfg)
    try:
        forge.analyze()
        if not cfg.dry_run and forge.rm.head:
            with Worktree(forge.repo, forge.rm.head, "analyze") as wt:
                forge.reproduce(wt)
    except ProviderError as exc:
        print(f"Gemma API error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    d = forge.diagnosis
    data = {"run_id": forge.store.run_id, "run_dir": str(forge.store.root), "interpretation": forge.interpretation.model_dump(),
            "context": {"files": forge.pack.files(), "tokens": forge.pack.tokens, "metrics": forge.pack.metrics},
            "diagnosis": d.model_dump(), "reproduction": forge.repro_result.to_dict() if forge.repro_result else None,
            "usage": forge.provider.usage(), "log": forge.log}
    forge.store.write_json("analysis.json", data)
    human = [f"Run {forge.store.run_id} -> {forge.store.root}", *forge.log, "", "Hypotheses:"]
    human += [f"  [{h.confidence:.2f}] {h.file}::{h.symbol} - {h.explanation}" for h in d.hypotheses]
    if forge.repro_result:
        human.append(f"\nReproduction: {forge.repro_result.status} - {forge.repro_result.reason}")
    elif cfg.dry_run:
        human.append("\nDry run: regression test written to the run directory but not executed (use --execute).")
    human.append(f"\nUsage: {forge.provider.usage()}")
    emit(args, data, "\n".join(human))
    return EXIT_OK


# ----------------------------------------------------------------------------------- solve / verify / pr
STATUS_EXIT = {"VERIFIED": EXIT_OK, "PARTIALLY_VERIFIED": EXIT_OK, "INCONCLUSIVE": 3, "FAILED": EXIT_ERROR,
               "BLOCKED": EXIT_BLOCKED}


def _capsule_summary(capsule: dict, run_dir: Path) -> str:
    u = capsule.get("usage", {})
    ctx = capsule.get("context", {})
    lines = [f"Status: {capsule['verification_status']} - {capsule['verification_summary']}",
             f"Changed files: {', '.join(f['file'] for f in capsule.get('changed_files', [])) or 'none'}",
             f"Gemma: {capsule['model']} | API calls {u.get('api_calls')} | tokens in/out "
             f"{u.get('prompt_tokens')}/{u.get('output_tokens')} | context {ctx.get('context_tokens')} of "
             f"~{ctx.get('repo_tokens_estimate')} repo tokens",
             f"Capsule: {run_dir / 'capsule.md'}", f"PR draft: {run_dir / 'pr.md'}"]
    return "\n".join(lines)


def cmd_solve(args) -> int:
    from oss_forge.ai.gemma_provider import ProviderError
    cfg = load_config(args)
    if not _require_key(cfg):
        return EXIT_BLOCKED
    forge = _forge(args, cfg)
    try:
        capsule = forge.solve()
    except ProviderError as exc:
        print(f"Gemma API error: {exc}", file=sys.stderr)
        if forge.store:
            forge.store.write_json("error.json", {"error": str(exc), "log": forge.log, "usage": forge.provider.usage()})
        return EXIT_ERROR
    out = {"run_id": forge.store.run_id, "run_dir": str(forge.store.root), "status": capsule["verification_status"],
           "summary": capsule["verification_summary"], "usage": capsule["usage"], "log": forge.log}
    emit(args, out, "\n".join(forge.log) + "\n\n" + _capsule_summary(capsule, forge.store.root))
    return STATUS_EXIT.get(capsule["verification_status"], EXIT_ERROR)


def cmd_verify(args) -> int:
    from oss_forge.workflows.solve import RunStore, verify_run
    cfg = load_config(args)
    capsule = verify_run(cfg, args.run_id, Path(args.repo) if args.repo else None, args.allow_host_exec)
    run_dir = RunStore.open(cfg.home, args.run_id).root
    emit(args, {"run_id": args.run_id, "status": capsule["verification_status"], "checks": capsule["verification_checks"],
                "summary": capsule["verification_summary"]}, _capsule_summary(capsule, run_dir))
    return STATUS_EXIT.get(capsule["verification_status"], EXIT_ERROR)


def cmd_pr(args) -> int:
    from oss_forge.workflows.pr_builder import PRPackage, create_draft_pr
    from oss_forge.workflows.solve import RunStore
    cfg = load_config(args)
    store = RunStore.open(cfg.home, args.run_id)
    capsule, pkg = store.read_json("capsule.json"), PRPackage(**store.read_json("pr.json"))
    meta = store.read_json("run.json")
    if not args.create:
        emit(args, pkg.to_dict(), f"# {pkg.title}\n\n{pkg.body}\n\n## Commands\n" + "\n".join(pkg.commands)
             + "\n\nTo open a DRAFT PR: re-run with --create --approve (requires human approval).")
        return EXIT_OK
    if not args.approve:
        print("Refusing to push or open a PR without --approve (explicit human authorization).", file=sys.stderr)
        return EXIT_BLOCKED
    result = create_draft_pr(Path(args.repo or meta["repo_path"]), capsule, pkg, store.root)
    store.write_json("pr_result.json", result)
    emit(args, result, json.dumps(result, indent=2))
    return EXIT_OK if result.get("created") else EXIT_ERROR


def cmd_schema(args) -> int:
    from oss_forge.workflows.capsule import capsule_json_schema
    print(json.dumps(capsule_json_schema(), indent=2))
    return EXIT_OK


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

    repo_opts = argparse.ArgumentParser(add_help=False)
    repo_opts.add_argument("--repo", default=".", help="path to the target repository")
    repo_opts.add_argument("--no-cache", action="store_true", help="rebuild the repository map")

    p = sub.add_parser("map", parents=[common, repo_opts], help="repository intelligence (no API calls)")
    p.add_argument("--full", action="store_true", help="with --json: include every symbol and string literal")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("context", parents=[common, repo_opts],
                       help="rank code for an issue and pack it into a token budget (no API calls)")
    p.add_argument("--issue", required=True, help="issue text or path to an issue .md file")
    p.add_argument("--budget", type=int, help="context token budget (default OSS_FORGE_MAX_CONTEXT_TOKENS)")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("env", parents=[common, repo_opts], help="environment doctor: setup plan and diagnostics")
    p.set_defaults(func=cmd_env)

    exec_opts = argparse.ArgumentParser(add_help=False)
    exec_opts.add_argument("--issue", required=True,
                           help="GitHub issue URL, #number (repo must have a GitHub remote), .md/.txt file, or text")
    exec_opts.add_argument("--allow-host-exec", action="store_true",
                           help="HUMAN APPROVAL: run this repository's tests on the host when Docker is unavailable")
    exec_opts.add_argument("--max-patch-attempts", type=int)

    p = sub.add_parser("analyze", parents=[common, repo_opts, exec_opts],
                       help="Gemma 4 issue interpretation, root-cause hypotheses, regression test (+ reproduction with --execute)")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("solve", parents=[common, repo_opts, exec_opts],
                       help="end-to-end: analyse, reproduce, patch, verify, capsule, PR draft")
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("verify", parents=[common], help="re-run proof-of-fix verification for a stored run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--repo", help="target repository (default: path recorded in the run)")
    p.add_argument("--allow-host-exec", action="store_true", help="HUMAN APPROVAL: run tests on the host")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("pr", parents=[common], help="show the PR package; --create --approve opens a DRAFT PR")
    p.add_argument("--run-id", required=True)
    p.add_argument("--repo")
    p.add_argument("--create", action="store_true", help="push a branch to origin and open a draft PR")
    p.add_argument("--approve", action="store_true", help="explicit human authorization for --create")
    p.set_defaults(func=cmd_pr)

    p = sub.add_parser("capsule-schema", parents=[common], help="print the Proof-of-Fix capsule JSON Schema")
    p.set_defaults(func=cmd_schema)
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
