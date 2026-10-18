# OSS Forge

**Turn unfamiliar repositories into verified, review-ready open-source contributions using Gemma 4.**

OSS Forge is an [Agent Skill](https://agentskills.io) (`.agents/skills/oss-forge/SKILL.md`) plus a Python CLI.
It pairs deterministic repository analysis with hosted Gemma 4 (`gemma-4-26b-a4b-it`) via the Gemini API.

> Work in progress: built in phases. See the commit history for what is implemented so far.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/pip install -e .      # Windows; use .venv/bin/pip elsewhere
cp .env.example .env                # then set GEMINI_API_KEY in .env
python -m oss_forge doctor
```

License: Apache-2.0 (OSS Forge code). Gemma model weights are governed by Google's separate Gemma terms.
