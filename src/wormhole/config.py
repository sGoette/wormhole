"""Load settings from a simple KEY=VALUE env file, overridable by real environment variables."""

import os
from pathlib import Path
from typing import Dict, Optional


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load(path: Optional[str] = None) -> Dict[str, str]:
    """File values first, then os.environ wins for any key it defines."""
    env_path = path or os.environ.get("WORMHOLE_ENV")
    values: Dict[str, str] = {}
    if env_path and Path(env_path).is_file():
        values = parse_env_file(Path(env_path))
        values["_SOURCE"] = str(Path(env_path).resolve())
    for key in list(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return values
