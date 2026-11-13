#!/bin/sh
# Install or update wormhole + OwnTone on Debian 13 (trixie), amd64. Run as root:
#
#   /opt/wormhole/server/install.sh
#
# Safe to re-run (after updates that change server/ or config/owntone.conf.in). It:
#   - installs Debian packages and OwnTone's official .deb (version + checksum pinned)
#   - creates the system users `owntone` and `wormhole`, the data dirs and the PCM pipe
#   - writes /etc/wormhole/wormhole.env once (never overwrites it)
#   - renders /etc/owntone.conf from config/owntone.conf.in (stock file kept as .dist)
#   - installs and (re)starts the systemd services
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE=/etc/wormhole/wormhole.env

# OwnTone's own Debian build: https://github.com/owntone/owntone-debian/releases (dpkg_rev14).
# Not in Debian's repositories. Pinned so a re-run never changes OwnTone by surprise.
OWNTONE_VERSION="29.3.142.gitd6fb3ed-1+trixie"
OWNTONE_URL="https://github.com/owntone/owntone-debian/releases/download/dpkg_rev14"
OWNTONE_URL="$OWNTONE_URL/owntone_29.3.142.gitd6fb3ed-1%2Btrixie_amd64.deb"
OWNTONE_SHA256="76cbe423f78535906bf85396041134c8848f0935216bf3c9642ff6d9e0ac83a2"

say() { printf '\n== %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (e.g. after 'su -')"
. /etc/os-release
[ "${VERSION_CODENAME:-}" = trixie ] ||
    die "made for Debian 13 (trixie), this is ${PRETTY_NAME:-unknown}"
[ "$(dpkg --print-architecture)" = amd64 ] || die "OwnTone's .deb is amd64 only"

say "Debian packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    python3 ffmpeg git avahi-daemon ca-certificates curl

say "OwnTone $OWNTONE_VERSION"
if [ "$(dpkg-query -W -f='${Version}' owntone 2>/dev/null || true)" = "$OWNTONE_VERSION" ]; then
    echo "already installed"
else
    DEB=$(mktemp --suffix=.deb)
    curl -fsSL --retry 2 -o "$DEB" "$OWNTONE_URL"
    echo "$OWNTONE_SHA256  $DEB" | sha256sum -c --quiet || die "OwnTone .deb checksum mismatch"
    chmod 0644 "$DEB"  # apt's sandbox user must be able to read it
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$DEB"
    rm -f "$DEB"
fi

say "Users and directories"
for user in owntone wormhole; do
    id -u "$user" >/dev/null 2>&1 || useradd --system --user-group --no-create-home \
        --home-dir "/var/lib/$user" --shell /usr/sbin/nologin "$user"
done
install -d -o owntone -g owntone -m 0755 /var/cache/owntone
install -d -o wormhole -g wormhole -m 0755 /var/lib/wormhole
# The web UI updates this checkout with git, as the wormhole user.
chown -R wormhole:wormhole "$REPO"

say "Config $ENV_FILE"
install -d -m 0755 /etc/wormhole
if [ -f "$ENV_FILE" ]; then
    echo "kept existing file"
else
    install -m 0644 "$REPO/server/wormhole.env.example" "$ENV_FILE"
    echo "created from server/wormhole.env.example"
fi

# Run wormhole's Python as root without leaving root-owned __pycache__ in the checkout.
export WORMHOLE_ENV="$ENV_FILE" PYTHONPATH="$REPO/src" PYTHONDONTWRITEBYTECODE=1
wormhole() { python3 -m wormhole "$@"; }
PIPE=$(python3 -c 'from wormhole import config; print(config.path(config.load(), "PCM_PIPE"))')

say "PCM pipe $PIPE"
install -d -o wormhole -g wormhole -m 0755 "$(dirname "$PIPE")"
if [ -e "$PIPE" ] && [ ! -p "$PIPE" ]; then
    die "$PIPE exists but is not a named pipe"
fi
[ -p "$PIPE" ] || mkfifo "$PIPE"
# wormhole writes, OwnTone (group owntone) reads.
chown wormhole:owntone "$PIPE"
chmod 0640 "$PIPE"

say "OwnTone config /etc/owntone.conf"
[ -f /etc/owntone.conf.dist ] || cp /etc/owntone.conf /etc/owntone.conf.dist
wormhole owntone-config --user owntone --data-dir /var/cache/owntone \
    --logfile /var/log/owntone.log --name "wormhole on %h" -o /etc/owntone.conf.new
mv /etc/owntone.conf.new /etc/owntone.conf
echo "rendered from config/owntone.conf.in (stock file: /etc/owntone.conf.dist)"

say "Services"
sed "s|@REPO@|$REPO|g" "$REPO/server/wormhole.service" > /etc/systemd/system/wormhole.service
systemctl daemon-reload
systemctl enable --quiet avahi-daemon owntone wormhole
systemctl restart avahi-daemon
systemctl restart owntone
systemctl restart wormhole

say "Check"
PORT=$(sed -n 's/^WEB_PORT=//p' "$ENV_FILE" | tr -d '"' | head -1)
PORT=${PORT:-8766}
ok=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -fs "http://localhost:$PORT/api/update" >/dev/null; then ok=yes; break; fi
done
systemctl --no-pager --lines=0 status owntone wormhole | grep -E "^\S|Active:" || true
if [ -z "$ok" ]; then
    echo "wormhole did not answer on port $PORT. Logs: journalctl -u wormhole -n 50"
    exit 1
fi
cat <<EOF

wormhole is running. Web UI:
  http://$(hostname).local:$PORT
  http://$(hostname -I | awk '{print $1}'):$PORT
Logs: journalctl -u wormhole -f    OwnTone: /var/log/owntone.log
EOF
