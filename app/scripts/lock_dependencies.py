"""Regenerate hash-locked Python dependencies with an explicit build tool.

Run from the release-tools environment containing pip-tools==7.5.2. This
command resolves packages; the normal build only consumes committed locks.
"""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    from importlib.metadata import version
    if version("pip-tools") != "7.5.2":
        raise SystemExit("Use pip-tools==7.5.2 to regenerate release locks")
    environment = {**os.environ, "CUSTOM_COMPILE_COMMAND": "python scripts/lock_dependencies.py"}
    targets = (
        ("requirements.lock", "pyproject.toml", []),
        ("requirements-dev.lock", "pyproject.toml", ["--extra", "dev"]),
        ("requirements-build.lock", "requirements-build.in", []),
    )
    for output, source, extra in targets:
        subprocess.run([sys.executable, "-m", "piptools", "compile", "--quiet",
            "--allow-unsafe", "--generate-hashes", "--strip-extras",
            "--index-url", "https://pypi.org/simple", "--output-file", output,
            *extra, source], cwd=ROOT, env=environment, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
