"""Rebuild the active-task and done-task indexes.

Run from the repository root:

    python implementation/tools/rebuild_index.py

The script scans implementation/tasks/T*.md and implementation/done/T*.md.
It reads the first heading, the Phase/Status/Depends metadata lines, and
optional Completed/Commit/Reviewed by lines. It does not change the cards.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tasks"
DONE = ROOT / "done"

TITLE_RE = re.compile(r"^# (T\d+\.\d+) - (.+)$", re.M)
PHASE_RE = re.compile(r"^\*\*Phase:\*\* (.+)$", re.M)
STATUS_RE = re.compile(r"^\*\*Status:\*\* (.+)$", re.M)
DEPENDS_RE = re.compile(r"^\*\*Depends on:\*\* (.+)$", re.M)
COMPLETED_RE = re.compile(r"^\*\*Completed:\*\* (.+)$", re.M)
COMMIT_RE = re.compile(r"^\*\*Commit:\*\* (.+)$", re.M)
REVIEWED_RE = re.compile(r"^\*\*Reviewed by:\*\* (.+)$", re.M)


def parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")

    def first(pattern: re.Pattern[str], default: str = "") -> str:
        match = pattern.search(text)
        return match.group(1).strip() if match else default

    return {
        "id": first(TITLE_RE),
        "title": (TITLE_RE.search(text).group(2).strip() if TITLE_RE.search(text) else path.stem),
        "phase": first(PHASE_RE, "-"),
        "status": first(STATUS_RE, "TODO").rstrip("."),
        "depends": first(DEPENDS_RE, "none").rstrip("."),
        "completed": first(COMPLETED_RE, "-"),
        "commit": first(COMMIT_RE, "-"),
        "reviewed": first(REVIEWED_RE, "-"),
        "file": path.name,
    }


def sort_key(row: dict) -> tuple[int, int]:
    major, minor = row["id"].lstrip("T").split(".")
    return int(major), int(minor)


def table_row(row: dict) -> str:
    return (
        f"| {row['id']} | [{row['title']}]({row['file']}) | "
        f"{row['phase']} | {row['depends']} | {row['status']} |"
    )


def done_row(row: dict) -> str:
    return (
        f"| {row['id']} | [{row['title']}]({row['file']}) | "
        f"{row['completed']} | {row['commit']} | {row['reviewed']} |"
    )


def write_tasks(rows: list[dict]) -> None:
    body = "\n".join(table_row(row) for row in rows) or "| - | - | - | - | - |"
    text = f"""# Task Cards

Active task cards. One task per file. A task is small enough to implement, test, and review in one sitting.

## Workflow

1. Pick the lowest-numbered unblocked task.
2. Read the card, its **Spec**, and the shared contracts in `../spec/`.
3. Branch from `refactored`: `git switch -c task/T0.1-...`
4. Implement only the files in the card.
5. Write the tests named in the card.
6. Commit: `feat(T<id>): <summary>`.
7. Set the card's **Status** to `REVIEW` and run this script.
8. When the manager marks it done, move the card to `../done/` and run this script again.

## Status values

`TODO` -> `IN PROGRESS` -> `REVIEW` -> `DONE`. `BLOCKED` is allowed with a written reason.

## Index

| ID | Task | Phase | Depends on | Status |
|---|---|---|---|---|
{body}

---

## Phase exit gates

See `../spec/PHASES.md`. Do not start a phase until the previous phase's exit gate is green.
"""
    (TASKS / "README.md").write_text(text, encoding="utf-8", newline="\n")


def write_done(rows: list[dict]) -> None:
    body = "\n".join(done_row(row) for row in rows) or "| - | - | - | - | - |"
    text = f"""# Done Task Cards

Completed task cards live here. A card is moved here only after the manager has reviewed the spec compliance and the primary test.

## Move procedure

1. The implementer sets the card's **Status** to `REVIEW`.
2. The manager runs the card's primary test and reviews the diff.
3. If approved:
   - change the card's **Status** to `DONE`;
   - add `**Completed:** YYYY-MM-DD`, `**Commit:** <hash>`, and `**Reviewed by:** <name>` under the status line;
   - move the file from `../tasks/` to this folder;
   - run `python implementation/tools/rebuild_index.py`.
4. If changes are requested, keep it in `../tasks/` and write the findings in the card.

## Completed tasks

| ID | Task | Completed | Commit | Reviewed by |
|---|---|---|---|---|
{body}
"""
    (DONE / "README.md").write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    active = [parse(p) for p in TASKS.glob("T*.md")]
    completed = [parse(p) for p in DONE.glob("T*.md")]
    active.sort(key=sort_key)
    completed.sort(key=sort_key)
    write_tasks(active)
    write_done(completed)
    print(f"active: {len(active)}  done: {len(completed)}")


if __name__ == "__main__":
    main()
