"""Exact curriculum records: the rules that route a question to them, and the SQLite store.

Routing (rules first - the intent set is tiny and debuggability matters most):
  COURSE_LOOKUP    a course title in a short question ("credits for Deep Learning and Application")
  CURRICULUM_LIST  a "courses" word + a year, semester or program ("DSE courses in year 3")
  QUESTION         everything else -> hybrid search over the official sources
Records are looked up in the program named in the question. When none is named, they are looked up in
each default program (ITE and DSE) separately and shown in separate sections, never merged into one.
When unsure, route to QUESTION: a search still shows real sources; a wrong record lookup does not.

Store: course answers come from SQLite, never from the vector index, so they are exact.
`source` and `course` are dropped and rebuilt on every ingestion; `query_log` and `user_language`
(each Telegram user's chosen reply language) are never wiped.
"""
import re
import sqlite3
import time
import unicodedata
from difflib import SequenceMatcher

import config

# --- routing -----------------------------------------------------------------------------------

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
YEAR_RE = re.compile(r"\byear\s*([1-4])\b|\b([1-4])(?:st|nd|rd|th)[\s-]+year\b"
                     r"|\b(first|second|third|fourth)[\s-]+year\b|ឆ្នាំទី\s*([1-4១-៤])", re.I)
SEM_RE = re.compile(r"\bsemester\s*([12])\b|\b([12])(?:st|nd)\s+semester\b"
                    r"|\b(first|second)\s+semester\b|ឆមាសទី\s*([12១២])", re.I)
LIST_RE = re.compile(r"\b(courses?|subjects?|classes|curriculum|study plan|schedule|learn|study)\b|មុខវិជ្ជា", re.I)
# "the law program", "faculty of medicine": a program we hold no records for. Without this, such a
# question would get the default program's records ("courses in year 1 of the law program" -> DSE).
OTHER_PROGRAM_RE = re.compile(r"\b(?:faculty|bachelor|school) of ([a-z]+)|\b([a-z]+) (?:program|programme|degree|major)\b")
GENERIC = frozenset("the this that a an my your our their which what each whole study degree bachelor "
                    "bachelors university undergraduate engineering".split())
PUNCT_RE = re.compile(r"[?!.,;:()\[\]\"'“”‘’។៕៖]+")
KHMER_DIGITS = str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789")


def contains_khmer(text: str) -> bool:
    return any("ក" <= ch <= "៿" for ch in text)


def normalize(text: str) -> str:
    """NFKC + casefold + punctuation and whitespace collapse. Latin diacritics are stripped; Khmer is
    preserved (Khmer vowel signs are combining marks, so stripping them would destroy the text)."""
    t = unicodedata.normalize("NFKC", text).casefold()
    if not contains_khmer(t):
        t = "".join(ch for ch in unicodedata.normalize("NFD", t) if unicodedata.category(ch) != "Mn")
    return " ".join(PUNCT_RE.sub(" ", t).split())


def _number(match) -> int | None:
    if not match:
        return None
    value = next(g for g in match.groups() if g).lower()
    return ORDINALS.get(value) or int(value.translate(KHMER_DIGITS))


def route(query: str, programs: dict[str, str], titles: dict[str, set[str]],
          default_programs: tuple[str, ...] = config.DEFAULT_PROGRAMS) -> dict:
    """`programs` maps program id -> normalized program name ("DSE" -> "data science and engineering").
    `titles` maps program id -> the normalized course titles of that program.
    Result: `program` = the program named (None if none), `programs` = the programs to look up."""
    norm = normalize(query)
    result = {"raw": query, "normalized": norm, "intent": "QUESTION", "program": None,
              "programs": list(default_programs), "compared": [], "title": None,
              "year": _number(YEAR_RE.search(query)), "semester": _number(SEM_RE.search(query))}

    rest, named = f" {norm} ", []                      # `rest` = the question without program names
    for pid, name in sorted(programs.items(), key=lambda kv: -len(kv[1])):
        for pattern in (f" {name} ", f" {pid.lower()} "):
            if pattern in rest:
                rest = rest.replace(pattern, "  ")
                named.append(pid)
    named = list(dict.fromkeys(named))
    if len(named) > 1:                                 # comparing programs -> not a single-record question
        result["compared"] = named                     # the search looks for each one (answer._grounded)
        return result
    if named:
        result["program"], result["programs"] = named[0], [named[0]]
    elif any(set(m) - {""} - GENERIC for m in OTHER_PROGRAM_RE.findall(rest)):
        return result                                  # another program, not ours -> search, not records
    own = set().union(*(titles.get(p, set()) for p in result["programs"]))

    if len(norm.split()) <= 10:                        # short question naming a course title
        for title in sorted(own, key=len, reverse=True):
            inside_program_name = any(title in programs[p] for p in named) and f" {title} " not in rest
            if f" {title} " in f" {norm} " and not inside_program_name:
                result["title"] = title
                break
    if not result["title"]:                            # typo in a bare title: "deep lerning and application"
        bare = " ".join(rest.split()) or norm
        score, best = max(((SequenceMatcher(None, bare, t).ratio() * 100, t) for t in own), default=(0, ""))
        if score >= config.FUZZY_TITLE_THRESHOLD:
            result["title"] = best

    if result["title"]:
        result["intent"] = "COURSE_LOOKUP"
    elif LIST_RE.search(query) and (result["year"] or result["semester"] or named):
        result["intent"] = "CURRICULUM_LIST"
    return result


