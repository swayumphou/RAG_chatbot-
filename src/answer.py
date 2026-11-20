"""The answer layer, and the terminal chat. The Telegram bot, the web page and the evaluator all call `answer()`.

    python src\\answer.py "What do I study in year 1?"     one question
    python src\\answer.py                                  interactive chat (empty line to quit)
    python src\\answer.py --collection practice            ask the learning material instead
    python src\\answer.py --extractive "..."               quote source passages only, without Gemma
    python src\\answer.py --language Khmer "..."           reply in Khmer (default: the question's language)

In the order a question passes through this file:
  1. answer()      route -> exact course records, or search + relevance gate
  2. translate()   a Khmer question is searched in English (the sources are English)
     generate()    Gemma writes the answer from the passages; citations_valid() checks it
  3. render()      result -> readable text (plain for the terminal, HTML for Telegram and the web page)

Result shape returned by answer():
  {query_id, collection, route, mode, heading, text, courses, passages, top_score, note, searched, language}
  searched: the text that was searched ("" for record answers; English for a Khmer question)
  language: the reply language - the one the user chose, else the question's (see reply_language)
  suggestions: 3 follow-up questions for the buttons in Telegram and on the web page (see suggest)
  mode: record      exact course records from SQLite
        llm         Gemma answer whose citations passed the validator
        extractive  verbatim passages + citations (Gemma off, failed, or rejected)
        abstain     nothing relevant enough, or Gemma said the sources do not answer
"""
import argparse
import json
import re
import threading
import time
import uuid
from html import escape
from itertools import zip_longest

import httpx

import config
from curriculum import DB, contains_khmer, normalize, route
from hybrid import Searcher, select

# --- 1. entry point ----------------------------------------------------------------------------


class KB:
    """Everything a query needs, opened once per process."""

    def __init__(self, collection: str = "official", enable_llm: bool = config.ENABLE_LLM):
        self.collection, self.enable_llm = collection, enable_llm
        self.searcher = Searcher(collection)
        self.db = DB()
        self.lock = threading.Lock()                  # one query at a time through the models
        # `programs`: every department program, so "ITE courses" is recognized even without ITE records
        # (and refused, not answered with another program's). `names`, `titles`: programs with records.
        self.programs = ({pid: normalize(name) for pid, name in config.PROGRAM_NAMES.items()}
                         if collection == "official" else {})
        self.names, self.titles = {}, {}
        if collection == "official" and self.db.has_courses():
            for c in self.db.courses():
                self.names[c["program_id"]] = c["program_name"]
                self.programs[c["program_id"]] = normalize(c["program_name"])
                self.titles.setdefault(c["program_id"], set()).add(c["title_norm"])


LANGUAGES = ("English", "Khmer")


def reply_language(question: str, chosen: str | None = None) -> str:
    """The language the user chose (Telegram, web page); without a choice, the question's language."""
    return chosen if chosen in LANGUAGES else ("Khmer" if contains_khmer(question) else "English")


def answer(query: str, kb: KB, surface: str = "cli", user_hash: str | None = None,
           threshold: float = config.ABSTAIN_THRESHOLD, language: str | None = None) -> dict:
    started = time.perf_counter()
    language = reply_language(query, language)
    with kb.lock:
        routed = route(query, kb.programs, kb.titles)
        named = routed["compared"] or [p for p in [routed["program"]] if p]
        if named and kb.programs and not any(p in kb.names for p in named):   # e.g. ITE, with no ITE data
            result = _result("QUESTION", "abstain", note=f"no records for {', '.join(named)}")
        else:
            result = ((_records(routed, kb, language) if kb.names else None)
                      or _grounded(query, kb, threshold, language, [p for p in named if p in kb.names]))
        sections = result["sections"]
        program = routed["program"] or (sections[0]["program"] if len(sections) == 1 else None)
        result.update(query_id=uuid.uuid4().hex, collection=kb.collection, language=language,
                      suggestions=suggest(language, program, routed["year"], query, tuple(kb.names))
                      if kb.collection == "official" else [])
        if surface != "eval" and kb.collection == "official":     # the log is training data: official only
            retrieved = [{"id": p["course_id"] or p["source_id"], "anchor": p["anchor"], "rank": p["n"],
                          "score": p["score"]} for p in result["passages"]] \
                or [{"id": c["course_id"]} for c in result["courses"]]
            kb.db.log_query(query_id=result["query_id"], surface=surface, user_hash=user_hash,
                            raw_query=query, normalized_query=routed["normalized"], route=result["route"],
                            mode=result["mode"], retrieved_json=json.dumps(retrieved),
                            note=" | ".join(filter(None, [result["note"], result["searched"] not in ("", query)
                                                          and f"searched: {result['searched']}"])),
                            top_score=result["top_score"],
                            latency_ms=int((time.perf_counter() - started) * 1000))
    return result


