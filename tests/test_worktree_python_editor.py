"""Editor must never mutate main or execute source code to review it."""
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from leshy.worktree_workspace import WorktreeWorkspace
from leshy.worktree_python_editor import WorktreePythonEditor, PatchError


def git(*args):
    return subprocess.run(("git", *map(str, args)), check=True, capture_output=True, text=True).stdout.strip()


class PythonEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git("init", self.repo)
        git("-C", self.repo, "config", "user.name", "Tester")
        git("-C", self.repo, "config", "user.email", "test@example.com")
        (self.repo / "module.py").write_text("def value():\n    return 1\n", encoding="utf8")
        git("-C", self.repo, "add", "module.py")
        git("-C", self.repo, "commit", "-m", "baseline")
        self.ws = WorktreeWorkspace(self.repo)
        self.one = self.ws.create("agent-a")
        self.two = self.ws.create("agent-b")
        self.editor = WorktreePythonEditor(self.ws)

    def test_explicit_approval_and_main_preservation(self):
        info = self.editor.preview("agent-a", "module.py")
        with self.assertRaises(PermissionError):
            self.editor.apply("agent-a", "module.py", expected_sha256=info["sha256"], replacement="x=3\n")
        result = self.editor.apply("agent-a", "module.py", expected_sha256=info["sha256"],
                                   replacement="def value():\n    return 3\n", approval_verified=True)
        self.assertEqual(result["findings"], [])
        self.assertEqual((self.repo / "module.py").read_text(), "def value():\n    return 1\n")
        self.assertEqual((Path(self.two["path"]) / "module.py").read_text(), "def value():\n    return 1\n")
        self.assertEqual((Path(self.one["path"]) / "module.py").read_text(), "def value():\n    return 3\n")

    def test_stale_digest_does_not_overwrite_newer_version(self):
        info = self.editor.preview("agent-a", "module.py")
        self.editor.apply("agent-a", "module.py", expected_sha256=info["sha256"],
                          replacement="x = 5\n", approval_verified=True)
        with self.assertRaisesRegex(PatchError, "changed since preview"):
            self.editor.apply("agent-a", "module.py", expected_sha256=info["sha256"],
                              replacement="x = 6\n", approval_verified=True)
        self.assertEqual((Path(self.one["path"]) / "module.py").read_text(), "x = 5\n")

    def test_syntax_error_rejected_before_write(self):
        info = self.editor.preview("agent-a", "module.py")
        with self.assertRaisesRegex(PatchError, "syntax error"):
            self.editor.apply("agent-a", "module.py", expected_sha256=info["sha256"],
                              replacement="def broken(:\n", approval_verified=True)
        self.assertEqual(self.editor.preview("agent-a", "module.py")["sha256"], info["sha256"])

    def test_symlink_and_traversal_rejected(self):
        info = self.editor.preview("agent-a", "module.py")
        for path in ("../module.py", "/etc/passwd", "not.txt"):
            with self.assertRaises(PatchError):
                self.editor.apply("agent-a", path, expected_sha256=info["sha256"],
                                  replacement="x=1\n", approval_verified=True)
        link = Path(self.one["path"]) / "shortcut.py"
        try:
            link.symlink_to(self.repo / "module.py")
        except OSError:
            self.skipTest("Symlink unavailable")
        with self.assertRaises(PatchError):
            self.editor.preview("agent-a", "shortcut.py")

    def test_unapproved_review_has_no_effect_and_warnings_are_advisories(self):
        info = self.editor.preview("agent-a", "module.py")
        proposed = 'import os\nresult = os.system("echo not-running")\n'
        result = self.editor.apply("agent-a", "module.py", expected_sha256=info["sha256"],
                                   replacement=proposed, approval_verified=True)
        self.assertTrue(any(f["rule"] == "shell-command" for f in result["findings"]))
        # Parsing the replacement must never execute its os.system call.
        self.assertEqual((Path(self.one["path"]) / "module.py").read_text(), proposed)

    def test_oversize_and_noop_rejected(self):
        info = self.editor.preview("agent-a", "module.py")
        with self.assertRaises(PatchError):
            self.editor.apply("agent-a", "module.py", expected_sha256=info["sha256"],
                              replacement="x='" + "x" * 270000 + "'", approval_verified=True)
        with self.assertRaises(PatchError):
            self.editor.apply("agent-a", "module.py", expected_sha256=info["sha256"],
                              replacement="def value():\n    return 1\n", approval_verified=True)


if __name__ == "__main__": unittest.main()
