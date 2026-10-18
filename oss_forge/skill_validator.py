"""Validate a skill directory against the Agent Skills specification (https://agentskills.io/specification)."""
from __future__ import annotations

import re
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALLOWED_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse the YAML subset the spec uses: scalars plus one level of `metadata:` string map."""
    if not text.startswith("---"):
        raise ValueError("SKILL.md must start with YAML frontmatter ('---')")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("frontmatter is not closed with '---'")
    data: dict = {}
    current_map: dict | None = None
    for line in text[3:end].strip("\n").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if current_map is None:
                raise ValueError(f"unexpected indented line: {line!r}")
            key, _, value = line.strip().partition(":")
            current_map[key.strip()] = value.strip().strip('"').strip("'")
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"invalid frontmatter line: {line!r}")
        key, value = key.strip(), value.strip()
        if value == "":
            current_map = data[key] = {}
        else:
            current_map = None
            data[key] = value.strip('"').strip("'")
    return data, text[end + 4:]


def validate_skill(skill_dir: Path) -> list[str]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"missing {skill_md}"]
    try:
        meta, body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [str(exc)]
    errors = [f"unknown frontmatter field: {k}" for k in meta if k not in ALLOWED_FIELDS]
    name = meta.get("name", "")
    if not name:
        errors.append("name is required")
    elif len(name) > 64 or not NAME_RE.match(name):
        errors.append("name must be 1-64 chars of lowercase letters, digits and single hyphens")
    elif name != skill_dir.resolve().name:
        errors.append(f"name '{name}' must match directory name '{skill_dir.resolve().name}'")
    desc = meta.get("description", "")
    if not desc or len(desc) > 1024:
        errors.append("description is required and must be <= 1024 characters")
    if "compatibility" in meta and not (1 <= len(meta["compatibility"]) <= 500):
        errors.append("compatibility must be 1-500 characters")
    if "metadata" in meta and not isinstance(meta["metadata"], dict):
        errors.append("metadata must be a string-to-string map")
    if len(body.splitlines()) > 500:
        errors.append("SKILL.md body should stay under 500 lines")
    for ref in re.findall(r"\]\(((?:references|scripts|assets)/[^)]+)\)", body):
        if not (skill_dir / ref).exists():
            errors.append(f"referenced file does not exist: {ref}")
    return errors
