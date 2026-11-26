# Done Task Cards

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
| - | - | - | - | - |