# --- SQLite store ------------------------------------------------------------------------------

REBUILT = """
DROP TABLE IF EXISTS course;
DROP TABLE IF EXISTS source;
CREATE TABLE source (
  source_id TEXT PRIMARY KEY, title TEXT NOT NULL, publisher TEXT, doc_type TEXT, file TEXT,
  url TEXT, version TEXT, published TEXT, sha256 TEXT, pages INTEGER, chunks INTEGER, parse_status TEXT);
CREATE TABLE course (
  course_id TEXT PRIMARY KEY, program_id TEXT NOT NULL, program_name TEXT NOT NULL, year INTEGER NOT NULL,
  semester INTEGER NOT NULL, title TEXT NOT NULL, title_norm TEXT NOT NULL, credits REAL NOT NULL,
  hours TEXT, option TEXT, status TEXT, notes TEXT, source_id TEXT REFERENCES source(source_id),
  source_url TEXT, retrieved TEXT);
"""
KEPT = """
CREATE TABLE IF NOT EXISTS query_log (
  query_id TEXT PRIMARY KEY, surface TEXT, user_hash TEXT, raw_query TEXT, normalized_query TEXT,
  route TEXT, mode TEXT, note TEXT, retrieved_json TEXT, top_score REAL, rating INTEGER,
  latency_ms INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS user_language (
  user_hash TEXT PRIMARY KEY, language TEXT NOT NULL, updated_at TEXT);
"""


class DB:
    def __init__(self, path=config.SQLITE_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(KEPT)

    def rebuild(self, sources: list[dict], courses: list[dict], stats: dict) -> None:
        self.con.executescript(REBUILT)
        with self.con:
            self.con.executemany(
                "INSERT INTO source VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(s["source_id"], s["title"], s["publisher"], s["doc_type"], s["file"], s.get("url", ""),
                  s["version"], s.get("published", ""), s["sha256"], stats[s["source_id"]]["pages"],
                  stats[s["source_id"]]["chunks"], stats[s["source_id"]]["status"]) for s in sources])
            self.con.executemany(
                "INSERT INTO course VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(c["course_id"], c["program_id"], c["program_name"], c["year"], c["semester"], c["title"],
                  normalize(c["title"]), c["credits"], c["hours"], c["option"], c["status"], c["notes"],
                  c["source_id"], c["source_url"], c["retrieved"]) for c in courses])

    def has_courses(self) -> bool:
        found = self.con.execute("SELECT name FROM sqlite_master WHERE name = 'course'").fetchone()
        return bool(found) and self.con.execute("SELECT count(*) FROM course").fetchone()[0] > 0

    def courses(self, program: str | None = None, year: int | None = None, semester: int | None = None,
                title_norm: str | None = None) -> list[dict]:
        filters = {"c.program_id = ?": program, "c.year = ?": year, "c.semester = ?": semester,
                   "c.title_norm = ?": title_norm}
        used = {clause: value for clause, value in filters.items() if value is not None}
        where = ("WHERE " + " AND ".join(used)) if used else ""
        rows = self.con.execute(
            "SELECT c.*, s.title AS source_title FROM course c JOIN source s USING (source_id) "
            f"{where} ORDER BY c.program_id, c.year, c.semester, c.course_id", tuple(used.values()))
        return [dict(r) for r in rows]

    def log_query(self, **row) -> None:
        row["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        cols = ", ".join(row)
        with self.con:
            self.con.execute(f"INSERT INTO query_log ({cols}) VALUES ({', '.join('?' * len(row))})",
                             tuple(row.values()))

    def rate(self, query_id: str, rating: int) -> None:
        with self.con:
            self.con.execute("UPDATE query_log SET rating = ? WHERE query_id = ?", (rating, query_id))

    def language(self, user_hash: str) -> str | None:
        """The reply language a Telegram user chose (None = not chosen yet)."""
        row = self.con.execute("SELECT language FROM user_language WHERE user_hash = ?", (user_hash,)).fetchone()
        return row[0] if row else None

    def set_language(self, user_hash: str, language: str) -> None:
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO user_language VALUES (?, ?, ?)",
                             (user_hash, language, time.strftime("%Y-%m-%dT%H:%M:%S")))
