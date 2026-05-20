from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from branch_rebaser.tui import BranchRebaserApp, PrimaryBranchModal


class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_mounts_headlessly(self) -> None:
        asyncio.get_running_loop().set_debug(False)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.git(repo, "init")
            self.git(repo, "config", "user.email", "branch-rebaser@example.test")
            self.git(repo, "config", "user.name", "Branch Rebaser Tests")
            self.git(repo, "checkout", "-b", "main")
            (repo / "app.txt").write_text("base\n")
            self.git(repo, "add", "app.txt")
            self.git(repo, "commit", "-m", "base")
            long_branch = "feature/very-long-branch-name-that-should-not-wrap"
            self.git(repo, "checkout", "-b", long_branch)
            (repo / "feature.txt").write_text("feature\n")
            self.git(repo, "add", "feature.txt")
            self.git(repo, "commit", "-m", "feature")
            self.git(repo, "checkout", "main")

            app = BranchRebaserApp(repo)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                self.assertIsInstance(app.screen, PrimaryBranchModal)

                await pilot.press("enter")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, PrimaryBranchModal)
                self.assertEqual(app.primary, "main")
                self.assertGreaterEqual(app.query_one("#actions").region.height, 3)
                main_row = app.query_one("#branch-table").get_row("main")
                self.assertEqual(str(main_row[0]), "[ ]")
                self.assertEqual(str(main_row[1]), "main")
                long_row = app.query_one("#branch-table").get_row(long_branch)
                self.assertEqual(str(long_row[0]), "[ ]")
                self.assertTrue(long_row[0].no_wrap)
                self.assertTrue(long_row[1].no_wrap)
                self.assertEqual(app.query_one("#branch-table").get_row_height(long_branch), 1)

                await pilot.press("m")
                await pilot.pause()
                self.assertIsInstance(app.screen, PrimaryBranchModal)

                app.screen.action_cancel()
                await pilot.pause()
                self.assertNotIsInstance(app.screen, PrimaryBranchModal)

    def git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            text=True,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
