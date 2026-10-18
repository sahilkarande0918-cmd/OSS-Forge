# Contributing to OSS Forge

Thanks for helping! OSS Forge's rule for itself is the same one it enforces on patches: every claim needs
evidence.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Linux/macOS: .venv/bin/pip
python -m pytest                          # unit + integration (no API key needed)
python -m pytest -m live                  # real Gemma 4 calls; needs GEMINI_API_KEY in .env
ruff check oss_forge tests
agentskills validate .agents/skills/oss-forge
```

## Guidelines

- Keep pull requests small and focused; add or update tests for every behaviour change.
- Deterministic work belongs in Python; reserve Gemma calls for interpretation and generation, and keep
  prompts inside the configured token budget.
- Never weaken a safety check (argv allowlist, sandbox decision, patch-guard scope, redaction) without a test
  and a note in `references/SECURITY.md`.
- Benchmark numbers in docs must come from files in `benchmarks/results/` produced by the harness.
- Never commit `.env`, API keys, run artifacts (`.oss-forge/`) or caches.
- New benchmark cases: add the repository under `benchmarks/cases/`, a hidden oracle test, and an entry in
  `cases.json` with ground-truth files/symbols and the expected outcome (`fixable` or `abstain`).
