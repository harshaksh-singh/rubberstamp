"""Check that the release metadata agrees across the repository.

One version for the workspace root, every member in packages/*, CITATION.cff and apps/helpdesk/package.json; every
member declares the root's license, license file (a byte-identical copy of the root LICENSE), authors, Python range
and project URLs. CI and the pre-commit hook run it.

Usage: uv run python scripts/check_metadata.py
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAME_AS_ROOT = ("version", "license", "license-files", "authors", "requires-python")


def project(path: Path) -> dict:
    return tomllib.loads(path.read_text())["project"]


def cff_field(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}:\s*(.+?)\s*$", text, flags=re.M)
    return match.group(1).strip("'\"") if match else None


def problems(root: Path = ROOT) -> list[str]:
    found: list[str] = []
    top = project(root / "pyproject.toml")
    version = top["version"]
    licence = (root / "LICENSE").read_bytes()
    members = sorted(root.glob("packages/*/pyproject.toml"))
    if not members:
        found.append("no packages/*/pyproject.toml found")
    for path in members:
        name = path.parent.relative_to(root)
        meta = project(path)
        for key in SAME_AS_ROOT:
            if meta.get(key) != top.get(key):
                found.append(f"{name}: {key} is {meta.get(key)!r}, the root has {top.get(key)!r}")
        if set(meta.get("urls", {})) != set(top.get("urls", {})):
            found.append(f"{name}: project.urls keys {sorted(meta.get('urls', {}))} differ from the root's")
        for file in meta.get("license-files", []):
            copy = path.parent / file
            if not copy.is_file() or copy.read_bytes() != licence:
                found.append(f"{name}/{file} is missing or differs from the root LICENSE")
    cff = (root / "CITATION.cff").read_text()
    if cff_field(cff, "version") != version:
        found.append(f"CITATION.cff: version is {cff_field(cff, 'version')!r}, the root has {version!r}")
    if cff_field(cff, "license") != top.get("license"):
        found.append(f"CITATION.cff: license is {cff_field(cff, 'license')!r}, the root has {top.get('license')!r}")
    app = json.loads((root / "apps" / "helpdesk" / "package.json").read_text())
    if app.get("version") != version:
        found.append(f"apps/helpdesk/package.json: version is {app.get('version')!r}, the root has {version!r}")
    return found


def main() -> int:
    found = problems()
    for problem in found:
        print(problem, file=sys.stderr)
    if found:
        return 1
    print(f"metadata: ok (version {project(ROOT / 'pyproject.toml')['version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
