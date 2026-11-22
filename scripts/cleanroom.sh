#!/bin/sh
# Fails if any tracked file or commit message matches the private denylist (kept outside the repo).
DENY="${CLEANROOM_DENYLIST:-$(cd "$(dirname "$0")/../.." && pwd)/.private/denylist.txt}"
[ -f "$DENY" ] || { echo "cleanroom: no denylist at $DENY (skipped)"; exit 0; }
status=0
while IFS= read -r pat; do
  case "$pat" in ''|\#*) continue ;; esac
  if git ls-files -z | xargs -0 grep -nIiE "\b($pat)\b" -- 2>/dev/null | grep -v '^scripts/cleanroom.sh:'; then status=1; fi
  if git log --format=%B 2>/dev/null | grep -qiE "\b($pat)\b"; then echo "cleanroom: commit message matches /$pat/"; status=1; fi
done < "$DENY"
[ $status -eq 0 ] && echo "cleanroom: ok" || echo "cleanroom: FAILED"
exit $status
