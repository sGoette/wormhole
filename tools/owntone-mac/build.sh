#!/bin/sh
# Build OwnTone on the Mac for Phase 3 (no Homebrew formula exists).
#
# Follows OwnTone's own macOS CI (.github/workflows/macos.yml) with two changes:
# - uses the release tarballs, which ship generated parsers and `configure`, so no
#   bison/flex/gperf/autotools and no sudo symlinks are needed;
# - installs into .local/owntone/ inside the repo. Only the Homebrew deps are system-wide.
#
# Re-running is safe: downloads are cached and checksum-verified.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BASE="$ROOT/.local/owntone"
PREFIX="$BASE/usr"
SRC="$BASE/src"

OWNTONE_VERSION=29.4
OWNTONE_SHA256=124f22ede1ab6c5ee256feb4b3a75443e1c15e447829632befe309db2a3bed8c
OWNTONE_URL="https://github.com/owntone/owntone-server/releases/download/$OWNTONE_VERSION"
OWNTONE_URL="$OWNTONE_URL/owntone-$OWNTONE_VERSION.tar.xz"
INOTIFY_VERSION=20240724
INOTIFY_SHA256=5cc3fb7af407b17b7daa871cc98bb882716c4b5c296fadfb66bfe86c37cc599c
INOTIFY_URL="https://github.com/libinotify-kqueue/libinotify-kqueue/releases/download"
INOTIFY_URL="$INOTIFY_URL/$INOTIFY_VERSION/libinotify-$INOTIFY_VERSION.tar.gz"

# From the `Package requirements` checks in OwnTone's configure. Last.fm and Chromecast are
# disabled below, so gnutls is not needed. protobuf-c is: the bundled librespot-c configure
# runs (and requires it) even with --disable-spotify.
BREW_DEPS="pkg-config libunistring confuse libplist libwebsockets libevent libgcrypt json-c
libsodium openssl@3 ffmpeg sqlite curl libxml2 zlib protobuf-c"
# Keg-only formulas: pkg-config does not see them unless told.
KEG_ONLY="curl libxml2 zlib sqlite openssl@3"

JOBS=$(sysctl -n hw.ncpu)

fetch() {  # url sha256 file
    [ -f "$3" ] || curl -fL --retry 2 -o "$3" "$1"
    echo "$2  $3" | shasum -a 256 -c - >/dev/null || { echo "checksum mismatch: $3" >&2; exit 1; }
}

command -v brew >/dev/null || { echo "Homebrew is required" >&2; exit 1; }

echo "== Homebrew dependencies"
missing=""
for dep in $BREW_DEPS; do
    brew list --formula "$dep" >/dev/null 2>&1 || missing="$missing $dep"
done
if [ -n "$missing" ]; then
    echo "installing:$missing"
    # shellcheck disable=SC2086
    brew install $missing
else
    echo "all present"
fi

mkdir -p "$SRC" "$PREFIX"
cd "$SRC"

echo "== libinotify-kqueue $INOTIFY_VERSION"
fetch "$INOTIFY_URL" "$INOTIFY_SHA256" "libinotify-$INOTIFY_VERSION.tar.gz"
rm -rf "libinotify-$INOTIFY_VERSION"
tar -xzf "libinotify-$INOTIFY_VERSION.tar.gz"
(
    cd "libinotify-$INOTIFY_VERSION"
    ./configure --prefix="$PREFIX" --quiet
    make -j"$JOBS" >/dev/null
    make install >/dev/null
)

echo "== OwnTone $OWNTONE_VERSION"
fetch "$OWNTONE_URL" "$OWNTONE_SHA256" "owntone-$OWNTONE_VERSION.tar.xz"
rm -rf "owntone-$OWNTONE_VERSION"
tar -xJf "owntone-$OWNTONE_VERSION.tar.xz"
cd "owntone-$OWNTONE_VERSION"

BREW_PREFIX=$(brew --prefix)
PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
for keg in $KEG_ONLY; do
    PKG_CONFIG_PATH="$PKG_CONFIG_PATH:$(brew --prefix "$keg")/lib/pkgconfig"
done
export PKG_CONFIG_PATH
export CFLAGS="-I$PREFIX/include -I$BREW_PREFIX/include -I$(brew --prefix sqlite)/include"
export LDFLAGS="-L$PREFIX/lib -L$BREW_PREFIX/lib -L$(brew --prefix sqlite)/lib"
export INOTIFY_CFLAGS="-I$PREFIX/include"
export INOTIFY_LIBS="-L$PREFIX/lib -linotify"

./configure --prefix="$PREFIX" --sysconfdir="$BASE/etc" --localstatedir="$BASE/var" \
    --disable-spotify --disable-lastfm --without-alsa --without-pulseaudio --without-avahi
make -j"$JOBS"
make install

echo "== done: $PREFIX/sbin/owntone"
"$PREFIX/sbin/owntone" -v 2>&1 | head -1 || true
