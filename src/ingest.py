"""Build the indexes: validate -> load -> chunk -> embed -> index.

    python src\\ingest.py                         build both collections
    python src\\ingest.py --collection official   only one (official or practice)
    python src\\ingest.py --check                 validate the official registry only; change nothing

official  files in data/official that are listed in data/official/sources.csv with status=official.
          A source with doc_type=curriculum is a JSON file of exact course records.
practice  every readable file in data/practice (learning material). It gets its own index and is
          never mixed into official answers.
"""
import argparse
import csv
import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

import config
import hybrid
from curriculum import DB

# --- 1. which sources to index -----------------------------------------------------------------

SOURCE_FIELDS = ["source_id", "title", "publisher", "doc_type", "file", "version", "status"]
DOC_TYPES = {"handbook", "guide", "slides", "faq", "web", "curriculum"}
COURSE_KEYS = ["program_id", "program_name", "academic_year", "semester", "course_name", "credits"]


@dataclass
class Report:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    sources: list = field(default_factory=list)   # rows accepted for indexing
    courses: list = field(default_factory=list)   # exact curriculum records

    @property
    def ok(self) -> bool:
        return not self.errors


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def read_courses(source: dict, rep: Report) -> None:
    data = json.loads(Path(source["path"]).read_text(encoding="utf-8"))
    for program in data.get("programs", []):
        position = Counter()
        for n, c in enumerate(program.get("courses", []), start=1):
            where = f"{source['file']} {program.get('program_id')} course {n}"
            if missing := [k for k in COURSE_KEYS if c.get(k) in (None, "")]:
                rep.errors.append(f"{where}: missing {missing}")
                continue
            try:
                year, semester, credits = int(c["academic_year"]), int(c["semester"]), float(c["credits"])
            except (TypeError, ValueError):
                rep.errors.append(f"{where}: academic_year/semester/credits must be numbers")
                continue
            position[year, semester] += 1
            rep.courses.append({
                "course_id": f"{c['program_id']}-Y{year}S{semester}-{position[year, semester]:02d}",
                "program_id": c["program_id"], "program_name": c["program_name"],
                "year": year, "semester": semester, "title": c["course_name"].strip(), "credits": credits,
                "hours": c.get("source_hours") or "", "option": c.get("option") or "",
                "status": c.get("verification_status") or "", "notes": " ".join(c.get("review_notes") or []),
                "source_id": source["source_id"],
                "source_url": c.get("source_url") or program.get("source_url") or source.get("url", ""),
                "retrieved": (c.get("retrieved_at") or "")[:10]})


