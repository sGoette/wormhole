"""Update the running checkout from GitHub: check, fast-forward, roll back.

The server runs wormhole from a git clone (server/install.sh). The web UI calls this module;
after an update or rollback wormhole re-executes itself to load the new code.

Safety rules: only fast-forwards (never discards commits), refuse with local changes or local
commits (e.g. a development checkout on the Mac), and remember the previous commit so one
rollback is always possible.
"""

import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from . import config

log = logging.getLogger(__name__)

# Files whose changes need `sudo server/install.sh` (root) to take effect.
NEEDS_INSTALL = ["server/", "config/owntone.conf.in"]
STATE_FILE = "wormhole-previous-commit"  # inside .git, so it never shows as a local change


class UpdateError(Exception):
    pass


class Updater:
    def __init__(self, repo: Path = config.ROOT, remote: str = "origin", branch: str = "main",
                 timeout: float = 30.0):
        self.repo, self.remote, self.branch, self.timeout = repo, remote, branch, timeout

    @property
    def upstream(self) -> str:
        return f"{self.remote}/{self.branch}"

    def _git(self, *args: str) -> str:
        try:
            done = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True,
                                  text=True, timeout=self.timeout)
        except FileNotFoundError:
            raise UpdateError("git is not installed") from None
        except subprocess.TimeoutExpired:
            raise UpdateError(f"git {args[0]} took longer than {self.timeout:.0f}s") from None
        if done.returncode != 0:
            lines = [ln.strip() for ln in (done.stderr or done.stdout).splitlines() if ln.strip()]
            # git explains over several lines; the "fatal:"/"error:" line is the useful one.
            key = [ln for ln in lines if ln.startswith(("fatal:", "error:"))]
            detail = (key or lines or ["no output"])[0]
            raise UpdateError(f"git {args[0]} failed: {detail}")
        return done.stdout.strip()

    def available(self) -> bool:
        return (self.repo / ".git").exists()

    def _commit(self, ref: str) -> Dict[str, str]:
        sha, short, date, subject = self._git("log", "-1", "--format=%H%n%h%n%cI%n%s",
                                              ref).split("\n", 3)
        return {"commit": sha, "short": short, "date": date, "subject": subject}

    def _state_path(self) -> Path:
        return Path(self._git("rev-parse", "--absolute-git-dir")) / STATE_FILE

    def previous(self) -> Optional[str]:
        path = self._state_path()
        return path.read_text().strip() if path.is_file() else None

    def current(self) -> dict:
        """What is running now; no network."""
        if not self.available():
            return {"available": False, "reason": "not a git checkout"}
        out = dict(self._commit("HEAD"), available=True, branch=self.branch, repo=str(self.repo))
        try:  # showing the version must not depend on the remote (e.g. not set up yet)
            out["remote_url"] = self._git("remote", "get-url", self.remote)
        except UpdateError:
            out["remote_url"] = ""
        prev = self.previous()
        if prev and prev != out["commit"]:
            try:
                out["previous"] = self._commit(prev)
            except UpdateError:
                pass  # previous commit no longer exists locally
        return out

    def check(self) -> dict:
        """Fetch from GitHub and compare. Network: one `git fetch`."""
        status = self.current()
        if not status["available"]:
            return status
        self._git("fetch", "--quiet", self.remote, self.branch)
        behind = int(self._git("rev-list", "--count", f"HEAD..{self.upstream}"))
        ahead = int(self._git("rev-list", "--count", f"{self.upstream}..HEAD"))
        dirty = bool(self._git("status", "--porcelain", "--untracked-files=no"))
        commits: List[dict] = []
        if behind:
            for line in self._git("log", "--format=%h%x09%cI%x09%s", "-n", "20",
                                  f"HEAD..{self.upstream}").splitlines():
                short, date, subject = line.split("\t", 2)
                commits.append({"short": short, "date": date, "subject": subject})
        reason = ""
        if dirty:
            reason = "the checkout has local changes"
        elif ahead:
            reason = f"the checkout has {ahead} commit(s) that are not on {self.upstream}"
        status.update(behind=behind, ahead=ahead, dirty=dirty, commits=commits,
                      can_update=bool(behind) and not reason, blocked_reason=reason,
                      needs_install=self._needs_install("HEAD", self.upstream) if behind else [])
        return status

    def _needs_install(self, old: str, new: str) -> List[str]:
        names = self._git("diff", "--name-only", old, new, "--", *NEEDS_INSTALL)
        return [n for n in names.splitlines() if n]

    def apply(self) -> dict:
        status = self.check()
        if not status.get("can_update"):
            raise UpdateError(status.get("blocked_reason") or "already up to date")
        old = status["commit"]
        self._state_path().write_text(old + "\n")
        self._git("merge", "--ff-only", "--quiet", self.upstream)
        new = self._commit("HEAD")
        log.info("updated %s -> %s", old[:7], new["short"])
        return {"from": old[:7], "to": new["short"], "needs_install": status["needs_install"]}

    def rollback(self) -> dict:
        prev = self.previous()
        if not prev:
            raise UpdateError("no previous version recorded yet")
        current = self._git("rev-parse", "HEAD")
        if prev == current:
            raise UpdateError("already at the previous version")
        if self._git("status", "--porcelain", "--untracked-files=no"):
            raise UpdateError("the checkout has local changes")
        needs_install = self._needs_install(current, prev)
        self._git("reset", "--hard", "--quiet", prev)
        self._state_path().write_text(current + "\n")  # so the rollback can be undone
        log.info("rolled back %s -> %s", current[:7], prev[:7])
        return {"from": current[:7], "to": prev[:7], "needs_install": needs_install}
