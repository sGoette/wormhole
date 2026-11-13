"""Render OwnTone's config from config/owntone.conf.in for the Mac or the server.

One template for both places; only paths, the user and the name differ. Pipe format and the
API port come from the wormhole env file, so OwnTone and `wormhole feed` always agree.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from . import config

TEMPLATE = config.ROOT / "config" / "owntone.conf.in"

# OwnTone's "debug" logs every SQL query; "info" is the useful level for our DEBUG.
LOGLEVELS = {"DEBUG": "info", "INFO": "log", "WARNING": "warning", "ERROR": "warning"}


def _quote(value: str) -> str:
    if '"' in value or "\n" in value:
        raise ValueError(f"unsupported character in config value: {value!r}")
    return f'"{value}"'


def render(cfg: Dict[str, str], user: str, data_dir: Path, logfile: Path, name: str,
           extra_dirs: Optional[List[Path]] = None, template: Path = TEMPLATE) -> str:
    pipe_dir = config.path(cfg, "PCM_PIPE").parent
    port = urlsplit(config.require(cfg, "OWNTONE_URL")).port or 3689
    directories = [pipe_dir] + list(extra_dirs or [])
    values = {
        "USER": user,
        "DB_PATH": str(data_dir / "songs3.db"),
        "CACHE_DIR": str(data_dir),
        "LOGFILE": str(logfile),
        "LOGLEVEL": LOGLEVELS.get(cfg.get("LOG_LEVEL", "INFO").upper(), "log"),
        "NAME": name,
        "PORT": str(port),
        "DIRECTORIES": ", ".join(_quote(str(d)) for d in directories),
        "PIPE_SAMPLE_RATE": cfg.get("PIPE_SAMPLE_RATE", "44100"),
        "PIPE_BITS": cfg.get("PIPE_BITS", "16"),
    }
    text = template.read_text()
    for key, value in values.items():
        if key not in ("DIRECTORIES", "LOGLEVEL", "PORT", "PIPE_SAMPLE_RATE", "PIPE_BITS"):
            _quote(value)  # validates; the template supplies the quotes
        text = text.replace(f"@{key}@", value)
    leftover = re.findall(r"@[A-Z_]+@", text)
    if leftover:
        raise ValueError(f"template placeholders not filled: {', '.join(sorted(set(leftover)))}")
    return text
