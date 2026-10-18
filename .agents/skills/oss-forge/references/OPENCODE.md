# Using OSS Forge from OpenCode

## Skill discovery

OpenCode discovers project skills at `.agents/skills/<name>/SKILL.md` (also `.opencode/skills/` and
`.claude/skills/`), walking up from the working directory to the git root, and global skills in
`~/.config/opencode/skills/`, `~/.agents/skills/`, `~/.claude/skills/`. The agent loads OSS Forge via
its `skill` tool (`skill({ name: "oss-forge" })`) when a request matches the description.

To use OSS Forge on another repository, either install it globally:

```bash
pip install -e /path/to/OSS-Forge            # makes `oss_forge` importable everywhere
mkdir -p ~/.agents/skills && cp -r /path/to/OSS-Forge/.agents/skills/oss-forge ~/.agents/skills/
```

or start OpenCode inside the OSS-Forge checkout and pass `--repo /path/to/target`.

Permissions (optional, `opencode.json`):

```json
{ "permission": { "skill": { "oss-forge": "allow" } } }
```

## Which model does what

An Agent Skill does not change the host agent's model. Whatever model runs OpenCode reads `SKILL.md` and
calls the CLI; **the issue analysis, regression tests and patches are always produced by hosted Gemma 4**
inside the OSS Forge Python tooling (`oss_forge/ai/gemma_provider.py`, `GEMINI_API_KEY`).

## Running OpenCode itself on Gemma 4

Verified with OpenCode 1.19.3 (2026-10-18): `opencode debug skill` run in this repository lists `oss-forge`
from `.agents/skills/oss-forge/SKILL.md`, and `opencode models google` lists `google/gemma-4-26b-a4b-it` and
`google/gemma-4-31b-it` in the built-in catalog. Configure it with your Gemini key
(`opencode auth login` → Google, or `GOOGLE_GENERATIVE_AI_API_KEY`) and:

```json
{ "$schema": "https://opencode.ai/config.json", "model": "google/gemma-4-26b-a4b-it" }
```

**Not yet verified:** an OpenCode session actually running on Gemma 4 and loading/executing the skill end to
end (requires a configured key). Until verified, treat "OpenCode on Gemma 4" as expected, not confirmed.

Tool-calling quality of the host model matters for orchestration; OSS Forge's own Gemma calls do not use
function calling (structured JSON with schema validation and repair proved simpler and model-agnostic).
