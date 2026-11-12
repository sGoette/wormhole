#!/bin/sh
# Deploy to the NAS. Usage: deploy/deploy.sh {deploy|dry-run|rollback|status|logs|ssh}
#
# Layout on the NAS:
#   $NAS_PATH/releases/<timestamp>-<sha>/   one directory per deploy
#   $NAS_PATH/current -> releases/<...>     what services run
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE=${DEPLOY_ENV:-$ROOT/config/nas.env}   # DEPLOY_ENV lets you target another box

if [ ! -f "$ENV_FILE" ]; then
    echo "Missing $ENV_FILE — copy config/nas.env.example and fill it in." >&2
    exit 1
fi
# shellcheck disable=SC1090
. "$ENV_FILE"
: "${NAS_SSH:?set NAS_SSH in config/nas.env}"
: "${NAS_PATH:?set NAS_PATH in config/nas.env}"
NAS_PYTHON=${NAS_PYTHON:-python3}
RESTART_CMD=${RESTART_CMD:-}
NAS_LOG=${NAS_LOG:-/var/log/wormhole.log}
KEEP_RELEASES=${KEEP_RELEASES:-5}

remote() { ssh "$NAS_SSH" "$@"; }

# What gets shipped. Mac-only things (tools/, results/, deploy/, claude/) stay home.
SHIP="bin src nas"

release_name() {
    sha=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)
    dirty=$(git -C "$ROOT" status --porcelain 2>/dev/null | head -1)
    [ -n "$dirty" ] && sha="$sha-dirty"
    echo "$(date +%Y%m%d-%H%M%S)-$sha"
}

stage() {
    # Build the release in a local temp dir so rsync sees exactly what will run.
    STAGE=$(mktemp -d)
    trap 'rm -rf "$STAGE"' EXIT
    for d in $SHIP; do cp -R "$ROOT/$d" "$STAGE/"; done
    mkdir -p "$STAGE/config"
    cp "$ENV_FILE" "$STAGE/config/active.env"
    echo "$REL" > "$STAGE/VERSION"
    find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
    find "$STAGE" -name .DS_Store -delete
}

smoke() {
    echo "== smoke test"
    remote "WORMHOLE_PYTHON=$NAS_PYTHON $NAS_PATH/current/bin/wormhole info"
}

case "${1:-deploy}" in
deploy)
    REL=$(release_name)
    stage
    echo "== deploying $REL to $NAS_SSH:$NAS_PATH"
    remote "mkdir -p $NAS_PATH/releases"
    rsync -rlptz --delete "$STAGE/" "$NAS_SSH:$NAS_PATH/releases/$REL/"
    # ln -sfn swaps the link in one step on both FreeBSD and macOS.
    remote "cd $NAS_PATH && ln -sfn releases/$REL current \
        && ls -1t releases | tail -n +$((KEEP_RELEASES + 1)) | sed 's|^|releases/|' | xargs rm -rf"
    if [ -n "$RESTART_CMD" ]; then
        echo "== restart: $RESTART_CMD"
        remote "$RESTART_CMD"
    fi
    smoke
    ;;
dry-run)
    REL=dry-run
    stage
    rsync -rlptzn --delete -v "$STAGE/" "$NAS_SSH:$NAS_PATH/releases/dry-run/"
    ;;
rollback)
    remote "cd $NAS_PATH && cur=\$(readlink current | sed 's|releases/||') \
        && prev=\$(ls -1t releases | grep -vx \"\$cur\" | head -1) \
        && [ -n \"\$prev\" ] && ln -sfn releases/\$prev current && echo \"rolled back: \$cur -> \$prev\""
    [ -n "$RESTART_CMD" ] && remote "$RESTART_CMD"
    smoke
    ;;
status)
    remote "cd $NAS_PATH && echo live: \$(readlink current) && echo releases: && ls -1t releases"
    smoke
    ;;
logs)
    remote "tail -n 100 -F $NAS_LOG"
    ;;
ssh)
    exec ssh -t "$NAS_SSH" "cd $NAS_PATH/current 2>/dev/null; exec \$SHELL -l"
    ;;
*)
    echo "usage: $0 {deploy|dry-run|rollback|status|logs|ssh}" >&2
    exit 2
    ;;
esac
