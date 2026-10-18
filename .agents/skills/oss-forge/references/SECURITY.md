# Security model

OSS Forge treats every target repository and issue as potentially hostile.

## Implemented controls

- **No shell strings.** Every subprocess is a fixed argv from OSS Forge code; executables are allowlisted
  (`git`, `gh`, `python`, `docker`, `node`, `npm`, `npx`). Model output can never add a command.
- **Sandboxed tests.** With Docker available, tests run in `python:3.12-slim` + pytest with
  `--network none`, 1 CPU, 1 GB RAM, 256 PIDs, `no-new-privileges`, non-root user, only the throwaway
  worktree mounted. Without Docker, execution is refused unless `--allow-host-exec` (human approval).
- **Scrubbed environment.** Variables whose names contain KEY/TOKEN/SECRET/PASSWORD/AUTH/… are removed
  from child processes (except `gh`/`git push` when explicitly creating an approved PR).
- **Path confinement.** Edits and new tests must resolve inside the worktree: absolute paths, `..`,
  escaping symlinks and secret-like files (`.env*`, `*.pem`, `id_rsa`, …) are rejected.
- **Generated tests are statically checked** (no subprocess/socket/shutil/ctypes imports, no
  eval/exec/system calls) before they are run.
- **Prompt hygiene.** Issue/code text is wrapped in `<untrusted>` tags; the system prompt tells Gemma
  to treat it as data. Credential patterns and the API key are redacted from prompts, logs, capsules.
- **Human checkout untouched.** Work happens in temporary `git worktree`s; uncommitted user changes are
  never read into the patch or discarded.
- **Publishing gates.** Pushing/opening PRs requires `pr --create --approve`, refuses non-GitHub
  remotes and unverified runs, opens drafts only, never merges or force-pushes.

## Known limitations (be honest about these)

- Host mode (`--allow-host-exec`) runs repository code with your user's permissions; the scrubbed env
  and timeouts are not a sandbox.
- The Docker image only contains pytest; repositories with third-party dependencies are BLOCKED in
  Docker until a project-specific image is provided (not yet automated).
- `git worktree add` and `git log` run git on the target repo; malicious git hooks are not triggered by
  these commands, but git config of the target repository is honoured.
- Prompt-injection resistance depends on the model following instructions; the guardrails above limit
  the blast radius (no command execution, scoped edits) rather than guaranteeing it.
- Not yet suitable for running arbitrary hostile repositories unattended.
