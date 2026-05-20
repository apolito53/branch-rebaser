from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, RichLog, Select, Static

from .git_ops import (
    BranchInfo,
    GitError,
    RebasePlanResult,
    RepositoryAnalysis,
    analyze_repository,
    run_rebase_plan,
)


class PrimaryBranchModal(ModalScreen):
    BINDINGS = [
        ("enter", "confirm", "Use"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        candidates: List[str],
        current_primary: str,
        repo: Path,
        current_branch: str,
        *,
        startup: bool = False,
    ):
        super().__init__()
        self.candidates = candidates
        self.current_primary = current_primary
        self.repo = repo
        self.current_branch = current_branch
        self.startup = startup

    def compose(self) -> ComposeResult:
        title = "Choose Primary Branch" if self.startup else "Primary Branch"
        cancel_label = "Quit" if self.startup else "Cancel"
        with Vertical(id="primary-modal"):
            yield Static(title, id="primary-modal-title")
            yield Static(
                f"Repo: {self.repo} | current: {self.current_branch}",
                id="primary-modal-context",
            )
            yield Select(
                [(candidate, candidate) for candidate in self.candidates],
                prompt="Primary branch",
                allow_blank=False,
                value=self.current_primary,
                id="primary-picker",
            )
            yield Static("", id="primary-modal-error")
            with Horizontal(id="primary-modal-buttons"):
                yield Button("Use primary", id="primary-confirm", variant="primary")
                yield Button(cancel_label, id="primary-cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "primary-confirm":
            self.action_confirm()
        elif event.button.id == "primary-cancel":
            self.action_cancel()

    def action_confirm(self) -> None:
        select = self.query_one("#primary-picker", Select)
        value = select.value
        if value == Select.BLANK:
            self.query_one("#primary-modal-error", Static).update("Pick a primary branch.")
            return
        self.dismiss(str(value))

    def action_cancel(self) -> None:
        self.dismiss(None)


class BranchRebaserApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    PrimaryBranchModal {
        align: center middle;
    }

    #primary-modal {
        width: 78;
        height: 13;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }

    #primary-modal-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #primary-modal-context {
        margin-bottom: 1;
    }

    #primary-modal-error {
        color: $error;
        height: 1;
    }

    #primary-modal-buttons {
        height: 3;
        margin-top: 1;
    }

    #top {
        height: 9;
        padding: 1 2;
        border: solid $primary;
    }

    #actions {
        height: 3;
    }

    #content {
        height: 1fr;
    }

    #branch-table {
        width: 2fr;
        height: 1fr;
    }

    #log {
        width: 1fr;
        height: 1fr;
        border-left: solid $primary;
    }

    Button {
        margin-right: 1;
    }

    #status {
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f", "refresh", "Refresh"),
        ("r", "run_rebases", "Run"),
        ("space", "toggle_branch", "Toggle"),
        ("a", "select_recommended", "Recommended"),
        ("c", "clear_selection", "Clear"),
        ("m", "open_menu", "Menu"),
    ]

    def __init__(self, repo_path: Path):
        super().__init__()
        self.repo_path = repo_path
        self.analysis: Optional[RepositoryAnalysis] = None
        self.primary: Optional[str] = None
        self.selected: set[str] = set()
        self.branch_order: List[str] = []
        self.branch_by_name: Dict[str, BranchInfo] = {}
        self.running = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="top"):
            yield Static("Loading repository...", id="status")
            with Horizontal(id="actions"):
                yield Button("Primary menu", id="menu", variant="default")
                yield Button("Refresh", id="refresh", variant="default")
                yield Button("Select recommended", id="select-recommended", variant="default")
                yield Button("Clear", id="clear", variant="default")
                yield Button("Run selected", id="run", variant="primary")
        with Horizontal(id="content"):
            yield DataTable(id="branch-table")
            yield RichLog(id="log", markup=True, highlight=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#branch-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("Sel", "Branch", "Behind", "Ahead", "Upstream", "Status", "Last commit")
        self.refresh_analysis()
        self.call_after_refresh(self.open_primary_picker, True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "menu":
            self.action_open_menu()
        elif button_id == "refresh":
            self.action_refresh()
        elif button_id == "select-recommended":
            self.action_select_recommended()
        elif button_id == "clear":
            self.action_clear_selection()
        elif button_id == "run":
            self.action_run_rebases()

    def action_refresh(self) -> None:
        if not self.running:
            self.refresh_analysis()

    def action_open_menu(self) -> None:
        self.open_primary_picker(False)

    def action_toggle_branch(self) -> None:
        if self.running:
            return
        table = self.query_one("#branch-table", DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self.branch_order):
            return
        branch = self.branch_order[row]
        info = self.branch_by_name[branch]
        if not self.is_selectable(info):
            self.write_log(f"[yellow]Cannot select {branch}: {info.status}.[/yellow]")
            return
        if branch in self.selected:
            self.selected.remove(branch)
        else:
            self.selected.add(branch)
        self.render_branches()

    def action_select_recommended(self) -> None:
        if self.running or not self.analysis:
            return
        self.selected = {
            branch.name
            for branch in self.analysis.branches
            if self.is_selectable(branch) and branch.recommended
        }
        self.render_branches()
        self.write_log(f"Selected {len(self.selected)} recommended branches.")

    def action_clear_selection(self) -> None:
        if self.running:
            return
        self.selected.clear()
        self.render_branches()
        self.write_log("Selection cleared.")

    def action_run_rebases(self) -> None:
        if self.running:
            return
        if not self.analysis or not self.primary:
            self.write_log("[red]Repository analysis is not ready.[/red]")
            return
        if not self.selected:
            self.write_log("[yellow]No branches selected.[/yellow]")
            return
        if self.analysis.dirty:
            self.write_log("[red]Worktree is dirty or a rebase is already in progress. Clean it first.[/red]")
            return

        selected = [branch for branch in self.branch_order if branch in self.selected]
        primary = self.primary
        repo = self.analysis.repo
        self.running = True
        self.update_status(f"Running rebase plan for {len(selected)} branches onto {primary}...")
        self.write_log(f"[bold]Starting rebase plan onto {primary}[/bold]")
        self.set_buttons_enabled(False)

        def progress(message: str) -> None:
            self.call_from_thread(self.write_log, message)

        def worker() -> None:
            try:
                result = run_rebase_plan(repo, primary, selected, fetch=True, progress=progress)
            except Exception as exc:
                self.call_from_thread(self.finish_run_with_error, exc)
            else:
                self.call_from_thread(self.finish_run, result)

        thread = threading.Thread(target=worker, name="branch-rebaser-worker", daemon=True)
        thread.start()

    def refresh_analysis(self) -> None:
        try:
            analysis = analyze_repository(self.repo_path, self.primary)
        except GitError as exc:
            self.analysis = None
            self.update_status(f"Git error: {exc}")
            self.write_log(f"[red]{exc}[/red]")
            return

        previous_primary = self.primary
        self.analysis = analysis
        self.primary = analysis.primary
        self.branch_by_name = {branch.name: branch for branch in analysis.branches}
        self.branch_order = [branch.name for branch in analysis.branches]

        if previous_primary != analysis.primary:
            self.selected.clear()
        self.selected = {
            branch
            for branch in self.selected
            if branch in self.branch_by_name and self.is_selectable(self.branch_by_name[branch])
        }
        if not self.selected:
            self.selected = {
                branch.name
                for branch in analysis.branches
                if self.is_selectable(branch) and branch.recommended
            }
        self.render_branches()
        dirty = "dirty" if analysis.dirty else "clean"
        self.update_status(
            f"Repo: {analysis.repo} | current: {analysis.current_branch} | primary: {analysis.primary} | {dirty} | selected: {len(self.selected)}"
        )
        self.write_log(f"Loaded {len(analysis.branches)} local branches from {analysis.repo}.")

    def open_primary_picker(self, startup: bool = False) -> None:
        if self.running:
            return
        if not self.analysis:
            self.refresh_analysis()
        if not self.analysis:
            return

        modal = PrimaryBranchModal(
            list(self.analysis.primary_candidates),
            self.primary or self.analysis.primary,
            self.analysis.repo,
            self.analysis.current_branch,
            startup=startup,
        )
        self.push_screen(
            modal,
            callback=lambda selected_primary: self.apply_primary_selection(selected_primary, startup),
        )

    def apply_primary_selection(self, selected_primary: Optional[str], startup: bool = False) -> None:
        if selected_primary is None:
            if startup:
                self.exit()
            return
        if selected_primary == self.primary:
            if startup:
                self.write_log(f"Primary branch confirmed: {selected_primary}.")
            return
        self.primary = selected_primary
        self.selected.clear()
        self.refresh_analysis()
        self.write_log(f"Primary branch set to {selected_primary}.")

    def render_branches(self) -> None:
        table = self.query_one("#branch-table", DataTable)
        table.clear()
        if not self.analysis:
            return
        for branch in self.analysis.branches:
            selected = "[x]" if branch.name in self.selected else "[ ]"
            status = self.status_text(branch)
            branch_name = Text(branch.name)
            if branch.is_current:
                branch_name.append(" *", style="bold cyan")
            table.add_row(
                selected,
                branch_name,
                str(branch.behind_primary),
                str(branch.ahead_primary),
                branch.upstream or "-",
                status,
                branch.last_commit,
                key=branch.name,
            )

    def is_selectable(self, branch: BranchInfo) -> bool:
        return not branch.is_primary and not branch.is_current and not branch.already_merged

    def status_text(self, branch: BranchInfo) -> Text:
        if branch.status == "needs rebase":
            return Text(branch.status, style="bold yellow")
        if branch.status == "up to date":
            return Text(branch.status, style="green")
        if branch.status in {"primary", "current"}:
            return Text(branch.status, style="cyan")
        if branch.status == "already merged":
            return Text(branch.status, style="dim")
        return Text(branch.status, style="magenta")

    def finish_run(self, result: RebasePlanResult) -> None:
        counts: Dict[str, int] = {}
        for item in result.results:
            counts[item.status] = counts.get(item.status, 0) + 1
            style = {
                "rebased": "green",
                "conflict": "yellow",
                "failed": "red",
                "skipped": "dim",
            }.get(item.status, "white")
            self.write_log(f"[{style}]{item.branch}: {item.status} - {item.message}[/{style}]")

        summary = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())) or "no results"
        self.write_log(f"[bold]Finished: {summary}[/bold]")
        self.running = False
        self.set_buttons_enabled(True)
        self.selected.clear()
        self.refresh_analysis()

    def finish_run_with_error(self, exc: Exception) -> None:
        self.write_log(f"[red]Run failed: {exc}[/red]")
        self.running = False
        self.set_buttons_enabled(True)
        self.refresh_analysis()

    def write_log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def update_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def set_buttons_enabled(self, enabled: bool) -> None:
        for button_id in ("#menu", "#refresh", "#select-recommended", "#clear", "#run"):
            self.query_one(button_id, Button).disabled = not enabled
