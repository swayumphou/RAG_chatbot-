# Project Skills for Claude

These are **project-specific playbooks**, not generic coding advice. Each one encodes decisions, file paths, constants, and failure modes from the RUPP-SPKB v2.0 specs and the implementation plan.

## How to use

1. Read `../../CLAUDE.md` first.
2. Read the skill that matches the task **before** editing code.
3. Read the task card in `../../implementation/tasks/` and the shared contracts in `../../implementation/spec/`.
4. Follow the skill's verification section.

If a skill and a task card disagree, the task card wins for that task, but record the conflict in the task card or an ADR. If a skill and `INTERFACES.md` disagree, `INTERFACES.md` wins.

## Skills

| Skill | Use when |
|---|---|
| [`rag-project-context`](rag-project-context/SKILL.md) | Starting any session or task; loading the product, repo state, and non-negotiables. |
| [`implement-rag-task`](implement-rag-task/SKILL.md) | Picking up a task card, branching, implementing, testing, and reporting. |
| [`rag-data-and-indexing`](rag-data-and-indexing/SKILL.md) | Touching manifests, corpus files, parsers, chunking, embeddings, Qdrant, or ingestion. |
| [`rag-retrieval-and-answer`](rag-retrieval-and-answer/SKILL.md) | Touching intent routing, hybrid retrieval, entity resolution, portfolios, gates, Gemma, citations, or consent. |
| [`rag-evaluation`](rag-evaluation/SKILL.md) | Touching the golden set, metrics, eval runner, threshold sweep, baseline, or training pairs. |
| [`rag-telegram-and-privacy`](rag-telegram-and-privacy/SKILL.md) | Touching Telegram handlers, cards, pagination, sender throttling, state, relay, or privacy. |
| [`rag-code-review`](rag-code-review/SKILL.md) | Reviewing a task branch or the whole `refactored` branch. |

## Claude Code discovery

This repository keeps the canonical skills in `.agents/skills/`. Claude Code discovers this folder directly. If another harness only discovers `.claude/skills/`, point it at this folder or copy the skill directories there; do not maintain two different copies of the content.

## What makes these skills different from generic advice

- They name the exact V2.0 modules and task cards.
- They pin the project's constants (chunk sizes, RRF, gate, Telegram limits).
- They list the RAG-specific bugs this project exists to prevent: page-level grouping instead of project grouping, numeric citations, LLM calls below the gate, BM25 substituted for bge-m3 sparse, missing payload indexes, ambiguous names auto-merged, contact data in prompts.
- They say what not to do, so a coding agent does not "helpfully" invent senior names, projects, or test data.
