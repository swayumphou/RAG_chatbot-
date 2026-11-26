# Agent Resources

This folder is the shared instruction layer for coding agents working on RUPP-SPKB v2.0.

## Contents

```text
.agents/
└── skills/
    ├── README.md
    ├── rag-project-context/SKILL.md
    ├── implement-rag-task/SKILL.md
    ├── rag-data-and-indexing/SKILL.md
    ├── rag-retrieval-and-answer/SKILL.md
    ├── rag-evaluation/SKILL.md
    ├── rag-telegram-and-privacy/SKILL.md
    └── rag-code-review/SKILL.md
```

## Load order

1. `../CLAUDE.md` — project-wide instructions.
2. `.agents/skills/rag-project-context/SKILL.md` — product and invariants.
3. The task-specific skill for the work at hand.
4. The task card in `../implementation/tasks/` and the shared contracts in `../implementation/spec/`.

## Rules

- Skills are project-specific; do not replace them with generic advice.
- If a skill conflicts with `implementation/spec/INTERFACES.md`, the interface wins.
- If a skill conflicts with a task card, the task card wins for that task; record the conflict in the card or an ADR.
- Never put secrets, real student data, or contact details in a skill or a fixture.
- Keep one canonical copy in `.agents/skills/`; do not fork the content into `.claude/skills/` and `.agents/skills/` with different text.

Claude Code discovers `.agents/skills/` automatically. Other harnesses may need their own pointer.