def validate(sources_csv: Path = config.SOURCES_CSV, folder: Path = config.OFFICIAL_DIR) -> Report:
    """Check the official registry. Nothing is indexed unless it passes."""
    rep, seen, rows = Report(), set(), read_csv(sources_csv)
    for line, row in enumerate(rows, start=2):
        where = f"sources.csv line {line}"
        if missing := [c for c in SOURCE_FIELDS if not row.get(c)]:
            rep.errors.append(f"{where}: missing {missing}")
            continue
        sid, path = row["source_id"], Path(folder) / row["file"]
        if sid in seen:
            rep.errors.append(f"{where}: duplicate source_id '{sid}'")
            continue
        seen.add(sid)
        if row["status"] != "official":
            rep.warnings.append(f"{where}: '{sid}' status is '{row['status']}', not official -> skipped")
        elif row["doc_type"] not in DOC_TYPES:
            rep.errors.append(f"{where}: doc_type must be one of {sorted(DOC_TYPES)}")
        elif not path.exists():
            rep.errors.append(f"{where}: file not found: {path}")
        else:
            source = {**row, "path": path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            rep.sources.append(source)
            if row["doc_type"] == "curriculum":
                try:
                    read_courses(source, rep)
                except (json.JSONDecodeError, AttributeError, TypeError) as exc:
                    rep.errors.append(f"{where}: cannot read curriculum records: {exc}")

    listed = {row.get("file") for row in rows} | {Path(sources_csv).name}
    for path in sorted(Path(folder).iterdir()):
        if path.is_file() and path.name not in listed:
            rep.warnings.append(f"file '{path.name}' has no row in sources.csv -> not indexed")
    if not rep.courses:
        rep.warnings.append("no curriculum records found (no official source with doc_type=curriculum)")
    return rep


def practice_sources(folder: Path = config.PRACTICE_DIR) -> list[dict]:
    """Every readable file in data/practice is a source; no registry is needed for learning material."""
    sources = []
    for path in sorted(Path(folder).glob("*")) if Path(folder).exists() else []:
        if path.is_file() and path.suffix.lower() in READERS:
            sources.append({"source_id": re.sub(r"[^a-z0-9]+", "_", path.name.lower()).strip("_"),
                            "title": path.stem, "doc_type": "slides" if path.suffix.lower() == ".pptx" else "guide",
                            "file": path.name, "url": "", "path": path})
    return sources


# --- 2. loading: one Document per PDF page, slide, heading section or JSON passage -------------
# `page` is the number and `anchor` the readable citation ("p.3", "slide 2", "section 4", "passage 1").


class SourceLoader(BaseLoader):
    def __init__(self, path: Path, metadata: dict):
        self.path, self.metadata = Path(path), metadata

    def lazy_load(self) -> Iterator[Document]:
        if self.path.suffix.lower() not in READERS:
            raise ValueError(f"unsupported file type: {self.path.name}")
        reader, label = READERS[self.path.suffix.lower()]
        for page, section, text in reader(self.path):
            if text.strip():                              # never emit an empty Document
                yield Document(page_content=text.strip(), metadata={
                    **self.metadata, "page": page, "anchor": f"{label}{page}", "section": section})


def _wordy(text: str) -> bool:
    return sum(ch.isalpha() for ch in text) >= 3      # a bare page number is not a title


def _first_line(text: str, skip: frozenset = frozenset()) -> str:
    lines = (" ".join(l.split()) for l in text.splitlines())
    return next((l for l in lines if _wordy(l) and l not in skip), "")[:80]


def _pdf(path: Path):
    """The section of a page is its heading: a line set larger than the document's body text that is
    not a running header. A page without a heading continues the previous page's section.
    Paragraphs (PyMuPDF text blocks) are separated by a blank line, so the splitter cuts between
    paragraphs, not at the end of a wrapped line in the middle of a sentence."""
    import pymupdf
    with pymupdf.open(path) as doc:
        pages = [[(round(max(s["size"] for s in line["spans"]), 1), " ".join("".join(s["text"] for s in line["spans"]).split()))
                  for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) if line["spans"]]
                 for page in doc]
        pages = [[(size, text) for size, text in lines if _wordy(text)] for lines in pages]
        body_size = Counter(size for lines in pages for size, _ in lines).most_common(1)[0][0] if any(pages) else 0
        seen = Counter(text for lines in pages for text in {t for _, t in lines})
        running = frozenset(t for t, n in seen.items() if len(pages) >= 3 and n > len(pages) / 2)
        section = ""
        for number, (page, lines) in enumerate(zip(doc, pages), start=1):
            text = "\n\n".join(b[4].strip() for b in page.get_text("blocks") if b[6] == 0)   # 0 = text block
            if len(text.strip()) < 20:                    # scanned page -> OCR if available
                text = _ocr(page)
            headings = [(size, t) for size, t in lines if size > body_size and t not in running]
            section = (max(headings, key=lambda h: h[0])[1][:80] if headings
                       else section or _first_line(text, running))
            yield number, section, text


def _ocr(page) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    pix = page.get_pixmap(dpi=300)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return pytesseract.image_to_string(image, lang="eng+khm")


def _pptx(path: Path):
    from pptx import Presentation
    for number, slide in enumerate(Presentation(path).slides, start=1):
        parts = [s.text_frame.text for s in slide.shapes if s.has_text_frame]
        parts += [" | ".join(c.text for c in row.cells)
                  for s in slide.shapes if s.has_table for row in s.table.rows]
        if slide.has_notes_slide:
            parts.append(slide.notes_slide.notes_text_frame.text)
        title = slide.shapes.title.text if slide.shapes.title is not None else ""
        yield number, title or _first_line(parts[0] if parts else ""), "\n".join(parts)


