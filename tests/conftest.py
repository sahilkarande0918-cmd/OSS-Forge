"""Shared test helpers. FakeClient stands in for google-genai in unit tests only; live tests use the real API."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from oss_forge.config import Config


class FakeClient:
    """Mimics `genai.Client().models.generate_content`; replies are consumed in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.models = self

    def generate_content(self, model, contents, config=None):
        self.prompts.append(contents)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        usage = SimpleNamespace(prompt_token_count=len(contents) // 4, candidates_token_count=len(text) // 4,
                                thoughts_token_count=0)
        return SimpleNamespace(text=text, usage_metadata=usage)


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(api_key="test-key-not-real", home=tmp_path / "home", dry_run=False, max_api_calls=20,
                  max_context_tokens=12000)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def make_repo(root: Path, files: dict[str, str]) -> Path:
    """Create a real git repository with one commit."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
    git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return root
