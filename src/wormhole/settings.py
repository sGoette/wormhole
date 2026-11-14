"""Settings changed from the web UI, kept in a small JSON file (SETTINGS_FILE).

The env file holds the defaults and is root-owned on the server; this file belongs to the
`wormhole` user, so the web UI can write it. A value here wins over the env file's.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

RENDERER_NAME_MAX = 64


class Settings:
    def __init__(self, path: Optional[Path]):
        self.path = path  # None: not configured, nothing can be saved
        self.lock = threading.Lock()
        self.values: Dict[str, Any] = self._read()

    def _read(self) -> Dict[str, Any]:
        if self.path is None or not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable settings file %s: %s", self.path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if self.path is None:
            raise ValueError("SETTINGS_FILE is not set, so settings can't be saved "
                             "(server: re-run install.sh)")
        with self.lock:
            values = dict(self.values, **{key: value})
            tmp = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Write a temporary file and rename it: a crash never leaves half a file.
                fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-")
                with os.fdopen(fd, "w") as f:
                    json.dump(values, f, indent=2)
                    f.write("\n")
                os.replace(tmp, self.path)
            except OSError as exc:
                if tmp is not None and os.path.exists(tmp):
                    os.unlink(tmp)
                raise ValueError(f"cannot save settings to {self.path}: {exc}") from None
            self.values = values


def clean_renderer_name(name: Any) -> str:
    """The name shown in the turntable app: trimmed, 1-64 printable characters."""
    if not isinstance(name, str):
        raise ValueError("name must be text")
    name = " ".join(name.split())
    if not name:
        raise ValueError("Enter a name.")
    if len(name) > RENDERER_NAME_MAX:
        raise ValueError(f"The name can have at most {RENDERER_NAME_MAX} characters.")
    if not name.isprintable():
        raise ValueError("The name contains characters that can't be shown.")
    return name
