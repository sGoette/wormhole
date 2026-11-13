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

mkdir -p "$PIPE_DIR" "$DATA/cache" "$ROOT/tools/media"
if [ -e "$PIPE" ] && [ ! -p "$PIPE" ]; then
    echo "$PIPE exists but is not a named pipe" >&2
    exit 1
fi
[ -p "$PIPE" ] || mkfifo "$PIPE"

# OwnTone's own "debug" logs every SQL query; "info" is the useful level for our DEBUG.
case "${LOG_LEVEL:-INFO}" in
    DEBUG) LOGLEVEL=info ;;
    WARNING) LOGLEVEL=warning ;;
    *) LOGLEVEL=log ;;
esac

sed -e "s|@USER@|$(id -un)|" \
    -e "s|@DATA@|$DATA|g" \
    -e "s|@LOGLEVEL@|$LOGLEVEL|" \
    -e "s|@PORT@|$PORT|" \
    -e "s|@PIPE_DIR@|$PIPE_DIR|" \
    -e "s|@MEDIA@|$ROOT/tools/media|" \
    -e "s|@PIPE_SAMPLE_RATE@|$PIPE_SAMPLE_RATE|" \
    -e "s|@PIPE_BITS@|$PIPE_BITS|" \
    tools/owntone-mac/owntone.conf.in > "$CONF"

echo "config:  $CONF"
echo "pipe:    $PIPE ($PIPE_SAMPLE_RATE Hz, $PIPE_BITS bit, stereo)"
echo "log:     $DATA/owntone.log"
echo "web UI:  http://localhost:$PORT"
# --mdns-no-cname: macOS refuses the owntone.local alias (error -65540); not needed here.
exec "$OWNTONE" -f -c "$CONF" --mdns-no-cname
