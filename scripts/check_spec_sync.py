#!/usr/bin/env python3
"""Spec drift guard (pre-commit) for Sleepless.

Two deterministic checks that keep specs/ honest with the code:

  1. BLOCKING - every field on the @dataclass Config in sleepless.py must be
     documented somewhere under specs/. Adding a config field without
     documenting it fails the check (and the commit).
  2. WARNING - if sleepless.py is staged but no specs/ file is, print a
     non-blocking reminder to consider a spec update.

Run directly:  python3 scripts/check_spec_sync.py
Run via make:  make spec-check
Bypass once:   git commit --no-verify
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

SPEC_DIR = "specs/sleepless"


def repo_root() -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(__file__).resolve().parent.parent


def config_fields(source: Path) -> list[str]:
    """Field names declared on the Config dataclass in sleepless.py."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Config":
            return [stmt.target.id for stmt in node.body
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)]
    return []


def spec_text(root: Path) -> str:
    parts = []
    specs = root / "specs"
    if specs.is_dir():
        for path in sorted(specs.rglob("*.md")):
            try:
                parts.append(path.read_text(encoding="utf-8"))
            except OSError:
                pass
    return "\n".join(parts)


def staged_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "diff", "--cached", "--name-only"],
                             cwd=root, capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return [line for line in out.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def main() -> int:
    root = repo_root()
    app = root / "sleepless.py"
    if not app.is_file():
        print("spec-check: sleepless.py not found; skipping.", file=sys.stderr)
        return 0

    try:
        fields = config_fields(app)
    except SyntaxError as exc:
        print(f"spec-check: FAIL - could not parse sleepless.py ({exc}).", file=sys.stderr)
        return 1
    if not fields:
        print("spec-check: FAIL - no Config dataclass fields found in sleepless.py.", file=sys.stderr)
        return 1

    text = spec_text(root)
    undocumented = [f for f in fields if not re.search(rf"\b{re.escape(f)}\b", text)]

    status = 0
    if undocumented:
        print("spec-check: FAIL - Config field(s) not documented under specs/:", file=sys.stderr)
        for field in undocumented:
            print(f"  - {field}", file=sys.stderr)
        print(f"Document them in {SPEC_DIR}/data-model.md and "
              "contracts/config-schema.md, then re-stage. Bypass with --no-verify.",
              file=sys.stderr)
        status = 1

    staged = staged_files(root)
    if staged and "sleepless.py" in staged and not any(s.startswith("specs/") for s in staged):
        print("spec-check: WARNING - sleepless.py is staged but no specs/ file changed. "
              f"If behavior or requirements changed, update {SPEC_DIR}/ "
              "(reminder only, not blocking).", file=sys.stderr)

    if status == 0:
        print(f"spec-check: OK - all {len(fields)} Config field(s) documented in specs/.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
