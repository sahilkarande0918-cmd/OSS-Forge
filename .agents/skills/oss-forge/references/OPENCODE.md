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

## Running OpenCode itself on Gemma 4 (not verified)

OpenCode's providers are built on the AI SDK. Two configurations that *should* route OpenCode to Gemma 4
through the Gemini API; neither has been verified by this project, so test before relying on it:

1. Google provider with a custom model id (`GOOGLE_GENERATIVE_AI_API_KEY` set to your Gemini key):

   ```json
   {
     "provider": { "google": { "models": { "gemma-4-26b-a4b-it": { "name": "Gemma 4 26B A4B" } } } },
     "model": "google/gemma-4-26b-a4b-it"
   }
   ```

2. Gemini's OpenAI-compatible endpoint as a custom provider:

   ```json
   {
     "provider": {
       "gemini-openai": {
         "npm": "@ai-sdk/openai-compatible",
         "options": { "baseURL": "https://generativelanguage.googleapis.com/v1beta/openai/",
                      "apiKey": "{env:GEMINI_API_KEY}" },
         "models": { "gemma-4-26b-a4b-it": {} }
       }
     },
     "model": "gemini-openai/gemma-4-26b-a4b-it"
   }
   ```

Tool-calling quality of the host model matters for orchestration; OSS Forge's own Gemma calls do not use
function calling (structured JSON with schema validation and repair proved simpler and model-agnostic).
