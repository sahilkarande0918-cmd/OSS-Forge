# Proof-of-Fix capsule

Each run writes `capsule.json` (validated against the Pydantic model `ProofOfFixCapsule`, schema version
1.0) and a readable `capsule.md`. Print the JSON Schema with:

```bash
python .agents/skills/oss-forge/scripts/oss_forge.py capsule-schema
```

Key fields:

| field | meaning |
|---|---|
| `verification_status` / `verification_summary` | VERIFIED, PARTIALLY_VERIFIED, FAILED, INCONCLUSIVE, BLOCKED |
| `verification_checks` | boolean (or null = not evaluated) per check |
| `base_commit` | commit the patch and evidence refer to |
| `reproduction` | status, reason, exit code, runner, output tail of the first reproduction |
| `test_evidence[]` | every test run: label, argv, exit code, duration, runner, pass/fail counts, output tail |
| `patch`, `changed_files[]` | unified diff (applies with `git apply`) and per-file +/- line counts |
| `regression_test` | path and full source of the generated test |
| `hypotheses[]`, `root_cause` | model explanations (not evidence) |
| `source_locations[]` | top-ranked definitions with line ranges and ranking reasons |
| `model`, `usage` | Gemma model id, API calls/requests, prompt/output tokens as reported by the API |
| `context` | context tokens sent vs estimated repository tokens, files/symbols inspected |
| `limitations[]`, `reproduction_commands[]` | caveats and commands to re-check the evidence |

Capsules exclude API keys, environment values and whole source files; only touched definitions and test
output tails are stored.
