#!/bin/sh
# Prepare vendor/ for the Docker build (server/Dockerfile): the build context is this package and cannot reach
# ../../../packages, so the rs-* packages go in as wheels, and the third-party dependencies as exact pins with hashes
# exported from this project's uv.lock (no dev group, no extras: the server does not need TRL).
#
#   sh scripts/vendor_wheels.sh
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
root=$(cd "$here/../../.." && pwd)
rm -rf "$here/vendor"
mkdir -p "$here/vendor"
for package in rs-core rs-verify rs-world rs-suite; do
  uv build --quiet --wheel --out-dir "$here/vendor" "$root/packages/$package"
done
cd "$here"
uv export --quiet --frozen --no-dev --no-emit-project --no-emit-local --format requirements-txt \
  --output-file vendor/requirements.txt
ls vendor