SUGGEST = {   # follow-up question forms the assistant answers (each form has a golden-set question)
    "English": {"courses": "{p} courses in year {y}", "teach": "What does the {p} program teach?",
                "compare": "What is the difference between {a} and {b}?",
                "jobs": "What jobs can DSE graduates do?", "research": "What are the DSE research areas?"},
    "Khmer": {"courses": "មុខវិជ្ជា {p} ឆ្នាំទី{y}", "teach": "តើជំនាញ {p} រៀនអ្វីខ្លះ?",
              "compare": "តើ {a} និង {b} ខុសគ្នាដូចម្តេច?",
              "jobs": "តើបញ្ចប់ DSE អាចធ្វើការអ្វីបាន?", "research": "តើ DSE មានវិស័យស្រាវជ្រាវអ្វីខ្លះ?"},
}


def suggest(language: str, program: str | None = None, year: int | None = None, asked: str = "",
            available: tuple = ("DSE", "ITE", "AIE")) -> list[str]:
    """3 follow-up questions for the buttons next to the typing box (Telegram keyboard, web page bar).
    Chosen by code, never written by Gemma, so every button leads to a real answer. Only programs with
    records (`available`) are offered. No program known: DSE and ITE. After a program: that program
    again (the next year), its overview, and a comparison. Jobs and research areas exist for DSE only
    (passages in rupp-dse-degree.json)."""
    s = SUGGEST[language]
    courses = lambda p, y: s["courses"].format(p=p, y=_num(y, language))   # noqa: E731
    known = [p for p in ("DSE", "ITE", "AIE") if p in available]
    if not known:
        return []
    if program in known:
        other = next((p for p in known if p != program), None)
        options = [courses(program, year % 4 + 1 if year else 1), s["teach"].format(p=program)]
        options += [s["compare"].format(a=program, b=other)] if other else []
    else:
        main = known[:2]
        options = [courses(p, year or 1) for p in main]
        options += [s["compare"].format(a=main[0], b=main[1])] if len(main) > 1 else [s["teach"].format(p=main[0])]
    options += [s["jobs"], s["research"]] if "DSE" in known else []
    options += [courses(program or known[0], 2)]
    return [q for q in dict.fromkeys(options) if normalize(q) != normalize(asked)][:3]


def _result(route_name: str, mode: str, **extra) -> dict:
    return {"route": route_name, "mode": mode, "heading": "", "text": "", "courses": [], "sections": [],
            "passages": [], "top_score": None, "note": "", "searched": "", **extra}


def _records(routed: dict, kb: KB, language: str) -> dict | None:
    """Exact records, one section per program: two (ITE, DSE) when the question names no program,
    so two programs' courses are never presented as one curriculum."""
    if routed["intent"] == "QUESTION":
        return None
    t, year, semester = TEXT[language], routed["year"], routed["semester"]
    sections = []
    for program in routed["programs"]:
        courses = kb.db.courses(program, year, semester, routed["title"]) if program in kb.names else []
        if courses:
            heading = f"{t['programs'].get(program, kb.names[program])} ({program})"
            heading += t["year"].format(y=_num(year, language)) if year else ""
            heading += t["semester"].format(s=_num(semester, language)) if semester else ""
            sections.append({"program": program, "heading": heading, "courses": courses})
    if not sections:
        return None                                   # nothing exact -> fall through to search
    return _result(routed["intent"], "record", sections=sections, heading=sections[0]["heading"],
                   courses=[c for s in sections for c in s["courses"]])


