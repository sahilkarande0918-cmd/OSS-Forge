# OSS Forge demo script (≈5 minutes)

Everything below is real command output on your machine; nothing is pre-recorded. Steps 1–4 need no API key.

## 0. Setup (once)

```bash
pip install -e ".[dev]"
cp .env.example .env            # set GEMINI_API_KEY
python -m oss_forge doctor --live
python examples/setup_demo_repo.py ../shopkit-demo     # an "unfamiliar" 14-module repo with real bugs
```

## 1. Unfamiliar repository → instant map (0 API calls)

```bash
python -m oss_forge map --repo ../shopkit-demo
```

Show: languages, entry point `shopkit.cli:main`, pytest command, CONTRIBUTING.md, most-imported files.
Run it twice: the second run is a cache hit.

## 2. Environment doctor

```bash
python -m oss_forge env --repo ../shopkit-demo
```

Show the setup plan, and that the dependency-install steps are marked *requires approval*.

## 3. Bug report → relevant context (0 API calls)

```bash
python -m oss_forge context --repo ../shopkit-demo --issue examples/issue-checkout-tax.md --budget 2500
```

Point out: `checkout_total` and `compute_tax` ranked with reasons; context ≈2.4k tokens of a ≈5.3k-token repo.
The issue never names `checkout.py`.

## 4. Safety rails

```bash
python -m oss_forge solve --repo ../shopkit-demo --issue examples/issue-median.md --dry-run
```

Dry run: Gemma diagnoses and proposes a patch; nothing from the repo executes; status is INCONCLUSIVE and
the patch is labelled UNVERIFIED.

## 5. Full run: reproduce → patch → verify (Gemma 4)

```bash
python -m oss_forge solve --repo ../shopkit-demo --issue examples/issue-median.md --execute --allow-host-exec
```

(`--allow-host-exec` is the human approval to run the demo repo's tests on the host; with Docker running it
is not needed.) Narrate the log: interpretation → context → regression test → `REPRODUCED` → patch attempt(s)
→ independent verification in a fresh worktree.

## 6. Proof-of-Fix Capsule and PR draft

Open `<run_dir>/capsule.md`: status, checks (fails before / passes after / no new failures / only expected
files), test-evidence table with exit codes and durations, diff, regression test, limitations, reproduction
commands. Then:

```bash
python -m oss_forge pr --run-id <run_id>
python -m oss_forge verify --run-id <run_id> --allow-host-exec    # anyone can re-check the evidence
```

## 7. Honest abstention

```bash
python -m oss_forge solve --repo ../shopkit-demo --issue examples/issue-unclear.md --execute --allow-host-exec
```

Expected: INCONCLUSIVE with the missing information listed, and no patch.

## 8. Metrics

Each capsule's `usage` and `context` blocks give the API calls, prompt and output tokens (as reported by the
API), and context tokens versus repository size. For the comparison:

```bash
python -m oss_forge benchmark --suite smoke --mode context
python -m oss_forge benchmark --suite smoke --mode live --allow-host-exec
```
