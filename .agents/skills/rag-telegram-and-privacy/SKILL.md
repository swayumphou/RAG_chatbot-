---
name: rag-telegram-and-privacy
description: Use when touching Telegram handlers, project/portfolio cards, pagination, deep links, callback state, sender throttling, relay, consent, or any personal data.
---

# Telegram and Privacy

## The bot is a thin adapter

Every interaction maps to **one** `answer.service.answer()` call. No retrieval, ranking, threshold, prompt, or SQL logic in a handler.

`on_text` / `on_callback` does:
1. private-chat check;
2. duplicate check;
3. `sendChatAction("typing")`;
4. one `answer()` call;
5. format and send.

## Interaction contract

Commands:
`/start`, `/help`, `/search <q>`, `/senior <name>`, `/year <yyyy>`, `/about`.
Bare text = topic search.
Deep links: `?start=senior_<id>`, `?start=proj_<id>`.

Project card:
- title;
- authors, year, program, supervisor;
- verbatim snippet ≤ 400 chars;
- `— <title>, p.<n>`;
- `matched: semantic + keyword` (or actual path);
- buttons `Abstract`, `Portfolio`, `Repo` (if URL), `Next ▶`.

Ambiguous name:
- text `I found N seniors named <name>. Which one?`;
- one button per candidate;
- `pick:<state_key>:<index>`;
- never auto-select.

Portfolio:
- name (Latin + Khmer), program, cohort, supervisor;
- projects newest first;
- contact block only with consent;
- metadata-only label when there are no documents;
- paginate > 4096.

## Hard limits

| Limit | Value |
|---|---|
| Message | ≤ 4096 chars |
| `callback_data` | ≤ 64 bytes |
| Deep-link payload | ≤ 64 chars, `A-Za-z0-9_-` |
| Per-chat sends | ≥ 1 s apart, serialized |
| Global | under ~30 msg/s |
| Placeholder edits | ≤ 1 per 2 s |
| Duplicate query | 30 s cache |

- Escape titles/snippets with `html.escape(..., quote=False)` **before** adding HTML.
- `parse_mode=HTML`.
- `RetryAfter` → sleep `retry_after`, retry once, log and drop.
- Callback data carries refs, never a candidate list.
- `bot/state.py` is TTL’d, per chat, non-durable.

## Privacy rules

- Contact fields render only when `consent_public_contact=1`.
- Public surface: GitHub, LinkedIn, personal site, portfolio. University email only if opted in.
- Phone numbers and student IDs are never stored or indexed.
- No contact data in Qdrant payloads, prompts, logs, or training pairs.
- Telegram IDs are stored only as salted hashes.
- Relay requires `consent_relay=1` and a senior who has started the bot. Neither party sees the other's identity.
- Takedown: `status=withdrawn` removes the portfolio and chunks on the next run.

## Common Telegram/privacy bugs

- Handler doing retrieval or threshold logic.
- Title containing `<`, `&`, `_`, `*` breaking HTML.
- `callback_data` containing JSON or a full candidate row.
- `paginate()` silently dropping the tail of a > 4096 paragraph.
- No per-chat lock; 429 storm.
- Ambiguous name resolved to the first candidate.
- Contact email in the LLM prompt.
- Raw Telegram ID in `query_log`.
- Relay forwarding to a senior who never opted in.
- `/year` behaving like a course search instead of facet filters.

## Verification

Tests: `tests/unit/test_format.py`, `test_sender.py`, `test_state.py`, `test_relay.py`, `test_consent.py`, `test_bot.py`; integration: `tests/integration/test_bot_pipeline.py`.

Use a fake Telegram transport. Never contact Telegram in tests.