def _passages(docs: list) -> list[dict]:
    return [{"n": n, "source_id": d.metadata["source_id"], "title": d.metadata["source_title"],
             "anchor": d.metadata["anchor"], "section": d.metadata["section"], "url": d.metadata.get("url", ""),
             "course_id": d.metadata.get("course_id"), "course_title": d.metadata.get("course_title"),
             "score": d.metadata["cosine"], "body": d.page_content.split("\n", 1)[-1]}
            for n, d in enumerate(docs, start=1)]


PROGRAM_QUERY = "What does the {name} ({id}) bachelor program teach?"   # finds that program's overview first


def _grounded(query: str, kb: KB, threshold: float, language: str, programs: list[str] = ()) -> dict:
    """`programs`: the programs the question names ("what is DSE?", "DSE vs ITE")."""
    searched = query
    if kb.enable_llm and contains_khmer(query):       # the sources are English: search in English
        try:
            searched = translate(query)
        except Exception:                             # Ollama down -> search the Khmer text as it is
            pass
    if programs and kb.names:
        # A bare abbreviation embeds poorly: "what is DSE?" found one course passage (score 0.49), not
        # the DSE overview; "DSE vs ITE" found DSE passages only (every chunk names the Department of
        # Information Technology Engineering). So: one neutral search per named program, plus the
        # question itself, results taken in turns, so every named program's overview is offered.
        runs = [sorted(kb.searcher.candidates(PROGRAM_QUERY.format(name=config.PROGRAM_NAMES.get(p, kb.names[p]),
                                                                   id=p)),                   # by meaning:
                       key=lambda d: d.metadata["cosine"], reverse=True) for p in programs]    # overview first
        # The question's own search is ranked by meaning too: every course record of the program contains
        # its name, so keyword ranking put course records above the passage that answers ("How are DSE
        # students evaluated?" lost the evaluation passage, 0.64, to course records).
        runs.append(sorted(kb.searcher.candidates(searched), key=lambda d: d.metadata["cosine"], reverse=True))
        candidates = [d for group in zip_longest(*runs) for d in group if d is not None]
    else:
        candidates = kb.searcher.candidates(searched)
    closest = sorted(candidates, key=lambda d: d.metadata["cosine"], reverse=True)
    top = closest[0].metadata["cosine"] if closest else 0.0
    passages = _passages(select(candidates, threshold))

    common = {"top_score": top, "searched": searched}
    if not passages:                                  # nothing relevant enough: the answer model is never called
        return _result("QUESTION", "abstain", passages=_passages(closest[:3]), **common)
    if not kb.enable_llm:
        return _result("QUESTION", "extractive", passages=passages, **common)

    try:
        # Gemma answers the original question; for an English reply to a Khmer question it gets the
        # English translation instead (asked in Khmer to reply in English, it refused or replied in Khmer).
        draft = generate(searched if language == "English" else query, passages, kb.collection, language)
    except Exception as exc:                          # Ollama down / timeout / cut off -> sources still served
        return _result("QUESTION", "extractive", passages=passages, note=f"llm_failed: {exc}", **common)
    if NO_ANSWER in draft:
        return _result("QUESTION", "abstain", passages=passages[:3], note="llm_no_answer", **common)
    if not citations_valid(draft, len(passages)):
        return _result("QUESTION", "extractive", passages=passages, note="llm_citations_rejected", **common)
    return _result("QUESTION", "llm", text=draft, passages=passages, **common)


# --- 2. grounded synthesis with Gemma (via Ollama) + the deterministic citation validator ------

NO_ANSWER = "The official sources I have do not answer this question."

