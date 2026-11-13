"""Updater against real temporary git repositories (needs git, no network)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from wormhole.update import UpdateError, Updater


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


class UpdaterTest(unittest.TestCase):
    """origin = bare repo (stands in for GitHub), server = the VM's clone, dev = a developer
    pushing new commits."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.origin, self.server, self.dev = base / "origin.git", base / "server", base / "dev"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.dev)], check=True,
                       capture_output=True)
        self.commit(self.dev, "README.md", "one\n", "first")
        git(self.dev, "push", "-q", "origin", "HEAD:main")
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.server)], check=True)
        self.up = Updater(repo=self.server)

    def commit(self, repo: Path, name: str, text: str, msg: str) -> None:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(repo, "add", name)
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)

    def push(self, name: str, msg: str) -> None:
        self.commit(self.dev, name, msg + "\n", msg)
        git(self.dev, "push", "-q", "origin", "HEAD:main")

    def test_up_to_date(self):
        status = self.up.check()
        self.assertEqual((status["behind"], status["can_update"]), (0, False))
        self.assertEqual(status["subject"], "first")
        self.assertEqual(status["repo"], str(self.server))

    def test_behind_lists_commits_and_applies_fast_forward(self):
        self.push("src/a.py", "second")
        self.push("src/b.py", "third")
        status = self.up.check()
        self.assertEqual(status["behind"], 2)
        self.assertEqual([c["subject"] for c in status["commits"]], ["third", "second"])
        self.assertTrue(status["can_update"])
        self.assertEqual(status["needs_install"], [])
        result = self.up.apply()
        self.assertEqual(self.up.current()["subject"], "third")
        self.assertEqual(result["needs_install"], [])
        self.assertEqual(self.up.current()["previous"]["subject"], "first")

    def test_server_file_changes_need_install(self):
        self.push("server/install.sh", "installer change")
        self.push("config/owntone.conf.in", "template change")
        self.assertEqual(sorted(self.up.check()["needs_install"]),
                         ["config/owntone.conf.in", "server/install.sh"])

    def test_rollback_and_undo(self):
        self.push("src/a.py", "second")
        self.up.apply()
        self.up.rollback()
        self.assertEqual(self.up.current()["subject"], "first")
        self.assertEqual(self.up.check()["behind"], 1)  # the update is offered again
        self.up.rollback()  # rollback of the rollback
        self.assertEqual(self.up.current()["subject"], "second")

    def test_no_previous_version(self):
        with self.assertRaisesRegex(UpdateError, "no previous version"):
            self.up.rollback()

    def test_local_changes_block_update(self):
        self.push("src/a.py", "second")
        (self.server / "README.md").write_text("edited on the server\n")
        status = self.up.check()
        self.assertFalse(status["can_update"])
        self.assertIn("local changes", status["blocked_reason"])
        with self.assertRaisesRegex(UpdateError, "local changes"):
            self.up.apply()

    def test_unpushed_commits_block_update(self):
        self.push("src/a.py", "second")
        self.commit(self.server, "local.txt", "x", "local work")
        status = self.up.check()
        self.assertIn("1 commit(s) that are not on origin/main", status["blocked_reason"])
        self.assertFalse(status["can_update"])

    def test_state_file_is_not_a_local_change(self):
        self.push("src/a.py", "second")
        self.up.apply()
        self.assertEqual(git(self.server, "status", "--porcelain"), "")

    def test_not_a_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Updater(repo=Path(d)).check(),
                             {"available": False, "reason": "not a git checkout"})

    def test_version_shown_without_remote(self):
        git(self.server, "remote", "remove", "origin")
        status = self.up.current()
        self.assertEqual((status["subject"], status["remote_url"]), ("first", ""))
        with self.assertRaisesRegex(UpdateError, "git fetch failed"):
            self.up.check()

    def test_unreachable_remote(self):
        git(self.server, "remote", "set-url", "origin", "/nonexistent/repo.git")
        with self.assertRaisesRegex(UpdateError, "git fetch failed"):
            self.up.check()


if __name__ == "__main__":
    unittest.main()
