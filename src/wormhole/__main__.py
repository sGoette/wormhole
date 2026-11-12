"""CLI entry point: `bin/wormhole <command>`.

Commands grow with the phases (bridge in Phase 5, controller in Phase 6).
`info` is used by the deploy script as a smoke test on the NAS.
"""

import argparse
import platform
import sys
from pathlib import Path

from . import __version__, config


def cmd_info(_args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[2]
    version_file = root / "VERSION"
    release = version_file.read_text().strip() if version_file.is_file() else "dev (not deployed)"
    cfg = config.load()
    print(f"wormhole {__version__}  release: {release}")
    print(f"python   {platform.python_version()} on {platform.system()} {platform.release()}")
    print(f"config   {cfg.pop('_SOURCE', 'none found')}")
    for key in sorted(cfg):
        print(f"  {key}={cfg[key]}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wormhole")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="show version, python, platform and active config").set_defaults(func=cmd_info)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