AUDIENCE = {
    "official": "You help high-school graduates understand the Data Science and Engineering (DSE)\n"
                "program and its sister programs at RUPP.",
    "practice": "You help a student study the learning material they were given.",
}
RULES = f"""Answer ONLY from the numbered passages inside <sources>.

Rules:
1. Use only the passages. No outside knowledge, no guessing.
2. End every factual sentence with its passage number, like [1] or [2].
3. If the passages do not answer the question, output exactly this English sentence, even for a
   Khmer question: {NO_ANSWER}
   Never answer a different question instead (for example, the curriculum when asked about research).
4. Never invent a course, credit count, fee, date, or requirement.
5. Reply in the language named at the end of these rules, in under 900 characters. Use short bullet
   points for lists and **bold** for key terms.
6. When you reply in Khmer: write natural, polite, formal Khmer for university students (say និស្សិត,
   not សិស្ស). Keep technical terms and course names in English, for example Python, Calculus,
   Data Structures. Name the programs វិស្វកម្មវិទ្យាសាស្ត្រទិន្នន័យ (DSE), វិស្វកម្មបច្ចេកវិទ្យាព័ត៌មាន (ITE)
   and វិស្វកម្មបញ្ញាសិប្បនិម្មិត (AIE). Use these terms: year 1 = ឆ្នាំទី១, semester 2 = ឆមាសទី២ (never
   សេមីស្ទ័រ), credit = ក្រេឌីត, core course = មុខវិជ្ជាគោល, elective course = មុខវិជ្ជាជ្រើសរើស.
   Use Khmer letters only, never Thai or Burmese letters or punctuation.
7. ITE, DSE and AIE are three different programs. Never present one program's courses or facts as
   another's. If the question names no program and the passages differ by program, answer for each
   program separately and name it."""

CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")      # [1] and [2, 3]


def _chat(system: str, user: str, num_predict: int = config.NUM_PREDICT) -> str:
    """One Gemma call through Ollama. Every call uses the same num_ctx: a different value can make
    Ollama reload the model. `think` is off: hidden reasoning would eat the num_predict budget."""
    response = httpx.post(f"{config.OLLAMA_URL}/api/chat", timeout=config.LLM_TIMEOUT, json={
        "model": config.OLLAMA_MODEL, "stream": False, "think": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"temperature": 0.0, "num_ctx": config.NUM_CTX, "num_predict": num_predict}})
    response.raise_for_status()
    data = response.json()
    if data.get("done_reason") == "length":
        raise RuntimeError("answer was cut off at the num_predict limit")
    return data["message"]["content"].strip().translate(LOOKALIKES)


def generate(question: str, passages: list[dict], collection: str = "official", language: str | None = None) -> str:
    context = "\n\n".join(f"[{p['n']}] ({p['title']}, {p['anchor']})\n{p['body']}" for p in passages)
    language = language or reply_language(question)   # decided in code, not guessed by Gemma
    # The language is said twice: with the rule alone, Gemma answered 3 of 8 English questions in English
    # or refused them when Khmer was chosen. The refusal sentence is repeated too, or Gemma translates it.
    text = _chat(f"{AUDIENCE[collection]} {RULES}\n\nLanguage: {language}",
                 f"<sources>\n{context}\n</sources>\n\nQuestion: {question}\n\n"
                 f"Write the answer in {language}. If the passages do not answer it, write only: {NO_ANSWER}")
    if language == "Khmer" and FOREIGN_RE.search(text):   # a whole Thai word ("ฝึกงาน"): one repair call
        text = _chat(REPAIR, text)
        if FOREIGN_RE.search(text):
            raise RuntimeError("the Khmer answer still contains Thai or Burmese words")   # -> quoted sources
    return khmer_style(text) if language == "Khmer" else text


# Gemma sometimes writes a Thai or Burmese letter that looks like a Khmer one ("វិស្វកម្ម" came out as
# "វិស្វกម្ម"). These are the slips seen so far, mapped back; add a pair when a new one appears.
LOOKALIKES = str.maketrans({"ก": "ក", "ว": "វ", "၊": ",", "။": "។"})
FOREIGN_RE = re.compile(r"[฀-๿က-႟]")   # Thai or Myanmar letters left after that
REPAIR = ("Rewrite the user's text in Khmer only: replace every Thai or Burmese word with the right Khmer "
          "word. Change nothing else - keep the [1]-style citations, the English terms and the numbers.")