def _docx(path: Path):
    import docx
    from docx.table import Table
    number, heading, lines = 0, "", []
    for block in docx.Document(path).iter_inner_content():
        if isinstance(block, Table):
            lines += [" | ".join(c.text.strip() for c in row.cells) for row in block.rows]
        elif block.style.name.lower().startswith(("heading", "title")):
            if lines:
                yield number, heading, "\n".join(lines)
            number, heading, lines = number + 1, block.text.strip(), [block.text.strip()]
        elif block.text.strip():
            lines.append(block.text.strip())
    if lines:
        yield number, heading, "\n".join(lines)


def _markdown(path: Path):
    number, heading, lines = 0, "", []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            if lines:
                yield number, heading, "\n".join(lines)
            number, heading, lines = number + 1, line.lstrip("# ").strip(), [line.lstrip("# ").strip()]
        elif line.strip():
            lines.append(line.strip())
    if lines:
        yield number, heading, "\n".join(lines)


def _json(path: Path):
    """Two shapes: a web page ({title, url, passages[]}) or the curriculum file ({programs[]}).
    From the curriculum file only overview text is read here; its courses are indexed as exact records."""
    data = json.loads(path.read_text(encoding="utf-8"))
    units = [(data.get("title", ""), p) for p in data.get("passages", [])]
    if "programs" in data:
        for who, c in ((data.get("department"), data.get("contact")),
                       ("Royal University of Phnom Penh", data.get("university_contact"))):
            if who and c:
                units.append((f"{who} contact", f"{who} contact details. Address: {c.get('address')}. "
                                                f"Phone: {c.get('phone')}. Email: {c.get('email')}."))
        for p in data["programs"]:
            name = f"{p['program_name']} ({p['program_id']})"
            units.append((f"{p['program_id']} program overview",
                          f"{name} bachelor program, {p.get('department', '')}. {p.get('description', '')}"))
            for year in sorted({c["academic_year"] for c in p["courses"]}):
                lines = [f"{name} courses in year {year}."]
                for sem in sorted({c["semester"] for c in p["courses"] if c["academic_year"] == year}):
                    names = [c["course_name"] for c in p["courses"]
                             if (c["academic_year"], c["semester"]) == (year, sem)]
                    lines.append(f"Semester {sem}: " + "; ".join(names) + ".")
                units.append((f"{p['program_id']} year {year} study plan", "\n".join(lines)))
    for number, (section, text) in enumerate(units, start=1):
        yield number, section, text


READERS = {".pdf": (_pdf, "p."), ".pptx": (_pptx, "slide "), ".docx": (_docx, "section "),
           ".md": (_markdown, "section "), ".txt": (_markdown, "section "), ".json": (_json, "passage ")}

# --- 3. chunking: every chunk starts with a breadcrumb, so it is citable on its own ------------
#     [Source: <title> | Section: <section> | <anchor>]

SEPARATORS = ["\n## ", "\n### ", "\n\n", "\n", ". ", " "]
BODY = RecursiveCharacterTextSplitter(chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP,
                                      separators=SEPARATORS)
SLIDES = RecursiveCharacterTextSplitter(chunk_size=config.SLIDE_CHUNK_SIZE,
                                        chunk_overlap=config.SLIDE_CHUNK_OVERLAP, separators=SEPARATORS)


def breadcrumb(meta: dict) -> str:
    return f"[Source: {meta['source_title']} | Section: {meta['section']} | {meta['anchor']}]"


def chunk_pages(pages: list[Document]) -> list[Document]:
    chunks = []
    for page in pages:
        meta = page.metadata
        splitter = SLIDES if meta["doc_type"] == "slides" else BODY
        for i, body in enumerate(splitter.split_text(page.page_content)):
            if len(body.strip()) < config.MIN_CHUNK_CHARS:
                continue                                  # headers, footers, page numbers
            chunk_meta = {**meta, "chunk_id": f"{meta['source_id']}::p{meta['page']}::c{i}"}
            chunks.append(Document(page_content=f"{breadcrumb(chunk_meta)}\n{body.strip()}",
                                   metadata=chunk_meta))
    return chunks


