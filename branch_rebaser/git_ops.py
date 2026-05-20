from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple


ProgressCallback = Callable[[str], None]


class GitError(RuntimeError):
    def __init__(self, message: str, result: Optional["GitCommandResult"] = None):
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class GitCommandResult:
    args: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class BranchInfo:
    name: str
    commit: str
    last_commit: str
    upstream: str
    behind_primary: int
    ahead_primary: int
    is_current: bool
    is_primary: bool
    already_merged: bool
    contains_primary: bool
    recommended: bool
    status: str


@dataclass(frozen=True)
class RepositoryAnalysis:
    repo: Path
    current_branch: str
    primary: str
    primary_candidates: Tuple[str, ...]
    branches: Tuple[BranchInfo, ...]
    dirty: bool
    has_remotes: bool


@dataclass(frozen=True)
class RebaseResult:
    branch: str
    status: str
    message: str
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class RebasePlanResult:
    repo: Path
    primary: str
    original_branch: str
    results: Tuple[RebaseResult, ...]


def run_git(
    repo: Path,
    args: Sequence[str],
    *,
    check: bool = True,
    env: Optional[dict] = None,
) -> GitCommandResult:
    command = ("git", "-C", str(repo), *args)
    run_env = os.environ.copy()
    run_env["GIT_TERMINAL_PROMPT"] = "0"
    if env:
        run_env.update(env)

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=run_env,
    )
    result = GitCommandResult(
        args=tuple(args),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and result.returncode != 0:
        raise GitError(command_error_message(result), result)
    return result


def command_error_message(result: GitCommandResult) -> str:
    detail = (result.stderr or result.stdout).strip()
    suffix = f": {detail}" if detail else ""
    return f"git {' '.join(result.args)} failed with exit code {result.returncode}{suffix}"


def find_repo_root(path: Path) -> Path:
    result = run_git(path, ["rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip()).resolve()


def current_branch(repo: Path) -> str:
    result = run_git(repo, ["branch", "--show-current"])
    branch = result.stdout.strip()
    if not branch:
        raise GitError("Detached HEAD is not supported. Check out a branch before running.")
    return branch


def has_remotes(repo: Path) -> bool:
    return bool(run_git(repo, ["remote"], check=True).stdout.strip())


def is_worktree_dirty(repo: Path) -> bool:
    return bool(run_git(repo, ["status", "--porcelain=v1"]).stdout.strip())


def ensure_clean_worktree(repo: Path) -> None:
    if rebase_in_progress(repo):
        raise GitError("A rebase is already in progress. Resolve or abort it before running.")
    if is_worktree_dirty(repo):
        raise GitError("Worktree is dirty. Commit, stash, or discard changes before running.")


def git_path(repo: Path, name: str) -> Path:
    result = run_git(repo, ["rev-parse", "--git-path", name])
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = repo / path
    return path


def rebase_in_progress(repo: Path) -> bool:
    return git_path(repo, "rebase-merge").exists() or git_path(repo, "rebase-apply").exists()


def ref_exists(repo: Path, ref: str) -> bool:
    return run_git(repo, ["rev-parse", "--verify", "--quiet", ref], check=False).returncode == 0


def local_branch_names(repo: Path) -> Tuple[str, ...]:
    result = run_git(repo, ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def primary_candidates(repo: Path) -> Tuple[str, ...]:
    result = run_git(
        repo,
        [
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
            "refs/remotes",
        ],
    )
    refs = []
    for line in result.stdout.splitlines():
        ref = line.strip()
        if not ref or ref.endswith("/HEAD"):
            continue
        refs.append(ref)

    preferred = ["origin/main", "origin/master", "main", "master", "upstream/main", "upstream/master"]
    ordered = []
    for ref in preferred:
        if ref in refs:
            ordered.append(ref)
    for ref in sorted(refs):
        if ref not in ordered:
            ordered.append(ref)
    return tuple(ordered)


def rev_list_counts(repo: Path, primary: str, branch: str) -> Tuple[int, int]:
    result = run_git(repo, ["rev-list", "--left-right", "--count", f"{primary}...{branch}"])
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        raise GitError(f"Unexpected rev-list output for {primary}...{branch}: {result.stdout!r}")
    return int(parts[0]), int(parts[1])


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant], check=False).returncode == 0


def analyze_repository(path: Path, primary: Optional[str] = None) -> RepositoryAnalysis:
    repo = find_repo_root(path)
    current = current_branch(repo)
    candidates = primary_candidates(repo)
    if primary is None:
        if not candidates:
            raise GitError("No local or remote branches were found.")
        primary = candidates[0]
    if not ref_exists(repo, primary):
        raise GitError(f"Primary ref does not exist: {primary}")

    branch_infos = []
    branch_result = run_git(
        repo,
        [
            "for-each-ref",
            "--format=%(refname:short)%09%(objectname:short)%09%(committerdate:relative)%09%(upstream:short)",
            "refs/heads",
        ],
    )
    for line in branch_result.stdout.splitlines():
        parts = line.split("\t")
        while len(parts) < 4:
            parts.append("")
        name, commit, last_commit, upstream = parts[:4]
        behind, ahead = rev_list_counts(repo, primary, name)
        is_primary = name == primary
        is_current = name == current
        already_merged = not is_primary and is_ancestor(repo, name, primary)
        contains_primary = is_ancestor(repo, primary, name)
        recommended = (
            behind > 0
            and not is_primary
            and not is_current
            and not already_merged
        )
        status = branch_status(
            is_primary=is_primary,
            is_current=is_current,
            already_merged=already_merged,
            contains_primary=contains_primary,
            behind=behind,
        )
        branch_infos.append(
            BranchInfo(
                name=name,
                commit=commit,
                last_commit=last_commit,
                upstream=upstream,
                behind_primary=behind,
                ahead_primary=ahead,
                is_current=is_current,
                is_primary=is_primary,
                already_merged=already_merged,
                contains_primary=contains_primary,
                recommended=recommended,
                status=status,
            )
        )

    branch_infos.sort(key=lambda branch: (branch.is_primary, branch.name.lower()))
    return RepositoryAnalysis(
        repo=repo,
        current_branch=current,
        primary=primary,
        primary_candidates=candidates,
        branches=tuple(branch_infos),
        dirty=is_worktree_dirty(repo) or rebase_in_progress(repo),
        has_remotes=has_remotes(repo),
    )


def branch_status(
    *,
    is_primary: bool,
    is_current: bool,
    already_merged: bool,
    contains_primary: bool,
    behind: int,
) -> str:
    if is_primary:
        return "primary"
    if is_current:
        return "current"
    if already_merged:
        return "already merged"
    if behind > 0:
        return "needs rebase"
    if contains_primary:
        return "up to date"
    return "diverged"


def fetch_all(repo: Path, progress: Optional[ProgressCallback] = None) -> None:
    if not has_remotes(repo):
        if progress:
            progress("No remotes configured; skipping fetch.")
        return
    if progress:
        progress("Fetching remotes...")
    run_git(repo, ["fetch", "--all", "--prune"])


def run_rebase_plan(
    path: Path,
    primary: str,
    branches: Iterable[str],
    *,
    fetch: bool = True,
    progress: Optional[ProgressCallback] = None,
) -> RebasePlanResult:
    repo = find_repo_root(path)
    ensure_clean_worktree(repo)
    if not ref_exists(repo, primary):
        raise GitError(f"Primary ref does not exist: {primary}")

    branch_list = list(dict.fromkeys(branches))
    local_branches = set(local_branch_names(repo))
    unknown = [branch for branch in branch_list if branch not in local_branches]
    if unknown:
        raise GitError(f"Selected branch is not a local branch: {', '.join(unknown)}")

    original_branch = current_branch(repo)
    results: List[RebaseResult] = []

    try:
        if fetch:
            fetch_all(repo, progress)

        for branch in branch_list:
            if progress:
                progress(f"Analyzing {branch}...")

            if branch == primary:
                results.append(RebaseResult(branch, "skipped", "Selected branch is the primary branch."))
                continue
            if is_ancestor(repo, branch, primary):
                results.append(RebaseResult(branch, "skipped", "Branch is already merged into primary."))
                if progress:
                    progress(f"Skipped {branch}: already merged into {primary}.")
                continue

            if progress:
                progress(f"Checking out {branch}...")
            checkout = run_git(repo, ["checkout", branch], check=False)
            if checkout.returncode != 0:
                results.append(
                    RebaseResult(
                        branch,
                        "failed",
                        command_error_message(checkout),
                        checkout.stdout,
                        checkout.stderr,
                    )
                )
                continue

            if progress:
                progress(f"Rebasing {branch} onto {primary}...")
            rebase = run_git(repo, ["rebase", primary], check=False)
            if rebase.returncode == 0:
                results.append(
                    RebaseResult(branch, "rebased", f"Rebased onto {primary}.", rebase.stdout, rebase.stderr)
                )
                if progress:
                    progress(f"Rebased {branch}.")
                continue

            combined_output = f"{rebase.stdout}\n{rebase.stderr}"
            conflicted = is_conflict_output(combined_output) or rebase_in_progress(repo)
            abort_message = ""
            if rebase_in_progress(repo):
                abort = run_git(repo, ["rebase", "--abort"], check=False)
                if abort.returncode != 0:
                    abort_message = f" Rebase abort failed: {command_error_message(abort)}"

            if conflicted:
                message = f"Conflict while rebasing onto {primary}; aborted. Resolve manually.{abort_message}"
                status = "conflict"
                if progress:
                    progress(f"Skipped {branch}: conflict, rebase aborted.")
            else:
                message = f"Rebase failed and was aborted when possible.{abort_message}"
                status = "failed"
                if progress:
                    progress(f"Failed {branch}: rebase error.")
            results.append(RebaseResult(branch, status, message, rebase.stdout, rebase.stderr))
    finally:
        if current_branch(repo) != original_branch:
            if progress:
                progress(f"Restoring {original_branch}...")
            run_git(repo, ["checkout", original_branch], check=False)

    return RebasePlanResult(
        repo=repo,
        primary=primary,
        original_branch=original_branch,
        results=tuple(results),
    )


def is_conflict_output(output: str) -> bool:
    markers = [
        "CONFLICT",
        "Resolve all conflicts manually",
        "could not apply",
        "fix conflicts and then run",
    ]
    return any(marker in output for marker in markers)