# Loanwords Gemma uses where RUPP has a Khmer term ("សេមីស្ទ័រទី 1" -> "ឆមាសទី១"); add a pair when seen.
KHMER_TERMS = {"សេមីស្ទ័រ": "ឆមាស"}
ENGLISH_TERM_RE = re.compile(r"\b(year|semester)\s+([0-9])\b", re.I)    # "year 3" inside a Khmer sentence
TERM_NUMBER_RE = re.compile(r"(ឆ្នាំទី|ឆមាសទី)\s*([0-9]+)")


def khmer_style(text: str) -> str:
    """RUPP's Khmer terms (ឆ្នាំទី៣ ឆមាសទី១) in a Khmer answer, with Khmer numerals after them; a program's
    first mention gets its Khmer name too. Other numbers (phone, credits, dates) stay as written."""
    for loan, term in KHMER_TERMS.items():
        text = text.replace(loan, term)
    for pid, english in config.PROGRAM_NAMES.items():     # Gemma keeps the English name despite rule 6
        text = text.replace(f"{english} ({pid})", f"{TEXT['Khmer']['programs'][pid]} ({english}, {pid})", 1)
    text = ENGLISH_TERM_RE.sub(lambda m: {"year": "ឆ្នាំទី", "semester": "ឆមាសទី"}[m[1].lower()] + m[2], text)
    return TERM_NUMBER_RE.sub(lambda m: m[1] + m[2].translate(KHMER_NUMERALS), text)


# Khmer words the translator gets wrong without help ("ជំនាញ" came out as "specialties", which the
# sources call "majors"). Add a line here when a Khmer question misses a source its English twin finds.
GLOSSARY = {"ដេប៉ាតឺម៉ង់": "department", "ជំនាញ": "major", "ឆ្នាំសិក្សាមូលដ្ឋាន": "foundation year",
            "មុខវិជ្ជា": "course", "ក្រេឌីត": "credits", "ឆមាស": "semester", "សាកលវិទ្យាល័យ": "university"}
TRANSLATE = ("Translate the user's question from Khmer to English. Output only the English question. "
             "Keep names as they are. Use these terms: " + "; ".join(f"{k} = {v}" for k, v in GLOSSARY.items()))


def translate(question: str) -> str:
    """Khmer question -> English, used only for searching (bge-m3 scores Khmer against English text low)."""
    return _chat(TRANSLATE, question, num_predict=80) or question


def citations_valid(text: str, n_passages: int) -> bool:
    """At least one citation, and every citation points at a passage that was actually retrieved."""
    found = {int(n) for group in CITE_RE.findall(text) for n in group.split(",")}
    return bool(found) and found <= set(range(1, n_passages + 1))


# --- 3. rendering: result -> text (plain for the terminal, HTML for Telegram and the web page) -

