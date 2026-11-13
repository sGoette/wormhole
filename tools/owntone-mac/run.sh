#!/bin/sh
# Run the locally built OwnTone in the foreground with settings from config/local.env.
# Web UI: http://localhost:<port from OWNTONE_URL>. Ctrl+C to stop.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
BASE="$ROOT/.local/owntone"
OWNTONE="$BASE/usr/sbin/owntone"
CONF="$BASE/owntone.conf"

[ -x "$OWNTONE" ] || { echo "OwnTone not built yet: run make owntone-build" >&2; exit 1; }

# shellcheck disable=SC1091
. config/local.env
PORT=$(echo "$OWNTONE_URL" | sed -E 's|.*:([0-9]+).*|\1|')
case "$PCM_PIPE" in /*) PIPE="$PCM_PIPE" ;; *) PIPE="$ROOT/$PCM_PIPE" ;; esac
PIPE_DIR=$(dirname "$PIPE")
DATA="$BASE/var"

mkdir -p "$PIPE_DIR" "$DATA" "$ROOT/tools/media"
if [ -e "$PIPE" ] && [ ! -p "$PIPE" ]; then
    echo "$PIPE exists but is not a named pipe" >&2
    exit 1
fi
[ -p "$PIPE" ] || mkfifo "$PIPE"

# Same template as the server (config/owntone.conf.in); tools/media added for local tests.
WORMHOLE_ENV=config/local.env bin/wormhole owntone-config --user "$(id -un)" \
    --data-dir "$DATA" --logfile "$DATA/owntone.log" --dir "$ROOT/tools/media" -o "$CONF"

echo "config:  $CONF"
echo "pipe:    $PIPE ($PIPE_SAMPLE_RATE Hz, $PIPE_BITS bit, stereo)"
echo "log:     $DATA/owntone.log"
echo "web UI:  http://localhost:$PORT"
# --mdns-no-cname: macOS refuses the owntone.local alias (error -65540); not needed here.
exec "$OWNTONE" -f -c "$CONF" --mdns-no-cname
