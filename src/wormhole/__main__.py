"""CLI entry point: `bin/wormhole <command>`.

On the server, systemd runs `wormhole web`; install.sh uses `owntone-config`.
"""

import argparse
import logging
import platform
import sys
from pathlib import Path
from typing import Dict

from . import __version__, config, feed
from .owntone import OwnTone, OwnToneError


def cmd_info(cfg: Dict[str, str], _args: argparse.Namespace) -> int:
    from .update import UpdateError, Updater
    try:
        cur = Updater().current()
        release = f"{cur['short']} {cur['date'][:10]} {cur['subject']}" if cur["available"] \
            else cur["reason"]
    except UpdateError as exc:
        release = str(exc)
    print(f"wormhole {__version__}  commit: {release}")
    print(f"python   {platform.python_version()} on {platform.system()} {platform.release()}")
    print(f"config   {cfg.get('_SOURCE', 'none found')}")
    for key in sorted(k for k in cfg if not k.startswith("_")):
        print(f"  {key}={cfg[key]}")
    return 0


def cmd_feed(cfg: Dict[str, str], args: argparse.Namespace) -> int:
    source = args.input or cfg.get("FEED_INPUT") or feed.TONE
    pipe = config.path(cfg, "PCM_PIPE")
    # OwnTone's pipe_autostart is off: start the pipe on the first audio, like the renderer.
    ot = OwnTone(config.require(cfg, "OWNTONE_URL"))
    feeder = feed.Feeder(
        source=source,
        pipe=pipe,
        rate=int(cfg.get("PIPE_SAMPLE_RATE", "44100")),
        bits=int(cfg.get("PIPE_BITS", "16")),
        on_first_data=lambda: ot.play_pipe(pipe.name),
    )
    feeder.install_signal_handlers()
    return feeder.run()


def cmd_web(cfg: Dict[str, str], args: argparse.Namespace) -> int:
    from . import web
    return web.serve(cfg, args.port or int(cfg.get("WEB_PORT", "8766")))


def cmd_owntone_config(cfg: Dict[str, str], args: argparse.Namespace) -> int:
    from . import owntoneconf
    text = owntoneconf.render(cfg, user=args.user, data_dir=Path(args.data_dir),
                              logfile=Path(args.logfile), name=args.name,
                              extra_dirs=[Path(d) for d in args.dir])
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


def cmd_update(cfg: Dict[str, str], args: argparse.Namespace) -> int:
    from .update import UpdateError, Updater
    up = Updater(remote=cfg.get("UPDATE_REMOTE", "origin"), branch=cfg.get("UPDATE_BRANCH", "main"))
    try:
        if args.action == "check":
            s = up.check()
            print(f"running:  {s['short']} {s['subject']}")
            print(f"behind:   {s['behind']} commit(s) on {up.upstream}")
            for c in s["commits"]:
                print(f"  {c['short']} {c['subject']}")
            if s["blocked_reason"]:
                print(f"blocked:  {s['blocked_reason']}")
        else:
            r = up.apply() if args.action == "apply" else up.rollback()
            print(f"{args.action}: {r['from']} -> {r['to']}; restart wormhole to load it")
            if r["needs_install"]:
                print("server files changed, run as root: server/install.sh")
    except UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_owntone(cfg: Dict[str, str], args: argparse.Namespace) -> int:
    ot = OwnTone(config.require(cfg, "OWNTONE_URL"))
    try:
        if args.action == "outputs":
            for o in ot.outputs():
                flags = []
                if o.needs_auth_key or o.requires_auth:
                    flags.append("needs PIN/password")
                mark = "*" if o.selected else " "
                extra = f"  ({', '.join(flags)})" if flags else ""
                print(f"{mark} {o.name:<28} {o.type:<10} vol {o.volume:>3}  id {o.id}{extra}")
        elif args.action == "select":
            outputs = ot.find_outputs(args.names)
            ot.set_outputs([o.id for o in outputs])
            if args.volume is not None:
                for o in outputs:
                    ot.update_output(o.id, volume=args.volume)
            print("selected: " + ", ".join(o.name for o in outputs))
        elif args.action == "pin":
            (output,) = ot.find_outputs([args.name])
            ot.update_output(output.id, pin=args.pin)
            print(f"PIN sent to {output.name}")
        elif args.action == "status":
            p = ot.player()
            selected = [o.name for o in ot.outputs() if o.selected]
            print(f"state:   {p.get('state')}")
            print(f"volume:  {p.get('volume')}")
            print(f"outputs: {', '.join(selected) or 'none selected'}")
        else:
            getattr(ot, args.action)()  # play / pause / stop
            print(f"{args.action} ok")
    except OwnToneError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wormhole")
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="show version, python, platform and active config")
    info.set_defaults(func=cmd_info)

    fd = sub.add_parser("feed", help="write PCM into OwnTone's pipe (restarts on failure)")
    fd.add_argument("--input", help="'tone', an audio file, or a stream URL "
                                    "(default: FEED_INPUT from config)")
    fd.set_defaults(func=cmd_feed)

    web = sub.add_parser("web", help="web UI: speakers, volumes, feed (default port WEB_PORT)")
    web.add_argument("--port", type=int)
    web.set_defaults(func=cmd_web)

    oc = sub.add_parser("owntone-config", help="render OwnTone's config from the template")
    oc.add_argument("--user", required=True, help="user OwnTone runs as")
    oc.add_argument("--data-dir", required=True, help="database + cache directory")
    oc.add_argument("--logfile", required=True)
    oc.add_argument("--name", default="wormhole on %h", help="library name (%%h = hostname)")
    oc.add_argument("--dir", action="append", default=[], help="extra library directory")
    oc.add_argument("-o", "--output", help="write here instead of stdout")
    oc.set_defaults(func=cmd_owntone_config)

    upd = sub.add_parser("update", help="update this checkout from GitHub")
    upd.add_argument("action", choices=["check", "apply", "rollback"])
    upd.set_defaults(func=cmd_update)

    ot = sub.add_parser("owntone", help="control OwnTone (speakers, player)")
    ot.set_defaults(func=cmd_owntone)
    act = ot.add_subparsers(dest="action", required=True)
    act.add_parser("outputs", help="list speakers (* = selected)")
    sel = act.add_parser("select", help="play on exactly these speakers")
    sel.add_argument("names", nargs="+", help="speaker names (unique part is enough)")
    sel.add_argument("--volume", type=int, help="also set each speaker's volume (0-100)")
    pin = act.add_parser("pin", help="send the verification PIN shown by a speaker")
    pin.add_argument("name")
    pin.add_argument("pin")
    act.add_parser("status", help="player state and selected speakers")
    for name in ("play", "pause", "stop"):
        act.add_parser(name)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config.load()
    level = getattr(logging, cfg.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