TEXT = {    # the fixed words of an answer in each reply language (Gemma's own text follows RULES 5-7)
    "English": {
        "abstain": "I could not find this in the sources I have.",
        "hint": " Try asking about a course, the courses in a year or semester, or what a program teaches.",
        "closest": "Closest sources (low confidence):", "sources": "Sources",
        "term": "Year {y}, semester {s}", "year": " · year {y}", "semester": " · semester {s}",
        "credit": "{c} credit", "credits": "{c} credits", "hours": ", hours {h}", "option": "Option {n}",
        "review": " (record needs review)", "note": "Note: {n}", "total": "Total: {n} courses, {c} credits",
        "source": "Source: {title} · {url} · retrieved {date}",
        "both": "You did not name a program, so here are {programs} separately:", "and": " and ",
        "other": "To see one program only, or AIE, name it in the question, e.g. “AIE courses in year 1”.",
        "programs": {}},                              # English program names come from the records
    "Khmer": {
        "abstain": "ខ្ញុំរកមិនឃើញព័ត៌មាននេះនៅក្នុងប្រភពដែលខ្ញុំមានទេ។",
        "hint": " សូមសួរអំពីមុខវិជ្ជាណាមួយ មុខវិជ្ជាក្នុងឆ្នាំ ឬឆមាសណាមួយ ឬអ្វីដែលជំនាញមួយបង្រៀន។",
        "closest": "ប្រភពដែលជិតបំផុត (ទំនុកចិត្តទាប)៖", "sources": "ប្រភព",
        "term": "ឆ្នាំទី{y} ឆមាសទី{s}", "year": " · ឆ្នាំទី{y}", "semester": " · ឆមាសទី{s}",
        "credit": "{c} ក្រេឌីត", "credits": "{c} ក្រេឌីត", "hours": ", ម៉ោង {h}", "option": "មុខវិជ្ជាជ្រើសរើស {n}",
        "review": " (កំណត់ត្រានេះត្រូវពិនិត្យឡើងវិញ)", "note": "កំណត់សម្គាល់៖ {n}",
        "total": "សរុប៖ មុខវិជ្ជា {n} · {c} ក្រេឌីត", "source": "ប្រភព៖ {title} · {url} · ទាញយកថ្ងៃទី {date}",
        "both": "ដោយសារអ្នកមិនបានបញ្ជាក់ជំនាញ ខាងក្រោមនេះជាមុខវិជ្ជាសម្រាប់ជំនាញ {programs} ដាច់ដោយឡែកពីគ្នា៖",
        "and": " និង ",
        "other": "សម្រាប់ជំនាញ AIE ឬដើម្បីមើលតែជំនាញមួយ សូមបញ្ជាក់ឈ្មោះជំនាញនៅក្នុងសំណួរ ឧទាហរណ៍ «មុខវិជ្ជា AIE ឆ្នាំទី១»។",
        "programs": {"DSE": "វិស្វកម្មវិទ្យាសាស្ត្រទិន្នន័យ", "ITE": "វិស្វកម្មបច្ចេកវិទ្យាព័ត៌មាន",
                     "AIE": "វិស្វកម្មបញ្ញាសិប្បនិម្មិត"}},
}
KHMER_NUMERALS = str.maketrans("0123456789", "០១២៣៤៥៦៧៨៩")
OPTION_RE = re.compile(r"Option (\d+)")
BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")              # Gemma's **bold**
BULLET_RE = re.compile(r"^([ \t]*)[*-][ \t]+", re.M)    # Gemma's "*   item" / "- item" (indented = nested)


def _digits(text: str, language: str) -> str:
    """Khmer answers write numbers in Khmer numerals: ឆ្នាំទី១, ៣ ក្រេឌីត."""
    return text.translate(KHMER_NUMERALS) if language == "Khmer" else text


def _num(value: float, language: str) -> str:
    return _digits(f"{value:g}", language)


def _markdown(text: str, html: bool) -> str:
    """Gemma's light Markdown in already-escaped text: bullets -> "•" (nested "◦"), **bold** -> <b>."""
    text = BULLET_RE.sub(lambda m: "    ◦ " if m[1] else "• ", text)
    return BOLD_RE.sub(r"<b>\1</b>" if html else r"\1", text)
SNIPPET_CHARS = 400
DETAIL_LIMIT = 3                                      # up to this many records are shown in full
TELEGRAM_LIMIT = 4096


def _cite(title: str, anchor: str, url: str = "") -> str:
    return f"{title}, {anchor}" + (f" · {url}" if url else "")


def _course_line(c: dict, t: dict, language: str) -> str:
    line = f"{c['title']} — " + t["credit" if c["credits"] == 1 else "credits"].format(c=_num(c["credits"], language))
    line += t["hours"].format(h=_digits(c["hours"], language)) if c["hours"] else ""
    if c["option"]:
        number = OPTION_RE.fullmatch(c["option"])
        line += " [" + (t["option"].format(n=_num(int(number[1]), language)) if number else c["option"]) + "]"
    return line + (t["review"] if c["status"] == "review_needed" else "")


