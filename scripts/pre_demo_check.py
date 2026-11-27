"""Pre-demo health check: one command that says whether the demo is ready (T7.4).

    python scripts/pre_demo_check.py           # readable list
    python scripts/pre_demo_check.py --json    # machine-readable

Each check prints PASS, FAIL, or SKIP (SKIP = the data needed for it does not
exist, for example no two seniors share a name). Exit code 1 when any check
fails. Telegram is never contacted: the bot checks use a fake Telegram bot.

 1. point count == chunk count in SQLite
 2. every point has project_id, senior_ids, page_number, text
 3. the same query twice gives the same ranking
 4. a senior's Khmer and Latin names reach the same senior
 5. an ambiguous name gives a choice, not a guess
 6. a senior without consent shows no contact block
 7. a portfolio longer than 4096 characters is split into pages
 8. a nonsense query abstains and never calls the LLM
 9. re-ingesting the unchanged corpus adds 0 points
10. 50 rapid messages: no crash, sends spaced out
11. the loaded model tags are the pinned ones
12. Qdrant (and Ollama when ENABLE_LLM=true) are reachable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # after the installed libraries (see the note in Dockerfile)

from answer.consent import CONSENT_DENIED  # noqa: E402
from answer.service import answer  # noqa: E402
from bot import handlers  # noqa: E402
from bot.format import esc, portfolio_card, portfolio_page  # noqa: E402
from bot.sender import ChatThrottle, RecentReplies  # noqa: E402
from config import DEFAULT_OLLAMA_MODEL  # noqa: E402
from ingestion.indexer import count_points, scroll_all  # noqa: E402
from retrieval.portfolio import Portfolio  # noqa: E402

REQUIRED_KEYS = ("project_id", "senior_ids", "page_number", "text")
PROBE_QUERY = "khmer ocr"
NONSENSE_QUERY = "qzxv wplk mnbt vrrd"
BURST_GAP = 0.05  # a short gap keeps the 50-message check quick; the bot uses 1 s


def ask(kit, query="", **options):
    """One answer() call with the kit's services (the LLM stays off)."""
    return answer(
        query,
        kit.client,
        kit.embedder,
        kit.db,
        kit.threshold,
        False,
        collection=kit.collection,
        **options,
    )


def ranking(result) -> list[str]:
    return [p.project_id for p in result.projects or result.weak_matches]


# ---------------------------------------------------------------- the checks


def check_point_count(kit):
    expected = sum(kit.db.indexed_chunk_counts().values())
    actual = count_points(kit.client, kit.collection)
    if expected == 0:
        return "FAIL", "no chunks in SQLite: run python -m ingestion.run first"
    if actual != expected:
        return "FAIL", f"{actual} points in Qdrant but {expected} chunks in SQLite"
    return "PASS", f"{actual} points"


def check_payload_keys(kit):
    bad = []
    total = 0
    for point in scroll_all(kit.client, kit.collection):
        total += 1
        missing = [key for key in REQUIRED_KEYS if key not in point.payload]
        if missing:
            bad.append(f"{point.payload.get('chunk_id', point.id)} lacks {', '.join(missing)}")
    if bad:
        return "FAIL", "; ".join(bad[:3]) + (f" (+{len(bad) - 3} more)" if len(bad) > 3 else "")
    return "PASS", f"{total} points complete"


def check_stable_ranking(kit):
    first, second = ranking(ask(kit, PROBE_QUERY)), ranking(ask(kit, PROBE_QUERY))
    if first != second:
        return "FAIL", f"{first} then {second}"
    return "PASS", f"'{PROBE_QUERY}' -> {first}"


def check_khmer_and_latin_name(kit):
    for senior in kit.db.all_seniors():
        latin, khmer = senior["full_name_latin"], senior["full_name_khmer"]
        if not khmer or len(kit.db.alias_matches(latin)) != 1:
            continue
        by_latin = ask(kit, latin, intent="SENIOR_LOOKUP").resolved_senior
        by_khmer = ask(kit, khmer, intent="SENIOR_LOOKUP").resolved_senior
        if by_latin == by_khmer == senior["senior_id"]:
            return "PASS", f"'{latin}' and '{khmer}' -> {senior['senior_id']}"
        return "FAIL", f"'{latin}' -> {by_latin}, '{khmer}' -> {by_khmer}"
    return "SKIP", "no senior with a unique Latin name and a Khmer name"


def check_ambiguous_name(kit):
    for senior in kit.db.all_seniors():
        name = senior["full_name_latin"]
        if len(kit.db.alias_matches(name)) < 2:
            continue
        result = ask(kit, name, intent="SENIOR_LOOKUP")
        if result.mode == "ambiguous" and len(result.resolution.candidates) >= 2:
            return "PASS", f"'{name}' -> {len(result.resolution.candidates)} candidates"
        return "FAIL", f"'{name}' -> {result.mode} {result.resolved_senior}"
    return "SKIP", "no two seniors share a name"


