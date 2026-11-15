#!/usr/bin/env bash
# Deploy the Lens server to the Hostinger VPS with the commit stamped in BUILD (shown on /api/info).
# It refuses while a phone broadcast runs (a deploy clears the in-memory state). --force skips that check.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$HERE" rev-parse --show-toplevel)"
URL="https://lens.srv2159196.hstgr.cloud"
if [ "${1:-}" != "--force" ]; then
  LIVE="$(curl -s --max-time 10 "$URL/debug.json" | python3 -c '
import json, sys, time
d = json.load(sys.stdin)
now = time.time()
print(" ".join(sorted({h.get("session", "?") for h in d.get("heartbeats", []) if h.get("source") == "broadcast"
                         and h.get("state") == "running" and now - h.get("ts", 0) < 30})))' || true)"
  [ -z "$LIVE" ] || { echo "a broadcast is running ($LIVE): wait until it ends, or pass --force" >&2; exit 1; }
fi
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD)"
git -C "$ROOT" diff --quiet HEAD -- hostinger-noise/ios/server || COMMIT="$COMMIT-dirty"
echo "$COMMIT" >"$HERE/BUILD"
trap 'rm -f "$HERE/BUILD"' EXIT
"$ROOT/infra/hostinger/host.sh" hostinger-noise/ios/server lens.srv2159196.hstgr.cloud 3000
git -C "$ROOT" checkout -- infra/hostinger/apps.tsv 2>/dev/null || true
curl -s --max-time 10 "$URL/api/info"
echo
python3 "$HERE/warm_cache.py" --server "$URL" || echo "warm_cache.py: a demo claim is not stored" >&2