def render(result: dict, html: bool = False) -> str:
    language = result.get("language", "English")
    t = TEXT[language]
    e = (lambda s: escape(str(s), quote=False)) if html else str
    b = (lambda s: f"<b>{s}</b>") if html else (lambda s: s)
    i = (lambda s: f"<i>{s}</i>") if html else (lambda s: s)
    term = lambda c: t["term"].format(y=_num(c["year"], language), s=_num(c["semester"], language))  # noqa: E731
    out: list[str] = []

    if result["mode"] == "record":
        sections = result.get("sections") or [{"heading": result["heading"], "courses": result["courses"]}]
        if len(sections) > 1:                         # no program named: say so, then one section each
            names = t["and"].join(s["program"] for s in sections)
            out += [e(t["both"].format(programs=names)), ""]
        for section in sections:
            courses = section["courses"]
            out += [b(e(section["heading"])), ""]
            if len(courses) <= DETAIL_LIMIT:
                for c in courses:
                    out.append(b(e(c["title"])))
                    out.append(e(term(c) + " · " + _course_line(c, t, language).split(" — ", 1)[1]))
                    if c["notes"]:
                        out.append(i(e(t["note"].format(n=c["notes"]))))
                    out.append("")
            else:
                shown = None
                for c in courses:
                    if (c["year"], c["semester"]) != shown:
                        out += ([""] if shown else []) + [b(e(term(c)))]
                        shown = (c["year"], c["semester"])
                    out.append(e(f"• {_course_line(c, t, language)}"))
                if not any(c["option"] for c in courses):  # options are alternatives, so a sum would mislead
                    total = sum(x["credits"] for x in courses)
                    out += ["", e(t["total"].format(n=_num(len(courses), language), c=_num(total, language)))]
                out.append("")
            first = courses[0]                        # each program's records cite that program's page
            out += [i(e(t["source"].format(title=first["source_title"], url=first["source_url"],
                                           date=_digits(first["retrieved"], language)))), ""]
        if len(sections) > 1:
            out.append(i(e(t["other"])))

    elif result["mode"] == "abstain":
        out.append(e(t["abstain"] + (t["hint"] if result.get("collection") == "official" else "")))
        if result["passages"]:
            out += ["", i(e(t["closest"]))]
            out += [e(f"· {_cite(p['title'], p['anchor'])} — {p['section']}") for p in result["passages"]]

    else:
        if result["text"]:
            out += [_markdown(e(result["text"]), html), "", b(e(t["sources"]))]
        for p in result["passages"]:
            out.append(b(e(f"[{p['n']}] {_cite(p['title'], p['anchor'])}")) + e(f" — {p['section']}"))
            if p["url"]:
                out.append(e(p["url"]))
            if not result["text"]:
                snippet = p["body"] if len(p["body"]) <= SNIPPET_CHARS else p["body"][:SNIPPET_CHARS] + "…"
                out += [e(f"“{snippet}”"), ""]

    while out and not out[-1]:
        out.pop()
    footer = f"route: {result['route']} · mode: {result['mode']}"
    if result["top_score"] is not None:
        footer += f" · score {result['top_score']}"
    return "\n".join(out + ["", i(e(footer))])


def paginate(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on blank lines so a message never breaks mid-paragraph or mid-tag."""
    pages, buf = [], ""
    for para in text.split("\n\n"):
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            pages.append(buf)
        buf = para[:limit]
    return pages + [buf] if buf else pages


# --- 4. terminal chat --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the assistant from the terminal.")
    parser.add_argument("question", nargs="*")
    parser.add_argument("--collection", choices=list(config.COLLECTIONS), default="official")
    parser.add_argument("--extractive", action="store_true", help="quote passages only, do not call Gemma")
    parser.add_argument("--language", choices=LANGUAGES, help="reply language (default: the question's)")
    args = parser.parse_args()
    kb = KB(args.collection, enable_llm=config.ENABLE_LLM and not args.extractive)

    if args.question:
        print(render(answer(" ".join(args.question), kb, language=args.language)))
        return
    print(f"Ask about the {args.collection} sources. Empty line to quit.")
    try:
        while question := input("\n> ").strip():
            print(render(answer(question, kb, language=args.language)))
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