def course_documents(courses: list[dict], source_titles: dict) -> list[Document]:
    """One atomic chunk per course record, so a record is never split."""
    docs = []
    for c in courses:
        meta = {"source_id": c["source_id"], "source_title": source_titles[c["source_id"]],
                "doc_type": "course_record", "url": c["source_url"], "page": 0,
                "anchor": f"year {c['year']}, semester {c['semester']}",
                "section": f"{c['program_id']} course: {c['title']}", "course_id": c["course_id"],
                "course_title": c["title"], "program_id": c["program_id"], "year": c["year"],
                "semester": c["semester"], "chunk_id": f"course::{c['course_id']}"}
        body = (f"{c['title']} is a course in the {c['program_name']} ({c['program_id']}) bachelor program, "
                f"year {c['year']}, semester {c['semester']}. Credits: {c['credits']:g}."
                + (f" Hours: {c['hours']}." if c["hours"] else "")
                + (f" {c['option']}." if c["option"] else ""))
        docs.append(Document(page_content=f"{breadcrumb(meta)}\n{body}", metadata=meta))
    return docs


# --- 4. the pipeline ---------------------------------------------------------------------------


def build(collection: str, dense: hybrid.OllamaDense) -> dict:
    """Index one collection and return its report. The old index stays untouched if anything fails."""
    if collection == "official":
        rep = validate()
        for w in rep.warnings:
            print("WARN ", w)
        if not rep.ok:
            for e in rep.errors:
                print("ERROR", e)
            raise SystemExit("official: validation failed - nothing was indexed")
        sources, courses = rep.sources, rep.courses
    else:
        sources, courses = practice_sources(), []

    pages, stats = [], {}
    for src in sources:
        docs = SourceLoader(src["path"], {"source_id": src["source_id"], "source_title": src["title"],
                                          "doc_type": src["doc_type"], "url": src.get("url", "")}).load()
        stats[src["source_id"]] = {"pages": len(docs), "chunks": 0, "status": "ok" if docs else "failed"}
        if not docs:
            print(f"WARN  {src['file']}: no readable text -> excluded")
        pages += docs
    chunks = chunk_pages(pages)
    for c in chunks:
        stats[c.metadata["source_id"]]["chunks"] += 1
    chunks += course_documents(courses, {s["source_id"]: s["title"] for s in sources})
    if not chunks:
        print(f"{collection}: nothing to index - skipped")
        return {"sources": stats, "course_records": 0, "points": 0}

    points = hybrid.write_collection(config.COLLECTIONS[collection], chunks, dense)   # can fail -> goes first
    if collection == "official":
        db = DB()
        db.rebuild(sources, courses, stats)
        db.con.close()
    print(f"{collection}: {len(sources)} sources, {len(courses)} course records, {points} indexed chunks")
    return {"sources": stats, "course_records": len(courses), "points": points}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the search indexes.")
    parser.add_argument("--collection", choices=[*config.COLLECTIONS, "all"], default="all")
    parser.add_argument("--check", action="store_true", help="validate the official registry only")
    args = parser.parse_args()

    if args.check:
        rep = validate()
        for line in [f"WARN  {w}" for w in rep.warnings] + [f"ERROR {e}" for e in rep.errors]:
            print(line)
        print(f"{'OK' if rep.ok else 'FAILED'}: {len(rep.sources)} sources, {len(rep.courses)} course records")
        raise SystemExit(0 if rep.ok else 1)

    started, dense = time.time(), hybrid.OllamaDense()
    names = list(config.COLLECTIONS) if args.collection == "all" else [args.collection]
    report = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "embed_model": config.EMBED_MODEL,
              "collections": {name: build(name, dense) for name in names}}
    report["seconds"] = round(time.time() - started, 1)
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    out = config.INDEX_DIR / "ingest_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"done in {report['seconds']} s - report: {out}")


if __name__ == "__main__":
    main()
