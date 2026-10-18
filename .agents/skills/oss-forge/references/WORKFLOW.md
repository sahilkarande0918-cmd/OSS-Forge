# OSS Forge workflow reference

## Pipeline

| step | module | API calls | output in run dir |
|---|---|---|---|
| repository map (cached by file fingerprint) | `analysis/repo_map.py` | 0 | `repo_map.md` |
| issue load + fact extraction | `workflows/issue_detective.py` | 0 | `issue.json` |
| interpretation (keywords, suspects, clarity) | Gemma | 1 | `interpretation.json` |
| context ranking + packing under budget | `analysis/context_engine.py` | 0 | `context.json` |
| hypotheses + regression test (+ expansions) | Gemma | 1-3 | `diagnosis.json`, `regression_test.py` |
| reproduction on original code (worktree) | `workflows/reproduce.py` | 0 (+1 regen) | `reproduction.json` |
| patch attempts with focused feedback | Gemma + `patch_guard.py` | 1-N | `attempts.json`, `patch.diff` |
| independent verification (fresh worktree) | `workflows/verify_fix.py` | 0 | `verification.json` |
| capsule + PR package | `capsule.py`, `pr_builder.py` | 0 | `capsule.json/.md`, `pr.md`, `pr.json`, `run.json` |

Runs live in `$OSS_FORGE_HOME/runs/<run_id>/` (default `.oss-forge/`).

## Context ranking signals

Each symbol's score is a sum of explainable signals (see `context.json` → `ranking[].reasons`):
stack-frame hit (15), exact name in issue (9), Gemma suspect (7), error-message literal in body (9),
file mentioned (6), file stem (3), name sub-words (1.5 each), TF-IDF similarity (≤4), raises the reported
exception (3), git history (≤1.5), plus 35% of a strong neighbour's score through call/import edges.
Definitions are extracted with exact AST line ranges and packed until the budget (55% of
`OSS_FORGE_MAX_CONTEXT_TOKENS`) is reached. Gemma may request up to 3 more definitions per round.

## Reproduction statuses

- `REPRODUCED`: assertion failure, or the exception type named in the issue.
- `NOT_REPRODUCED`: test passes on original code → run stops, status INCONCLUSIVE.
- `ENVIRONMENT_BLOCKED`: no sandbox/approval or missing third-party module → BLOCKED.
- `INCONCLUSIVE`: broken test (import/name errors, nothing collected); one regeneration is attempted.

## Patch guard rules

Allowed files = non-test, non-build files that were in the context shown to Gemma. Each `search` block
must match exactly once (CRLF and trailing whitespace tolerated). Max 3 files / 80 changed lines. Edited
Python must parse. Rejections and failing test output are returned to Gemma as focused feedback.

## Verification checks

`patch_applies`, `regression_fails_before`, `regression_passes_after`, `existing_suite_ran`,
`no_new_failures` (per-test JUnit comparison), `only_expected_files_changed`, `syntax_ok`,
`whitespace_ok`. VERIFIED requires the first five to be true and none false.
