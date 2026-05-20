from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from branch_rebaser.git_ops import GitError, analyze_repository, rebase_in_progress, run_rebase_plan


class GitOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.git("init")
        self.git("config", "user.email", "branch-rebaser@example.test")
        self.git("config", "user.name", "Branch Rebaser Tests")
        self.git("checkout", "-b", "main")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=check,
            text=True,
            capture_output=True,
        )

    def write(self, name: str, content: str) -> None:
        (self.repo / name).write_text(content)

    def commit_file(self, name: str, content: str, message: str) -> None:
        self.write(name, content)
        self.git("add", name)
        self.git("commit", "-m", message)

    def test_analysis_recommends_branch_behind_primary(self) -> None:
        self.commit_file("app.txt", "base\n", "base")
        self.git("checkout", "-b", "feature")
        self.commit_file("feature.txt", "feature\n", "feature")
        self.git("checkout", "main")
        self.commit_file("main.txt", "main\n", "main")

        analysis = analyze_repository(self.repo, "main")
        feature = next(branch for branch in analysis.branches if branch.name == "feature")

        self.assertEqual(feature.behind_primary, 1)
        self.assertEqual(feature.ahead_primary, 1)
        self.assertEqual(feature.status, "needs rebase")
        self.assertTrue(feature.recommended)

    def test_conflicting_rebase_is_aborted_and_reported(self) -> None:
        self.commit_file("app.txt", "base\n", "base")
        self.git("checkout", "-b", "feature")
        self.commit_file("app.txt", "feature\n", "feature change")
        self.git("checkout", "main")
        self.commit_file("app.txt", "main\n", "main change")

        result = run_rebase_plan(self.repo, "main", ["feature"], fetch=False)

        self.assertEqual(result.original_branch, "main")
        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].branch, "feature")
        self.assertEqual(result.results[0].status, "conflict")
        self.assertIn("Resolve manually", result.results[0].message)
        self.assertFalse(rebase_in_progress(self.repo))
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "main")

    def test_dirty_worktree_is_rejected(self) -> None:
        self.commit_file("app.txt", "base\n", "base")
        self.git("checkout", "-b", "feature")
        self.commit_file("feature.txt", "feature\n", "feature")
        self.git("checkout", "main")
        self.write("dirty.txt", "dirty\n")

        with self.assertRaises(GitError):
            run_rebase_plan(self.repo, "main", ["feature"], fetch=False)


if __name__ == "__main__":
    unittest.main()
