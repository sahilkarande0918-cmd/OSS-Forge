#!/usr/bin/env python3
"""Skill entry point: runs the shared `oss_forge` package CLI (no duplicated logic).

Usage: python .agents/skills/oss-forge/scripts/oss_forge.py <command> [options]
Works with an installed package (`pip install -e .`) or straight from a checkout of the OSS-Forge repo.
"""
import sys
from pathlib import Path

try:
    from oss_forge.cli import main
except ImportError:
    repo_root = Path(__file__).resolve().parents[4]
    if not (repo_root / "oss_forge").is_dir():
        sys.exit("oss_forge package not found: run `pip install oss-forge` or `pip install -e <OSS-Forge checkout>`")
    sys.path.insert(0, str(repo_root))
    from oss_forge.cli import main

raise SystemExit(main())