def check_no_contact_without_consent(kit):
    for senior in kit.db.all_seniors():
        row = kit.db.get_senior(senior["senior_id"])
        if row["consent_public_contact"]:
            continue
        result = ask(kit, senior_id=row["senior_id"])
        text = portfolio_card(result.portfolio)
        leaked = [row[f] for f in ("contact_email", "github", "linkedin", "website") if row[f]]
        if "contact" in result.portfolio.senior or any(value in text for value in leaked):
            return "FAIL", f"{row['senior_id']} shows contact data without consent"
        if esc(CONSENT_DENIED) not in text:
            return "FAIL", f"{row['senior_id']}: the consent message is missing"
        return "PASS", f"{row['senior_id']} shows no contact block"
    return "SKIP", "every senior consented to public contact"


def check_long_portfolio_paginates(kit):
    projects = [
        {"project_id": f"p{i}", "title_en": f"A long project title number {i}", "year": 2024}
        for i in range(300)
    ]
    senior = {
        "senior_id": "s-check",
        "full_name_latin": "Check Name",
        "full_name_khmer": "",
        "program": "DSE",
        "cohort_year": 2024,
        "supervisor_id": "sup-0",
    }
    portfolio = Portfolio(senior, projects, False)
    pages, page = [], 0
    while True:
        text, markup = portfolio_page(portfolio, page)
        pages.append(text)
        has_next = any(b.text == "Next ▶" for row in markup.inline_keyboard for b in row)
        if not has_next:
            break
        page += 1
    if len(pages) < 2 or any(len(text) > 4096 for text in pages):
        return "FAIL", f"{len(pages)} pages, longest {max(map(len, pages))} characters"
    return "PASS", f"{len(pages)} pages, all <= 4096 characters"


def check_negative_abstains_without_model(kit):
    with mock.patch("answer.service.synthesize") as llm:  # notices any LLM call
        result = answer(
            NONSENSE_QUERY,
            kit.client,
            kit.embedder,
            kit.db,
            kit.threshold,
            True,  # LLM on: the gate alone must stop it
            collection=kit.collection,
        )
    if llm.called:
        return "FAIL", "the LLM was called for a query below the gate"
    if result.mode != "abstain":
        return "FAIL", f"'{NONSENSE_QUERY}' was answered ({result.mode}); the threshold may be low"
    return "PASS", "abstained, LLM not called"


def check_reingest_adds_nothing(kit):
    from ingestion.run import run_ingestion

    if not kit.projects_csv.exists() or not kit.corpus_dir.exists():
        return "SKIP", "manifest or corpus folder not found"
    report = run_ingestion(
        kit.projects_csv,
        kit.seniors_csv,
        kit.corpus_dir,
        kit.db,
        kit.client,
        kit.embedder,
        rebuild=False,
        collection=kit.collection,
        log_dir=kit.log_dir,
    )
    if report["status"] != "ok":
        return "FAIL", f"ingestion status {report['status']}"
    if report["new_points"] != 0:
        return "FAIL", f"{report['new_points']} new points for an unchanged corpus"
    return "PASS", f"0 new points ({report['points_after']} total)"


class FakeTelegram:
    """Records sends instead of talking to Telegram."""

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(time.perf_counter())

    async def send_chat_action(self, chat_id, action):
        pass


def check_rapid_messages(kit):
    bot = FakeTelegram()
    context = SimpleNamespace(
        bot=bot,
        chat_data={},
        args=[],
        bot_data={
            "client": kit.client,
            "embedder": kit.embedder,
            "db": kit.db,
            "threshold": kit.threshold,
            "enable_llm": False,
            "salt": "pre-demo-check",
            "throttle": ChatThrottle(min_gap=BURST_GAP),
            "recent": RecentReplies(),
            "chats": {},
        },
    )

    async def burst():
        updates = []
        for number in range(50):
            updates.append(
                SimpleNamespace(
                    effective_chat=SimpleNamespace(id=1, type="private"),
                    effective_user=SimpleNamespace(id=1),
                    effective_message=SimpleNamespace(text=f"{PROBE_QUERY} {number % 5}"),
                    callback_query=None,
                )
            )
        jobs = [handlers.on_text(update, context) for update in updates]
        return await asyncio.gather(*jobs, return_exceptions=True)

    # These test messages must not end up in query_log (training data).
    with mock.patch("bot.handlers.log_query"):
        outcomes = asyncio.run(burst())
    errors = [item for item in outcomes if isinstance(item, Exception)]
    if errors:
        return "FAIL", f"{len(errors)} messages crashed, first: {errors[0]!r}"
    gaps = [later - earlier for earlier, later in zip(bot.sent, bot.sent[1:], strict=False)]
    if gaps and min(gaps) < BURST_GAP:
        return "FAIL", f"two sends only {min(gaps):.3f} s apart"
    return "PASS", f"{len(bot.sent)} sends, no crash, spacing kept"


