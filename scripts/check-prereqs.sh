#!/bin/sh
# Phase 0 acceptance check on the Mac (STRATEGY.md §4, Phase 0).
cd "$(dirname "$0")/.." || exit 1
fail=0
ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }
warn() { printf '  note  %s\n' "$1"; }

python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
    && ok "python3 $(python3 -c 'import platform; print(platform.python_version())') (>= 3.9)" \
    || bad "python3 >= 3.9 missing"

command -v ffmpeg >/dev/null \
    && ok "$(ffmpeg -version | head -1)" \
    || bad "ffmpeg missing (brew install ffmpeg)"

if [ -f tools/fake_turntable.py ]; then
    python3 tools/fake_turntable.py --help >/dev/null 2>&1 \
        && ok "tools/fake_turntable.py --help" \
        || bad "tools/fake_turntable.py --help failed"
else
    bad "tools/fake_turntable.py not present yet"
fi

warn "Local Network permission for Terminal/VS Code can't be checked from here:"
warn "  System Settings > Privacy & Security > Local Network"

exit $fail
