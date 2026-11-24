"""Write results/certify/world_digests.json: the snapshot digest of every instance seed's world (42 and 1-19) and the
sha256 of the tool manifest.

A fresh process, another OS or Python, or a container image that rebuilds a world must reproduce these digests
(the seed-42 digest is also pinned by rs-world's tests). Changing any of them is a deliberate act: it changes every
instance built on that world.

Usage: uv run python scripts/world_digests.py            # write the file
       uv run python scripts/world_digests.py --check    # exit 1 if it differs from a fresh build
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rs_suite.conformance import world_digests

OUT = Path(__file__).resolve().parents[1] / "results" / "certify" / "world_digests.json"


def text() -> str:
    return json.dumps(world_digests(), indent=1) + "\n"


def main(check: bool) -> int:
    fresh = text()
    if check:
        if not OUT.is_file() or OUT.read_text() != fresh:
            print(f"stale: {OUT} (run scripts/world_digests.py)")
            return 1
        print(f"checked {OUT}")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(fresh)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="compare with the file instead of writing it")
    sys.exit(main(parser.parse_args().check))