def check_model_tags(kit):
    problems = []
    loaded = kit.embedder.revision
    if not loaded.startswith(kit.embed_model):
        problems.append(f"embedder is {loaded}, config says {kit.embed_model}")
    baseline = {}
    if kit.baseline_path.exists() and kit.baseline_path.read_text(encoding="utf-8").strip():
        baseline = json.loads(kit.baseline_path.read_text(encoding="utf-8"))
    pinned_embed = baseline.get("model_tags", {}).get("embed_model")
    if pinned_embed and pinned_embed != loaded:
        problems.append(f"embedder is {loaded}, the eval baseline used {pinned_embed}")
    if kit.llm_model != DEFAULT_OLLAMA_MODEL:
        problems.append(f"OLLAMA_MODEL is {kit.llm_model}, pinned is {DEFAULT_OLLAMA_MODEL}")
    if problems:
        return "FAIL", "; ".join(problems)
    return "PASS", f"{loaded}, {kit.llm_model}"


def check_services_reachable(kit):
    import httpx

    try:
        kit.client.get_collections()
    except Exception as error:
        return "FAIL", f"Qdrant is not reachable: {error}"
    try:
        tags = httpx.get(f"{kit.ollama_url}/api/tags", timeout=5).json()
        models = [m.get("name") for m in tags.get("models", [])]
        ollama = "has" if kit.llm_model in models else "lacks"
        ollama_note = f"Ollama {ollama} {kit.llm_model}"
        ollama_ok = kit.llm_model in models
    except Exception as error:
        ollama_note, ollama_ok = f"Ollama not reachable ({type(error).__name__})", False
    if kit.enable_llm and not ollama_ok:
        return "FAIL", f"Qdrant OK; {ollama_note} but ENABLE_LLM=true"
    return "PASS", f"Qdrant OK; {ollama_note}" + ("" if kit.enable_llm else " (not needed)")


CHECKS = [
    ("point count matches chunks", check_point_count),
    ("payload keys complete", check_payload_keys),
    ("same query, same ranking", check_stable_ranking),
    ("Khmer and Latin name agree", check_khmer_and_latin_name),
    ("ambiguous name asks", check_ambiguous_name),
    ("no contact without consent", check_no_contact_without_consent),
    ("long portfolio paginates", check_long_portfolio_paginates),
    ("negative abstains, no LLM", check_negative_abstains_without_model),
    ("re-ingest adds 0 points", check_reingest_adds_nothing),
    ("50 rapid messages", check_rapid_messages),
    ("model tags pinned", check_model_tags),
    ("services reachable", check_services_reachable),
]


def run_checks(kit) -> list[dict]:
    """Run every check; an unexpected error is a FAIL that names the error."""
    results = []
    for name, check in CHECKS:
        try:
            status, detail = check(kit)
        except Exception as error:
            status, detail = "FAIL", f"error: {error!r}"
        results.append({"check": name, "status": status, "detail": detail})
    return results


# ---------------------------------------------------------------- command line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Is the demo ready? Never contacts Telegram.")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--db", type=Path, default=None, help="default data/kb.sqlite3")
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--qdrant-path", default=None, help="embedded Qdrant (smoke tests only)")
    parser.add_argument("--projects", type=Path, default=ROOT / "data" / "projects.csv")
    parser.add_argument("--seniors", type=Path, default=ROOT / "data" / "seniors.csv")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus")
    args = parser.parse_args(argv)

    from config import get_config
    from ingestion.embedder import Embedder
    from ingestion.indexer import connect_qdrant
    from store.db import DB, DEFAULT_DB_PATH

    config = get_config()
    db_path = args.db or DEFAULT_DB_PATH
    if not db_path.exists():
        print(f"FAIL  {db_path} not found: run python -m ingestion.run first")
        return 1
    embedder = Embedder(config.embed_model)
    embedder.load()
    kit = SimpleNamespace(
        client=connect_qdrant(args.qdrant_url, args.qdrant_path),
        embedder=embedder,
        db=DB(db_path),
        collection=None,
        threshold=config.abstain_threshold,
        enable_llm=config.enable_llm,
        ollama_url=config.ollama_url,
        embed_model=config.embed_model,
        llm_model=config.ollama_model,
        baseline_path=ROOT / "eval" / "baseline.json",
        projects_csv=args.projects,
        seniors_csv=args.seniors,
        corpus_dir=args.corpus,
        log_dir=None,
    )
    results = run_checks(kit)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(f"{item['status']:<5} {item['check']}: {item['detail']}")
    failed = [item for item in results if item["status"] == "FAIL"]
    if not args.json:
        print(f"\n{'NOT READY' if failed else 'READY'}: {len(failed)} failed check(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
