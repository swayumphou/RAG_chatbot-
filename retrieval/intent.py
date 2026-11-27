"""Understand a query: clean the text, then decide what the user wants.

Five intents (Architecture §9.3):

    TOPIC_SEARCH   "khmer ocr"                 -> hybrid search
    SENIOR_LOOKUP  "Sok Dara"                  -> find one senior
    PORTFOLIO      "projects by Sok Dara"      -> that senior's projects
    FACET_BROWSE   "DSE 2024"                  -> list projects by filter
    ANSWER         "who built a Khmer OCR app" -> search, then answer

When we are not sure, we choose TOPIC_SEARCH. A wrong topic search still shows
useful projects. A wrong name lookup shows a portfolio of the wrong person,
which is the worst mistake this product can make.
"""

from __future__ import annotations

import re
import unicodedata
import warnings
from dataclasses import dataclass

PROGRAMS = ("DSE", "ITE", "TEE", "BE", "FTCE")

YEAR_RE = re.compile(r"\b(20\d\d)\b")
PROGRAM_RE = re.compile(r"\b(DSE|ITE|TEE|BE|FTCE)\b", re.IGNORECASE)
QUESTION_RE = re.compile(
    r"^(who|what|which|when|where|why|has anyone|is there|are there)\b", re.IGNORECASE
)
PORTFOLIO_RE = re.compile(r"\b(portfolio|projects? by|projects? of|works of)\b", re.IGNORECASE)

# If a short query contains one of these words it is a topic, not a name.
TOPIC_WORDS = {"project", "projects", "thesis", "system", "using", "with"}

# Words removed around a name: "show me the portfolio of Sok Dara".
NAME_FILLER = {"show", "me", "the", "a", "open", "see", "please", "of", "by", "for"}

KHMER_DIGITS = str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789")


@dataclass(frozen=True)
class RoutedQuery:
    raw: str  # what the user typed
    normalized: str  # cleaned text, used for names and routing
    lexical: str  # text sent to the search model (Khmer words split)
    intent: str
    years: tuple[int, ...] = ()
    programs: tuple[str, ...] = ()
    name_span: str | None = None  # the name part, for SENIOR_LOOKUP / PORTFOLIO
    topic_span: str | None = None  # the topic part, for searches


# ---------------------------------------------------------------- cleaning


def contains_khmer(text: str) -> bool:
    """True if the text has any Khmer letter (Unicode block U+1780 to U+17FF)."""
    return any("ក" <= ch <= "៿" for ch in text)


def is_khmer_char(ch: str) -> bool:
    return "ក" <= ch <= "៿" or "᧠" <= ch <= "᧿"


