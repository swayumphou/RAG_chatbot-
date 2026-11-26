# Task Template

Copy this shape when adding a task card. Keep one task per file and name the file `T<phase>.<n>-<slug>.md`.

```markdown
# T0.0 - Short imperative title

**Phase:** 0 - Phase name
**Status:** TODO
**Depends on:** T0.0 or "none"

**Goal:** One sentence: what exists after this task that did not before.

**Spec:**
- exact requirement, value, or edge case;
- exact field name, endpoint, callback, or error string;
- what must NOT happen;
- the spec section or decision this comes from.

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py`
- Test: `tests/unit/test_file.py`

**Interfaces:**
- Consumes: exact names/types from earlier tasks.
- Produces: exact names/types later tasks rely on.

**Steps:**
- [ ] One checkable action.
- [ ] One checkable action.
- [ ] One checkable action.

**Acceptance:**
- [ ] Observable result.
- [ ] Edge case covered.

**Tests:**
- `tests/unit/test_file.py::test_specific_case`

**Out of scope:** what this task deliberately does not do.
```

## Rules for a good card

- It fits in one review sitting (roughly 1–3 hours).
- It names exact files; the implementer does not guess.
- It names exact public names and types; neighbors can integrate without reading the body.
- It has at least one test that would fail before the change and pass after.
- It states the negative case, not only the happy path.
- It does not re-implement a spec paragraph; it points to the spec and pins the values.
- It does not contain a full program body; a signature, a value, and a test are enough.
- It lists what is out of scope so the implementer does not expand the diff.
