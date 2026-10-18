# Advanced features

## Repository memory

`$F memory --repo R [--refresh]` shows what OSS Forge remembers about a repository
(`$OSS_FORGE_HOME/memory/<repo_id>.json`): architecture summary, test command, conventions, frequently called
symbols, verified fixes and failed attempts. Each remembered run stores the git blob hashes of the files it
touched; when those files change, the entry is reported as stale and no longer reused. During `solve`, fresh
entries whose wording overlaps the new issue are passed to Gemma as labelled hints (never as evidence).

## CI failure recovery

```bash
$F ci-repair --run-id ID --log ci.log                 # offline triage of a saved log (no GitHub access)
$F ci-repair --run-id ID --pr 42 --use-gemma          # read-only: gh fetches the failed run's log
$F ci-repair --run-id ID --pr 42 --execute [--allow-host-exec]   # also re-run the failing tests locally
```

Separates environment failures (missing modules, dependency resolution, network, disk, rate limits) from
code failures (failed tests, assertions, syntax, lint), correlates log file paths with the files the run
modified, optionally asks Gemma for an advisory analysis, re-runs the failing tests in a worktree with the
run's patch, and appends the result to the capsule. It never re-runs or edits remote workflows and does not
yet generate a corrective patch automatically.

## Maintainer mode

```bash
$F triage --repo R --limit 30          # uses gh issue list (read-only)
$F triage --repo R --issues-file issues.json
```

Ranks open issues: likely duplicates and related issues (TF-IDF cosine), complexity from clarity signals and
how well the context engine localizes the issue, and whether it is a good automation candidate. Each
candidate gets its own plan (dry run → review → execute → separate draft PR after approval). It never opens
PRs, comments or labels; mass submission is intentionally unsupported.
