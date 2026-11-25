"""`python -m rs_harbor.worlds DIGESTS_JSON`: check that this install builds the committed seeded worlds.

Both images run it at build time against results/certify/world_digests.json: every instance seed's world must hash
to the committed digest and the tool manifest to the committed sha256, or the build fails. Needs rs-world only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rs_world import REGISTRY
from rs_world.seed import world_digest
from rs_world.session import world_path


def differences(committed: dict) -> list[str]:
    found = []
    manifest = REGISTRY.manifest()["sha256"]
    if manifest != committed["registry_manifest_sha256"]:
        found.append(f"tool manifest {manifest} != {committed['registry_manifest_sha256']}")
    for seed, digest in committed["world_digests"].items():
        built = world_digest(world_path(int(seed)))
        if built != digest:
            found.append(f"world seed {seed}: {built} != {digest}")
    return found


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("usage: python -m rs_harbor.worlds results/certify/world_digests.json")
    committed = json.loads(Path(args[0]).read_text())
    found = differences(committed)
    for line in found:
        print(line, file=sys.stderr)
    if found:
        raise SystemExit(1)
    print(f"{len(committed['world_digests'])} seeded worlds and the tool manifest match {args[0]}")


if __name__ == "__main__":
    main()
