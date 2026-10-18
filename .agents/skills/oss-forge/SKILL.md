---
name: oss-forge
description: Investigates a bug report or GitHub issue in an unfamiliar repository and prepares a minimal, test-verified fix with a Proof-of-Fix capsule and PR draft, using hosted Gemma 4 plus deterministic repository analysis. Use when the user asks to fix an issue, reproduce a bug, prepare an open-source contribution, or understand an unfamiliar codebase.
license: Apache-2.0
compatibility: Requires Python 3.10+, git, network access to the Gemini API (GEMINI_API_KEY). Optional gh CLI and Docker.
metadata:
  author: sahilkarande0918-cmd
  version: "0.1.0"
---

# OSS Forge

Status: foundation only. Run the environment check before anything else:

```bash
python .agents/skills/oss-forge/scripts/oss_forge.py doctor --json
```

If `gemini-api-key` is not ok, stop and tell the user to copy `.env.example` to `.env` and set
`GEMINI_API_KEY`. Never ask the user to paste the key into chat.
