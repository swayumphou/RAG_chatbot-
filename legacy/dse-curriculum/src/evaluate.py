"""Evaluation on evaluation/golden_set.csv, reported per slice. It calls answer() directly, so the
numbers describe the same code path users get through the terminal, Telegram and the web page.

Golden-set columns: expected_ids are the sources or courses that must be shown (any one of them);
expected_answer is a reference answer written by hand from the sources (for people, not checked);
must_contain lists key facts the answer text must include: "|" separates facts that are all
required, "/" separates accepted spellings of one fact ("catalog/catalogue"). Case is ignored.

    python src\\evaluate.py --llm      results as users see them (Gemma on)
    python src\\evaluate.py            retrieval only (Gemma off)
    python src\\evaluate.py --sweep    retrieval only + the abstention-threshold table
"""
import argparse
import csv
import json
from collections import defaultdict

import config
from answer import KB, answer, render

GOLDEN = config.EVALUATION_DIR / "golden_set.csv"
REPORT = config.EVALUATION_DIR / "last_report.json"
SWEEP = [x / 100 for x in range(30, 75, 5)]


def first_hit(result: dict, expected: set) -> int | None:
    """Rank of the first shown item that is an expected source or course (None = miss)."""
    if result["mode"] == "abstain":
        return None
    if result["courses"]:                             # an exact record list is one answer
        return 1 if expected <= {c["title"] for c in result["courses"]} else None
    return next((p["n"] for p in result["passages"] if expected & {p["source_id"], p["course_title"]}), None)


def answer_ok(result: dict, must_contain: str) -> bool | None:
    """Every key fact appears in what the user reads (None = nothing to check)."""
    if not must_contain:
        return None
    if result["mode"] == "abstain":
        return False
    text = (result["text"] or render(result)).casefold()   # Gemma's answer, else the records / quotes
    return all(any(alt.strip().casefold() in text for alt in fact.split("/")) for fact in must_contain.split("|"))


def evaluate(golden: list[dict], kb: KB, threshold: float) -> list[dict]:
    rows = []
    for g in golden:
        r = answer(g["query"], kb, surface="eval", threshold=threshold)
        rows.append({**g, "route": r["route"], "mode": r["mode"], "top_score": r["top_score"],
                     "rank": first_hit(r, {x for x in g["expected_ids"].split("|") if x}),
                     "answer_ok": answer_ok(r, g.get("must_contain", "")), "answer": r["text"]})
    return rows


def share(rows: list[dict], test) -> float:
    return sum(1 for r in rows if test(r)) / max(len(rows), 1)


def is_negative(row: dict) -> bool:
    return row["slice"].startswith("negative")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the assistant on the golden set.")
    parser.add_argument("--sweep", action="store_true", help="also print the threshold table")
    parser.add_argument("--llm", action="store_true", help="evaluate with Gemma switched on")
    args = parser.parse_args()

    kb = KB(enable_llm=args.llm)
    with open(GOLDEN, encoding="utf-8-sig", newline="") as f:
        golden = list(csv.DictReader(f))

    threshold = config.ABSTAIN_THRESHOLD
    rows = evaluate(golden, kb, threshold)
    by_slice = defaultdict(list)
    for r in rows:
        by_slice[r["slice"]].append(r)

    print(f"\nthreshold={threshold}  embed={config.EMBED_MODEL}  "
          f"llm={config.OLLAMA_MODEL if args.llm else 'off'}  queries={len(rows)}\n")
    print(f"{'slice':<13}{'n':>4}{'route_acc':>11}{'recall@5':>10}{'mrr':>7}{'answer_ok':>11}{'abstained':>11}")
    summary = []
    for name, group in sorted(by_slice.items()):
        negative = is_negative(group[0])
        checked = [r for r in group if r["answer_ok"] is not None]
        stats = {"slice": name, "n": len(group),
                 "route_acc": share(group, lambda r: r["route"] == r["expected_route"]),
                 "recall@5": None if negative else share(group, lambda r: r["rank"] and r["rank"] <= config.TOP_K),
                 "mrr": None if negative else sum(1 / r["rank"] for r in group if r["rank"]) / len(group),
                 "answer_ok": share(checked, lambda r: r["answer_ok"]) if checked else None,
                 "abstained": share(group, lambda r: r["mode"] == "abstain")}
        summary.append(stats)
        fmt = lambda v: "-" if v is None else f"{v:.2f}"
        print(f"{name:<13}{stats['n']:>4}{stats['route_acc']:>11.2f}{fmt(stats['recall@5']):>10}"
              f"{fmt(stats['mrr']):>7}{fmt(stats['answer_ok']):>11}{stats['abstained']:>11.2f}")

    for r in rows:
        wrong_route = r["route"] != r["expected_route"]
        if wrong_route or (is_negative(r) and r["mode"] != "abstain") or (not is_negative(r) and not r["rank"]):
            print(f"  FAIL {r['query_id']}: {r['query']!r} route={r['route']} mode={r['mode']} score={r['top_score']}")
        elif r["answer_ok"] is False:
            print(f"  WRONG ANSWER {r['query_id']}: needs {r['must_contain']!r}\n      got: {r['answer'][:300]!r}")

    sweep = []
    if args.sweep:
        searched = [r for r in rows if r["route"] == "QUESTION"]
        off = max((r["top_score"] for r in searched if is_negative(r)), default=0.0)
        on = min((r["top_score"] for r in searched if not is_negative(r)), default=1.0)
        print(f"\nhighest unanswerable score {off:.4f} | lowest answerable score {on:.4f}")
        print("\nthreshold  unanswerable refused  answerable recall@5")
        for t in SWEEP:
            at = evaluate(golden, kb, t)
            neg, pos = [r for r in at if is_negative(r)], [r for r in at if not is_negative(r)]
            sweep.append({"threshold": t, "refused": share(neg, lambda r: r["mode"] == "abstain"),
                          "recall@5": share(pos, lambda r: r["rank"] and r["rank"] <= config.TOP_K)})
            print(f"{t:>9.2f}{sweep[-1]['refused']:>22.2f}{sweep[-1]['recall@5']:>21.2f}")

    REPORT.write_text(json.dumps({"threshold": threshold, "embed_model": config.EMBED_MODEL,
                                  "llm": config.OLLAMA_MODEL if args.llm else None, "slices": summary,
                                  "sweep": sweep, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved -> {REPORT}")


if __name__ == "__main__":
    main()