def normalize(text: str) -> str:
    """Clean text so the same name always looks the same.

    "  Sók  Dara " -> "sok dara"
    "Dara's"      -> "dara"
    "សុខ ដារា"    -> "សុខ ដារា"   (Khmer is kept as it is)
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.translate(KHMER_DIGITS)
    text = text.replace("​", " ")  # zero-width space used in Khmer text
    text = re.sub(r"(\w)['’]s\b", r"\1", text)  # possessive 's

    # Split letters from their accents (NFD), then drop Latin accents only.
    # Khmer vowel signs are also "combining marks", so we keep those.
    cleaned = []
    for ch in unicodedata.normalize("NFD", text):
        kind = unicodedata.category(ch)
        if kind == "Mn" and not is_khmer_char(ch):
            continue  # a Latin accent such as the one on "ó"
        if kind.startswith("P"):
            cleaned.append(" ")  # punctuation becomes a space (also Khmer ។ ៕ ៖)
            continue
        cleaned.append(ch)
    text = unicodedata.normalize("NFC", "".join(cleaned))
    return " ".join(text.split())


def segment_for_lexical(text: str) -> str:
    """Put spaces between Khmer words for the keyword (sparse) search.

    Khmer is written without spaces, so the keyword search cannot see word
    boundaries. English text is returned unchanged.
    """
    if not text or not contains_khmer(text):
        return text
    try:
        from khmernltk import word_tokenize
    except ImportError:
        warnings.warn(
            "khmer-nltk is not installed; using character trigrams for Khmer text. "
            "Khmer keyword search will be weaker.",
            RuntimeWarning,
            stacklevel=2,
        )
        return _khmer_trigrams(text)

    pieces = []
    for run in re.split(r"([ក-៿]+)", text):
        if contains_khmer(run):
            pieces.extend(word.strip() for word in word_tokenize(run))
        else:
            pieces.append(run)
    return " ".join(" ".join(pieces).split())


def _khmer_trigrams(text: str) -> str:
    """Fallback when khmer-nltk is missing: cut Khmer runs into 3-letter pieces."""

    def trigrams(match: re.Match) -> str:
        run = match.group(0)
        if len(run) <= 3:
            return f" {run} "
        return " " + " ".join(run[i : i + 3] for i in range(len(run) - 2)) + " "

    return " ".join(re.sub(r"[ក-៿]+", trigrams, text).split())


# ---------------------------------------------------------------- entities


def extract_years(normalized: str) -> tuple[int, ...]:
    """All 20xx years in the query, without duplicates."""
    years = []
    for year in YEAR_RE.findall(normalized):
        if int(year) not in years:
            years.append(int(year))
    return tuple(years)


def extract_programs(text: str) -> tuple[str, ...]:
    """Program codes (DSE, ITE, TEE, BE, FTCE) in the query, in capitals, without duplicates."""
    programs = []
    for code in PROGRAM_RE.findall(text or ""):
        if code.upper() not in programs:
            programs.append(code.upper())
    return tuple(programs)


# ---------------------------------------------------------------- routing


def route(query: str, db) -> RoutedQuery:
    """Decide the intent of one query.

    Rules are checked in this order (task T3.2):
      1. the whole query is a known name or surname   -> SENIOR_LOOKUP
      2. "portfolio", "projects by", "works of"        -> PORTFOLIO
      3. starts with a question word                   -> ANSWER
      4. 2-4 words, no topic word, has a known surname -> SENIOR_LOOKUP
      5. a year or program code, and at most 3 words   -> FACET_BROWSE
      6. anything else                                 -> TOPIC_SEARCH
    """
    raw = query or ""
    norm = normalize(raw)
    words = norm.split()
    years = extract_years(norm)
    programs = extract_programs(norm)

    def make(intent, name_span=None, topic_span=None):
        return RoutedQuery(
            raw=raw,
            normalized=norm,
            lexical=segment_for_lexical(raw.strip()),
            intent=intent,
            years=years,
            programs=programs,
            name_span=name_span,
            topic_span=topic_span,
        )

    if not norm:
        return make("TOPIC_SEARCH")

    known_names = db.known_names()
    known_surnames = db.known_surnames()

    # Rule 1
    if norm in known_names or norm in known_surnames:
        return make("SENIOR_LOOKUP", name_span=norm)

    # Rule 2: the name is what is left after removing the portfolio phrase.
    if PORTFOLIO_RE.search(norm):
        rest = PORTFOLIO_RE.sub(" ", norm).split()
        name = " ".join(w for w in rest if w not in NAME_FILLER)
        return make("PORTFOLIO", name_span=name or None)

    # Rule 3
    if QUESTION_RE.search(norm):
        return make("ANSWER", topic_span=norm)

    # Rule 4
    if 2 <= len(words) <= 4 and not set(words) & TOPIC_WORDS and set(words) & known_surnames:
        return make("SENIOR_LOOKUP", name_span=norm)

    # Rule 5
    if (years or programs) and len(words) <= 3:
        return make("FACET_BROWSE")

    # Rule 6: when in doubt, a topic search.
    return make("TOPIC_SEARCH", topic_span=norm)


def decompose_multi_intent(query: str, db) -> tuple[RoutedQuery, RoutedQuery] | None:
    """Split "Sok Dara's OCR project" into a name part and a topic part.

    Returns (SENIOR_LOOKUP("sok dara"), TOPIC_SEARCH("ocr")), or None when the
    query is not "name + topic". We only try this when the query has a
    possessive ('s) or the word project/thesis, as task T3.7 describes.
    """
    raw = query or ""
    norm = normalize(raw)
    words = norm.split()
    has_possessive = re.search(r"\w['’]s\b", raw.casefold()) is not None
    has_project_word = bool({"project", "projects", "thesis"} & set(words))
    if len(words) < 2 or not (has_possessive or has_project_word):
        return None

    # Find the longest run of words that is a known full name (2+ words,
    # or any Khmer name). One Latin word alone is too risky to treat as a name.
    known = db.known_names()
    name_start, name_end = None, None
    for start in range(len(words)):
        for end in range(len(words), start, -1):
            candidate = " ".join(words[start:end])
            long_enough = end - start >= 2 or contains_khmer(candidate)
            if candidate in known and long_enough:
                if name_start is None or end - start > name_end - name_start:
                    name_start, name_end = start, end
                break
    if name_start is None:
        return None

    name = " ".join(words[name_start:name_end])
    leftover = words[:name_start] + words[name_end:]
    filler = NAME_FILLER | {"project", "projects", "thesis", "work", "about", "on", "an"}
    topic = " ".join(w for w in leftover if w not in filler)
    if not topic:
        return None  # "Sok Dara's projects" is just a portfolio request

    years = extract_years(norm)
    programs = extract_programs(norm)
    name_query = RoutedQuery(
        raw=raw,
        normalized=name,
        lexical=name,
        intent="SENIOR_LOOKUP",
        years=years,
        programs=programs,
        name_span=name,
    )
    topic_query = RoutedQuery(
        raw=raw,
        normalized=topic,
        lexical=segment_for_lexical(topic),
        intent="TOPIC_SEARCH",
        years=years,
        programs=programs,
        topic_span=topic,
    )
    return name_query, topic_query
