# Branch Rebaser

A conservative TUI for rebasing multiple local git branches onto a selected primary branch.

## What it does

- Detects the target git repository from the current directory, or from `--repo`.
- Lists local branches with ahead/behind counts against a chosen primary ref.
- Defaults the primary ref to `origin/main`, `origin/master`, `main`, or `master` when available.
- Lets you select branches to rebase.
- Refuses to run unless the worktree is clean.
- Rebases selected branches one at a time.
- Aborts and skips branches that hit conflicts, then reports them for manual resolution.
- Restores the branch you started on after the run.

## Run

```bash
python3 -m branch_rebaser --repo /path/to/repo
```

From inside the repo you want to work on:

```bash
python3 -m branch_rebaser
```

After an editable install:

```bash
branch-rebaser --repo /path/to/repo
```

## Controls

- `Space`: toggle the highlighted branch
- `r`: run rebase for selected branches
- `f`: refresh branch analysis
- `a`: select recommended branches
- `c`: clear selections
- `q`: quit

## Safety model

This tool is designed for the batch case where conflicts should be skipped, not resolved inline. If a rebase fails with a conflict, the tool runs `git rebase --abort`, records the branch as conflicted, and continues with the next selected branch.
