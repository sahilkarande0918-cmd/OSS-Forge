"""Create a standalone git copy of the shopkit benchmark repository for demos.

    python examples/setup_demo_repo.py ../shopkit-demo
"""
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "benchmarks" / "cases" / "shopkit" / "repo"


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "../shopkit-demo").resolve()
    if dest.exists():
        print(f"{dest} already exists; remove it or choose another path")
        return 1
    shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    ident = ["-c", "user.email=demo@oss-forge.local", "-c", "user.name=demo"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "shopkit 1.4.2"]):
        subprocess.run(["git", *ident, *args], cwd=dest, check=True)
    print(f"demo repository ready: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
