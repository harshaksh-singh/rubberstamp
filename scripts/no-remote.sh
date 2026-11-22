#!/bin/sh
# Local-only policy: fail if any git remote is configured.
[ -z "$(git remote)" ] && echo "no-remote: ok" || { echo "no-remote: FAILED, remotes: $(git remote)"; exit 1; }
