"""Offline regression tests: no embedding model, no index and no Ollama needed.

    python -m unittest discover -s tests -v
"""
import asyncio
import gc
import json
import sys
import tempfile
import threading
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
warnings.filterwarnings("ignore", category=DeprecationWarning)       # third-party noise on Python 3.15

import pymupdf  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

import answer as answer_module  # noqa: E402
from answer import citations_valid, paginate, render  # noqa: E402
import telegram_bot  # noqa: E402
from curriculum import DB, normalize, route  # noqa: E402
import hybrid  # noqa: E402
from hybrid import OllamaDense, select, sparse_vector, tokenize, write_collection  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402
from ingest import SourceLoader, chunk_pages, course_documents, practice_sources, validate  # noqa: E402

PROGRAMS = {"DSE": "data science and engineering", "ITE": "information technology engineering",
            "AIE": "artificial intelligence engineering"}
TITLES = {"DSE": {"deep learning and application", "artificial intelligence", "project practicum",
                  "introduction to data science and engineering"},
          "ITE": {"project practicum", "fin tech"},
          "AIE": {"large language models", "internship"}}
DSE, ITE = "Data Science and Engineering", "Information Technology Engineering"
UNNAMED = ["ITE", "DSE"]                              # looked up, separately, when no program is named


def go(query: str) -> dict:
    return route(query, PROGRAMS, TITLES, tuple(UNNAMED))


class FakeSearcher:                                   # records what was searched, returns one passage
    def candidates(self, text):
        self.searched = text
        return [Document(page_content="[h]\nbody", metadata={
            "source_id": "library", "source_title": "Library", "anchor": "p.1", "section": "S",
            "page": 1, "cosine": 0.7})]


def candidate(source: str, page: int, cosine: float, course: str | None = None) -> Document:
    return Document(page_content="x", metadata={"source_id": source, "page": page, "cosine": cosine,
                                                "course_id": course})


class Routing(unittest.TestCase):
    def test_normalize_keeps_khmer_and_strips_latin_accents_and_punctuation(self):
        self.assertEqual(normalize("  Café   DATA? "), "cafe data")
        self.assertEqual(normalize("ពិជគណិតលីនេអ៊ែរ"), "ពិជគណិតលីនេអ៊ែរ")

    def test_course_lookup(self):
        r = go("How many credits is Deep Learning and Application?")
        self.assertEqual((r["intent"], r["program"], r["programs"], r["title"]),
                         ("COURSE_LOOKUP", None, UNNAMED, "deep learning and application"))
        self.assertEqual(go("deep lerning and application")["title"], "deep learning and application")   # typo
        r = go("Project Practicum in year 2")                          # a title both ITE and DSE have
        self.assertEqual((r["intent"], r["programs"], r["year"]), ("COURSE_LOOKUP", UNNAMED, 2))
        self.assertEqual(go("Fin Tech")["title"], "fin tech")          # an ITE-only title, no program named
        r = go("Large Language Models in AIE")
        self.assertEqual((r["intent"], r["program"], r["programs"], r["title"]),
                         ("COURSE_LOOKUP", "AIE", ["AIE"], "large language models"))
        self.assertEqual(go("Introduction to Data Science and Engineering")["intent"], "COURSE_LOOKUP")

    def test_a_program_name_is_not_mistaken_for_a_course(self):
        r = go("Artificial Intelligence Engineering courses in year 1")
        self.assertEqual((r["intent"], r["program"], r["title"]), ("CURRICULUM_LIST", "AIE", None))
        self.assertEqual(go("Is there an internship?")["intent"], "QUESTION")   # AIE-only title, AIE not named
        self.assertEqual(go("difference between DSE and AIE courses")["intent"], "QUESTION")   # two programs

    def test_curriculum_list_and_question(self):
        r = go("What courses are in year 1 semester 2?")
        self.assertEqual((r["intent"], r["programs"], r["year"], r["semester"]), ("CURRICULUM_LIST", UNNAMED, 1, 2))
        r = go("subjects in the first year second semester")
        self.assertEqual((r["intent"], r["year"], r["semester"]), ("CURRICULUM_LIST", 1, 2))
        r = go("មុខវិជ្ជាឆ្នាំទី៣")                                       # Khmer: "subjects of year 3"
        self.assertEqual((r["intent"], r["year"]), ("CURRICULUM_LIST", 3))
        self.assertEqual(go("AIE courses")["intent"], "CURRICULUM_LIST")
        self.assertEqual(go("How do I apply for a scholarship?")["intent"], "QUESTION")

    def test_another_universitys_program_does_not_get_dse_records(self):
        self.assertEqual(go("What courses are in year 1 of the law program?")["intent"], "QUESTION")
        self.assertEqual(go("faculty of medicine courses in year 2")["intent"], "QUESTION")
        self.assertEqual(go("courses in year 1 of the program")["intent"], "CURRICULUM_LIST")   # generic word
        self.assertEqual(go("DSE degree courses in year 1")["programs"], ["DSE"])               # ours, named


class Ingestion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_validate_reads_course_records_and_skips_what_is_not_official(self):
        course = {"program_id": "DSE", "program_name": DSE, "academic_year": 1, "semester": 1}
        (self.dir / "curricula.json").write_text(json.dumps({"programs": [{
            "program_id": "DSE", "program_name": DSE, "source_url": "https://x",
            "courses": [{**course, "course_name": "Physics I", "credits": "3.0", "source_hours": "(2-2)"},
                        {**course, "course_name": "Broken", "credits": "many"}]}]}), encoding="utf-8")
        (self.dir / "draft.md").write_text("# Draft\nnot official", encoding="utf-8")
        (self.dir / "unlisted.md").write_text("# Extra", encoding="utf-8")
        registry = self.dir / "sources.csv"
        registry.write_text("source_id,title,publisher,doc_type,file,url,version,published,status\n"
                            "cur,Curricula,RUPP,curriculum,curricula.json,,1,,official\n"
                            "d,Draft,RUPP,faq,draft.md,,1,,draft\n"
                            "gone,Missing,RUPP,web,missing.json,,1,,official\n", encoding="utf-8")
        rep = validate(registry, self.dir)
        self.assertEqual([c["course_id"] for c in rep.courses], ["DSE-Y1S1-01"])
        self.assertEqual(rep.courses[0]["credits"], 3.0)
        self.assertEqual([s["source_id"] for s in rep.sources], ["cur"])
        self.assertTrue(any("credits must be numbers" in e for e in rep.errors))
        self.assertTrue(any("file not found" in e for e in rep.errors))
        self.assertTrue(any("not official" in w for w in rep.warnings))
        self.assertTrue(any("unlisted.md" in w for w in rep.warnings))
        self.assertFalse(any(w.startswith("file 'sources.csv'") for w in rep.warnings))

    def test_practice_sources_take_every_readable_file(self):
        (self.dir / "Lecture 1.pdf").write_bytes(b"%PDF-1.4")
        (self.dir / "Lecture 1.pptx").write_bytes(b"PK")
        (self.dir / "notes.xyz").write_text("ignored", encoding="utf-8")
        found = {s["source_id"]: s["doc_type"] for s in practice_sources(self.dir)}
        self.assertEqual(found, {"lecture_1_pdf": "guide", "lecture_1_pptx": "slides"})

    def test_pdf_sections_come_from_headings_not_running_headers(self):
        doc = pymupdf.open()
        for heading in ("1. Why Simulation?", None, "2. Building a Model"):
            page = doc.new_page()
            page.insert_text((72, 40), "Course Notes", fontsize=11)             # running header
            if heading:
                page.insert_text((72, 90), heading, fontsize=14)
            for n in range(4):
                page.insert_text((72, 130 + 18 * n), f"Body sentence {n} explains the idea in plain words.",
                                 fontsize=11)
        doc.save(self.dir / "notes.pdf")
        doc.close()
        docs = SourceLoader(self.dir / "notes.pdf", {"source_id": "n", "source_title": "Notes",
                                                     "doc_type": "guide"}).load()
        self.assertEqual([(d.metadata["anchor"], d.metadata["section"]) for d in docs],
                         [("p.1", "1. Why Simulation?"), ("p.2", "1. Why Simulation?"),
                          ("p.3", "2. Building a Model")])

    def test_pdf_chunks_end_between_paragraphs_not_inside_one(self):
        first = [f"Paragraph one, line {n}: the simulation model describes demand in plain words." for n in range(5)]
        second = [f"Paragraph two, line {n}: the decision rule compares waste and shortage risk." for n in range(5)]
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100), "\n".join(first), fontsize=11)
        page.insert_text((72, 260), "\n".join(second), fontsize=11)          # a gap -> another text block
        doc.save(self.dir / "two.pdf")
        doc.close()
        pages = SourceLoader(self.dir / "two.pdf", {"source_id": "t", "source_title": "Two",
                                                    "doc_type": "guide"}).load()
        bodies = [c.page_content.split("\n", 1)[1] for c in chunk_pages(pages)]
        self.assertEqual([b.splitlines()[0] for b in bodies], [first[0], second[0]])   # 2nd chunk = 2nd paragraph
        self.assertEqual(bodies[0].splitlines()[-1], first[-1])

    def test_json_loader_and_course_chunks(self):
        page = self.dir / "page.web.json"
        page.write_text(json.dumps({"title": "Library", "url": "https://x", "passages": ["First.", "Second."]}),
                        encoding="utf-8")
        docs = SourceLoader(page, {"source_id": "lib", "source_title": "Library", "doc_type": "web"}).load()
        self.assertEqual([(d.metadata["anchor"], d.page_content) for d in docs],
                         [("passage 1", "First."), ("passage 2", "Second.")])
        course = {"course_id": "DSE-Y3S2-03", "program_id": "DSE", "program_name": DSE, "year": 3, "semester": 2,
                  "title": "Deep Learning and Application", "credits": 3.0, "hours": "(2-2)", "option": "",
                  "source_id": "cur", "source_url": "https://x"}
        chunk = course_documents([course], {"cur": "Curricula"})[0]
        self.assertIn("year 3, semester 2. Credits: 3. Hours: (2-2).", chunk.page_content)
        self.assertEqual(chunk.metadata["course_title"], "Deep Learning and Application")

    def test_chunker_splits_long_pages_and_labels_every_chunk(self):
        meta = {"source_id": "s", "source_title": "Handbook", "doc_type": "handbook", "page": 3,
                "anchor": "p.3", "section": "Rules"}
        text = " ".join(f"Sentence number {i} about the program." for i in range(80))
        chunks = chunk_pages([Document(page_content=text, metadata=meta)])
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.page_content.startswith("[Source: Handbook | Section: Rules | p.3]\n") for c in chunks))
        self.assertTrue(all(len(c.page_content.split("\n", 1)[1]) <= 700 for c in chunks))
        self.assertEqual(len({c.metadata["chunk_id"] for c in chunks}), len(chunks))


class Search(unittest.TestCase):
    def test_tokenize_english_khmer_and_stopwords(self):
        self.assertEqual(tokenize("What courses do I study in Year 1?"), ["course", "study", "year", "1"])
        self.assertEqual(tokenize("what is it"), ["what", "is", "it"])          # only stopwords -> keep them
        self.assertEqual(tokenize("ពិជគណិត")[:2], ["ពិជ", "ិជគ"])                 # Khmer -> character trigrams

    def test_sparse_vector_is_stable_and_query_weights_are_one(self):
        doc, again = sparse_vector("tuition fee tuition", False), sparse_vector("tuition fee tuition", False)
        self.assertEqual((doc.indices, doc.values), (again.indices, again.values))
        self.assertEqual(len(doc.indices), 2)
        self.assertGreater(max(doc.values), min(doc.values))                    # repeated term weighs more
        query = sparse_vector("tuition fees", True)
        self.assertEqual(set(query.indices), set(doc.indices))
        self.assertEqual(set(query.values), {1.0})

    def test_rebuilding_a_collection_drops_the_points_of_removed_chunks(self):
        class FakeDense(OllamaDense):                 # fixed vectors: no Ollama needed
            def embed_documents(self, texts):
                return [[1.0, 0.0, 0.0] for _ in texts]

        docs = [Document(page_content=t, metadata={"chunk_id": t}) for t in ("kept", "removed")]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, warnings.catch_warnings(), \
             mock.patch.object(hybrid, "open_disk", lambda: QdrantClient(path=tmp)):
            warnings.simplefilter("ignore", ResourceWarning)   # qdrant-client leaves the deleted file open
            self.assertEqual(write_collection("t", docs, FakeDense()), 2)
            self.assertEqual(write_collection("t", docs[:1], FakeDense()), 1)   # a deleted source leaves nothing
            gc.collect()                              # let that warning fire here, inside the filter

    def test_select_drops_weak_keyword_hits_and_duplicate_pages(self):
        fused = [candidate("curricula", 0, 0.45, "DSE-Y2S2-02"),               # ranked first by keywords alone
                 candidate("library", 1, 0.60), candidate("library", 1, 0.58), candidate("library", 2, 0.54)]
        kept = select(fused, threshold=0.53)
        self.assertEqual([(d.metadata["source_id"], d.metadata["page"]) for d in kept],
                         [("library", 1), ("library", 2)])
        self.assertEqual(select(fused, threshold=0.90), [])                     # nothing passes -> abstain
        page3 = [candidate("article", 3, 0.65), candidate("other", 1, 0.60), candidate("article", 3, 0.72)]
        kept = select(page3, threshold=0.49)                  # page 3 keeps its place, with its best chunk
        self.assertEqual([(d.metadata["source_id"], d.metadata["cosine"]) for d in kept],
                         [("article", 0.72), ("other", 0.60)])


class Answers(unittest.TestCase):
    def test_citation_validator(self):
        self.assertTrue(citations_valid("Tuition is $600 [1]. Two instalments [2].", 3))
        self.assertFalse(citations_valid("No citations at all.", 3))
        self.assertFalse(citations_valid("Invented source [7].", 3))
        self.assertTrue(citations_valid("Both pages say so [2, 3].", 3))
        self.assertFalse(citations_valid("Mixed [1, 9].", 3))

    def test_render_course_list_flags_review_and_skips_total_for_options(self):
        base = {"year": 4, "semester": 2, "hours": "", "notes": "", "source_title": "Curricula",
                "source_url": "https://x", "retrieved": "2026-11-04"}
        courses = [{**base, "title": f"Course {n}", "credits": 3.0, "option": "", "status": "verified"}
                   for n in range(3)]
        courses.append({**base, "title": "Capstone", "credits": 2.0, "option": "", "status": "review_needed"})
        result = {"route": "CURRICULUM_LIST", "mode": "record", "heading": "AIE", "courses": courses,
                  "passages": [], "text": "", "top_score": None}
        text = render(result)
        self.assertIn("Capstone — 2 credits (record needs review)", text)
        self.assertIn("Total: 4 courses, 11 credits", text)
        courses[0]["option"] = "Option 1"
        self.assertNotIn("Total:", render(result))

    def test_render_escapes_html_and_abstain_hint_is_official_only(self):
        passage = {"n": 1, "title": "A <b>title</b>", "anchor": "p.1", "section": "S & T", "url": "", "body": "x"}
        result = {"route": "QUESTION", "mode": "abstain", "collection": "practice", "passages": [passage],
                  "text": "", "courses": [], "top_score": 0.2}
        page = render(result, html=True)
        self.assertIn("A &lt;b&gt;title&lt;/b&gt;", page)
        self.assertIn("S &amp; T", page)
        self.assertNotIn("what a program teaches", page)
        self.assertIn("what a program teaches", render({**result, "collection": "official"}))

    def test_khmer_question_is_searched_in_english_and_answered_as_asked(self):
        kb = SimpleNamespace(enable_llm=True, collection="official", searcher=FakeSearcher())
        asked = {}
        with mock.patch.object(answer_module, "translate", return_value="What does the library have?"), \
             mock.patch.object(answer_module, "generate",
                               side_effect=lambda q, p, c, lang: asked.update(q=q, lang=lang) or "Books [1]."):
            result = answer_module._grounded("តើបណ្ណាល័យមានអ្វីខ្លះ?", kb, 0.49, "Khmer")
        self.assertEqual(kb.searcher.searched, "What does the library have?")
        self.assertEqual((asked["q"], asked["lang"]), ("តើបណ្ណាល័យមានអ្វីខ្លះ?", "Khmer"))   # Gemma sees Khmer
        self.assertEqual((result["mode"], result["searched"]), ("llm", "What does the library have?"))

        with mock.patch.object(answer_module, "translate", side_effect=RuntimeError("ollama down")):
            kb.enable_llm = False                     # Gemma off -> no translation, Khmer searched as is
            answer_module._grounded("តើបណ្ណាល័យមានអ្វីខ្លះ?", kb, 0.49, "Khmer")
        self.assertEqual(kb.searcher.searched, "តើបណ្ណាល័យមានអ្វីខ្លះ?")

    def test_the_chosen_language_wins_over_the_question_language(self):
        reply = answer_module.reply_language
        self.assertEqual([reply("What is DSE?"), reply("តើ DSE ជាអ្វី?")], ["English", "Khmer"])   # no choice
        self.assertEqual([reply("What is DSE?", "Khmer"), reply("តើ DSE ជាអ្វី?", "English")], ["Khmer", "English"])
        self.assertEqual(reply("What is DSE?", "French"), "English")                   # unknown choice ignored
        kb = SimpleNamespace(enable_llm=True, collection="official", searcher=FakeSearcher())
        with mock.patch.object(answer_module, "translate") as translate, \
             mock.patch.object(answer_module, "generate", return_value="Books [1].") as generate:
            answer_module._grounded("What does the library have?", kb, 0.49, "Khmer")
        translate.assert_not_called()                 # an English question is searched as it is ...
        self.assertEqual(generate.call_args.args[3], "Khmer")       # ... and answered in the chosen language
        with mock.patch.object(answer_module, "translate", return_value="What does the library have?"), \
             mock.patch.object(answer_module, "generate", return_value="Books [1].") as generate:
            answer_module._grounded("តើបណ្ណាល័យមានអ្វីខ្លះ?", kb, 0.49, "English")
        self.assertEqual(generate.call_args.args[0], "What does the library have?")   # English reply: Gemma
        #                                                       gets the translation, not the Khmer question

    def test_render_in_khmer_uses_khmer_words_and_numerals_for_records_and_refusals(self):
        self.assertEqual(answer_module.TEXT["English"].keys(), answer_module.TEXT["Khmer"].keys())
        base = {"year": 1, "semester": 2, "hours": "(2-2)", "notes": "", "source_title": "Curricula",
                "source_url": "https://x", "retrieved": "2026-11-04", "option": "", "status": "verified"}
        courses = [{**base, "title": f"Course {n}", "credits": 3.0} for n in range(4)]
        text = render({"route": "CURRICULUM_LIST", "mode": "record", "heading": "DSE", "courses": courses,
                       "passages": [], "text": "", "top_score": None, "language": "Khmer"})
        self.assertIn("ឆ្នាំទី១ ឆមាសទី២", text)
        self.assertIn("Course 0 — ៣ ក្រេឌីត, ម៉ោង (២-២)", text)
        self.assertIn("សរុប៖ មុខវិជ្ជា ៤ · ១២ ក្រេឌីត", text)
        self.assertNotIn("credits", text)
        courses[0]["option"] = "Option 1"             # an elective group: មុខវិជ្ជាជ្រើសរើស
        self.assertIn("Course 0 — ៣ ក្រេឌីត, ម៉ោង (២-២) [មុខវិជ្ជាជ្រើសរើស ១]",
                      render({"route": "CURRICULUM_LIST", "mode": "record", "heading": "DSE", "courses": courses,
                              "passages": [], "text": "", "top_score": None, "language": "Khmer"}))
        refusal = render({"route": "QUESTION", "mode": "abstain", "collection": "official", "passages": [],
                          "text": "", "courses": [], "top_score": 0.2, "language": "Khmer"})
        self.assertTrue(refusal.startswith("ខ្ញុំរកមិនឃើញ"))

    def test_no_program_named_shows_ite_and_dse_in_separate_sections(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = DB(Path(tmp) / "kb.sqlite3")
            row = {"year": 2, "semester": 1, "title": "Project Practicum", "hours": "(0-4)", "option": "",
                   "status": "verified", "notes": "", "source_id": "cur", "retrieved": "2026-11-04"}
            db.rebuild([{"source_id": "cur", "title": "Curricula", "publisher": "RUPP", "doc_type": "curriculum",
                         "file": "c.json", "version": "1", "sha256": "x"}],
                       [{**row, "course_id": "DSE-1", "program_id": "DSE", "program_name": DSE, "credits": 2.0,
                         "source_url": "https://rupp/dse"},
                        {**row, "course_id": "ITE-1", "program_id": "ITE", "program_name": ITE, "credits": 1.0,
                         "source_url": "https://rupp/ite"}],
                       {"cur": {"pages": 1, "chunks": 1, "status": "ok"}})
            kb = SimpleNamespace(db=db, names={"DSE": DSE, "ITE": ITE})
            result = answer_module._records(go("Project Practicum"), kb, "English")
            self.assertEqual([s["program"] for s in result["sections"]], ["ITE", "DSE"])
            text = render({**result, "language": "English"})
            self.assertIn("You did not name a program, so here are ITE and DSE separately:", text)
            self.assertIn("Year 2, semester 1 · 1 credit, hours (0-4)", text)          # ITE (singular)
            self.assertIn("Year 2, semester 1 · 2 credits, hours (0-4)", text)         # DSE
            self.assertLess(text.index(f"{ITE} (ITE)"), text.index("https://rupp/ite"))   # each section
            self.assertLess(text.index("https://rupp/ite"), text.index(f"{DSE} (DSE)"))   # cites its own
            self.assertLess(text.index(f"{DSE} (DSE)"), text.index("https://rupp/dse"))   # program page
            self.assertIn("AIE courses in year 1", text)                               # how to ask for AIE
            khmer = render({**answer_module._records(go("Project Practicum"), kb, "Khmer"), "language": "Khmer"})
            self.assertIn("វិស្វកម្មបច្ចេកវិទ្យាព័ត៌មាន (ITE)", khmer)
            self.assertIn("វិស្វកម្មវិទ្យាសាស្ត្រទិន្នន័យ (DSE)", khmer)
            named = answer_module._records(go("Project Practicum in DSE"), kb, "English")
            self.assertEqual([s["program"] for s in named["sections"]], ["DSE"])
            self.assertNotIn("separately", render({**named, "language": "English"}))   # named: one program only
            db.con.close()

    def test_a_comparison_searches_each_program_and_takes_the_results_in_turns(self):
        question = "What is the difference between DSE and ITE?"
        self.assertEqual(go(question)["compared"], ["ITE", "DSE"])

        class Searcher:                               # three passages per search, named after it
            asked: list = []

            def candidates(self, text):
                self.asked.append(text)
                tag = "ite" if "(ITE) bachelor" in text else "dse" if "(DSE) bachelor" in text else "q"
                return [Document(page_content="[h]\nbody", metadata={
                    "source_id": f"{tag}_{n}", "source_title": tag, "anchor": "p.1", "section": "S",
                    "page": 1, "cosine": 0.7}) for n in range(3)]

        kb = SimpleNamespace(enable_llm=False, collection="official", searcher=Searcher(),
                             names={"DSE": DSE, "ITE": ITE})
        result = answer_module._grounded(question, kb, 0.49, "English", ["ITE", "DSE"])
        self.assertEqual(kb.searcher.asked, [f"What does the {ITE} (ITE) bachelor program teach?",
                                             f"What does the {DSE} (DSE) bachelor program teach?", question])
        self.assertEqual([p["source_id"] for p in result["passages"]], ["ite_0", "dse_0", "q_0", "ite_1", "dse_1"])

    def test_gemma_markdown_becomes_bold_and_bullets(self):
        result = {"route": "QUESTION", "mode": "llm", "passages": [], "courses": [], "top_score": 0.6,
                  "text": "**DSE** covers:\n*   statistics [1]\n    *   Statistics for Engineers [2]\n- machine learning [1]"}
        self.assertIn("<b>DSE</b> covers:\n• statistics [1]\n    ◦ Statistics for Engineers [2]\n• machine learning [1]",
                      render(result, html=True))
        self.assertNotIn("**", render(result))

    def test_a_thai_word_in_a_khmer_answer_gets_one_repair_call_else_the_sources_are_quoted(self):
        passage = [{"n": 1, "title": "ITE", "anchor": "p.1", "body": "Internship at the end of year 3."}]
        replies = iter(["និស្សិតទៅฝึกงាន [1]។", "និស្សិតទៅកម្មសិក្សា [1]។"])
        with mock.patch.object(answer_module, "_chat", side_effect=lambda *a, **k: next(replies)) as chat:
            self.assertEqual(answer_module.generate("When is the internship?", passage, "official", "Khmer"),
                             "និស្សិតទៅកម្មសិក្សា [1]។")
        self.assertEqual(chat.call_args.args[0], answer_module.REPAIR)
        with mock.patch.object(answer_module, "_chat", return_value="និស្សិតទៅฝึกงาន [1]។"), \
             self.assertRaises(RuntimeError):            # still Thai after the repair -> _grounded quotes
            answer_module.generate("When is the internship?", passage, "official", "Khmer")

    def test_khmer_answers_use_ruppss_terms_and_numerals_but_keep_other_numbers(self):
        self.assertEqual(answer_module.khmer_style("ឆ្នាំទី 3 សេមីស្ទ័រទី 1 [5]។ ទូរស័ព្ទ +855 23 883 640"),
                         "ឆ្នាំទី៣ ឆមាសទី១ [5]។ ទូរស័ព្ទ +855 23 883 640")
        self.assertEqual(answer_module.khmer_style("នៅ year 3, semester 1 មាន Certificate of Foundation Year"),
                         "នៅ ឆ្នាំទី៣, ឆមាសទី១ មាន Certificate of Foundation Year")
        self.assertEqual(answer_module.khmer_style(f"{DSE} (DSE) គឺជាកម្មវិធី... {DSE} (DSE) [1]"),  # first mention
                         f"វិស្វកម្មវិទ្យាសាស្ត្រទិន្នន័យ ({DSE}, DSE) គឺជាកម្មវិធី... {DSE} (DSE) [1]")

    def test_a_question_naming_one_program_also_searches_that_programs_overview(self):
        class Searcher:
            asked: list = []

            def candidates(self, text):
                self.asked.append(text)
                return [Document(page_content="[h]\nbody", metadata={"source_id": f"s{len(self.asked)}",
                        "source_title": "t", "anchor": "p.1", "section": "S", "page": 1, "cosine": 0.7})]

        kb = SimpleNamespace(enable_llm=False, collection="official", searcher=Searcher(), names={"DSE": DSE})
        answer_module._grounded("what is DSE?", kb, 0.49, "English", ["DSE"])
        self.assertEqual(kb.searcher.asked, [f"What does the {DSE} (DSE) bachelor program teach?", "what is DSE?"])

    def test_suggestions_cover_both_majors_until_one_is_known_and_never_repeat_the_question(self):
        suggest = answer_module.suggest
        self.assertEqual(suggest("English"), ["DSE courses in year 1", "ITE courses in year 1",
                                              "What is the difference between DSE and ITE?"])
        self.assertEqual(suggest("Khmer", None, 1, "មុខវិជ្ជាឆ្នាំទី១"),
                         ["មុខវិជ្ជា DSE ឆ្នាំទី១", "មុខវិជ្ជា ITE ឆ្នាំទី១", "តើ DSE និង ITE ខុសគ្នាដូចម្តេច?"])
        self.assertEqual(suggest("English", "DSE", 1), ["DSE courses in year 2", "What does the DSE program teach?",
                                                        "What is the difference between DSE and ITE?"])
        self.assertNotIn("What is the difference between DSE and ITE?",
                         suggest("English", None, None, "what is the difference between DSE and ITE"))
        every = [q for lang in ("English", "Khmer") for p in (None, "DSE", "ITE", "AIE") for y in (None, 1, 4)
                 for q in suggest(lang, p, y)]
        self.assertTrue(all(len(q) <= 55 for q in every))     # fits a Telegram button without cutting
        self.assertEqual({go(q)["intent"] for q in every if "courses" in q or "មុខវិជ្ជា" in q}, {"CURRICULUM_LIST"})

    def test_a_program_without_records_is_refused_not_answered_with_another_programs(self):
        class Searcher:
            def candidates(self, text):
                raise AssertionError("must not search")   # refused before any search

        kb = SimpleNamespace(collection="official", searcher=Searcher(), names={"DSE": DSE}, titles={},
                             programs={"DSE": "data science and engineering", "ITE": "information technology engineering"},
                             lock=threading.Lock(), db=SimpleNamespace(log_query=lambda **_: None), enable_llm=True)
        result = answer_module.answer("ITE courses in year 1", kb, surface="eval")
        self.assertEqual((result["mode"], result["note"]), ("abstain", "no records for ITE"))
        self.assertEqual(result["suggestions"][0], "DSE courses in year 1")         # only programs with data

    def test_suggestions_offer_only_programs_with_records(self):
        only_dse = answer_module.suggest("English", available=("DSE",))
        self.assertEqual(only_dse, ["DSE courses in year 1", "What does the DSE program teach?",
                                    "What jobs can DSE graduates do?"])
        self.assertFalse(any("ITE" in q or "AIE" in q for q in
                             answer_module.suggest("Khmer", "ITE", 1, "", ("DSE",))))   # ITE asked, no ITE data

    def test_each_users_language_is_remembered(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = DB(Path(tmp) / "kb.sqlite3")
            self.assertIsNone(db.language("u1"))
            db.set_language("u1", "Khmer")
            db.set_language("u1", "English")          # a later choice replaces the first
            db.set_language("u2", "Khmer")
            self.assertEqual((db.language("u1"), db.language("u2")), ("English", "Khmer"))
            db.con.close()

    def test_bot_asks_a_new_user_for_a_language_and_then_answers_the_waiting_question(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = DB(Path(tmp) / "kb.sqlite3")
            sent, answered = [], []
            chat = SimpleNamespace(id=7, send_action=mock.AsyncMock(),
                                   send_message=mock.AsyncMock(side_effect=lambda text, **_: sent.append(text)))
            ctx = SimpleNamespace(bot_data={"kb": SimpleNamespace(db=db)}, user_data={}, args=[])

            def update(text=None, data=None):         # the parts of a Telegram update the handlers read
                button = SimpleNamespace(data=data, answer=mock.AsyncMock(), edit_message_text=mock.AsyncMock())
                return SimpleNamespace(effective_user=SimpleNamespace(id=42), effective_chat=chat,
                                       message=SimpleNamespace(text=text), callback_query=button)

            def fake_answer(query, kb, surface, user_hash, language=None):
                answered.append((query, language))
                return {"query_id": "q", "suggestions": ["s1", "s2", "s3"]}

            with mock.patch.object(telegram_bot, "answer", fake_answer), \
                 mock.patch.object(telegram_bot, "render", lambda result, html, compact=False: "the answer"), \
                 mock.patch.object(telegram_bot, "render_sources", lambda result: ""):
                asyncio.run(telegram_bot.on_text(update("What do I study in year 1?"), ctx))
                self.assertEqual((sent, answered), ([telegram_bot.ASK_LANGUAGE], []))   # asked, not answered
                asyncio.run(telegram_bot.on_language_chosen(update(data="lang:Khmer"), ctx))
                self.assertEqual(answered, [("What do I study in year 1?", "Khmer")])  # the waiting question
                self.assertEqual(sent[-2:], ["the answer", telegram_bot.MORE["Khmer"]])   # then the buttons:
                keyboard = chat.send_message.call_args.kwargs["reply_markup"].keyboard
                self.assertEqual([[b.text for b in row] for row in keyboard],
                                 [["s1"], ["s2"], ["s3"], [telegram_bot.LANGUAGE_BUTTON]])
                asyncio.run(telegram_bot.on_text(update("DSE courses in year 3"), ctx))
                self.assertEqual(answered[-1], ("DSE courses in year 3", "Khmer"))     # no second picker
                self.assertEqual(sent.count(telegram_bot.ASK_LANGUAGE), 1)
                for word in (telegram_bot.LANGUAGE_BUTTON, "Language"):   # tapped or typed: the picker
                    asyncio.run(telegram_bot.on_text(update(word), ctx))
                    self.assertEqual(sent[-1], telegram_bot.ASK_LANGUAGE)
                self.assertEqual(len(answered), 2)                     # ... and never sent to answer()
            self.assertEqual(db.language(telegram_bot._user_hash(42)), "Khmer")        # survives a restart
            db.con.close()

    def test_telegram_answer_is_clean_and_resource_button_opens_and_closes_its_sources(self):
        passages = [{"n": n, "source_id": "s", "title": f"Source {n}", "anchor": f"p.{n}", "section": "Overview",
                     "url": "https://example.edu/dse" if n == 2 else "", "course_id": None, "course_title": None,
                     "score": 0.8, "body": "text"} for n in (1, 2)]
        result = {"query_id": "q1", "route": "QUESTION", "mode": "llm", "language": "English",
                  "collection": "official", "text": "DSE teaches machine learning [2].", "heading": "",
                  "courses": [], "sections": [], "passages": passages, "note": "", "top_score": 0.8,
                  "suggestions": []}
        clean = render(result, html=True, compact=True)
        self.assertEqual(clean, "DSE teaches machine learning [2].")      # no source list, link or footer
        sources = answer_module.render_sources(result)
        self.assertIn('[2] <a href="https://example.edu/dse">Source 2</a>, p.2 — Overview', sources)
        self.assertNotIn("Source 1", sources)                              # only what the answer cites
        self.assertIn("https://example.edu/dse", render(result))           # the terminal keeps the full view

        course = {"title": "Capstone", "credits": 2.0, "hours": "", "option": "", "status": "", "notes": "",
                  "year": 4, "semester": 2, "source_title": "Curricula", "source_url": "https://x",
                  "retrieved": "2026-11-04"}
        records = {"route": "COURSE_LOOKUP", "mode": "record", "heading": "DSE", "courses": [course],
                   "passages": [], "text": "", "top_score": None}
        self.assertNotIn("https://x", render(records, html=True, compact=True))
        self.assertIn('<a href="https://x">Curricula</a> · retrieved 2026-11-04', answer_module.render_sources(records))
        script = {**records, "courses": [{**course, "source_url": "javascript:alert(1)"}]}
        self.assertNotIn("<a", answer_module.render_sources(script))       # only http(s) pages become links

        sent, edits = [], []
        chat = SimpleNamespace(id=8, send_action=mock.AsyncMock(),
                               send_message=mock.AsyncMock(side_effect=lambda text, **kw: sent.append((text, kw))))
        db = SimpleNamespace(language=lambda user: "English", rate=lambda query_id, value: None)
        ctx = SimpleNamespace(bot_data={"kb": SimpleNamespace(db=db)}, user_data={})
        labels = lambda markup: [[b.text for b in row] for row in markup.inline_keyboard]   # noqa: E731

        def tap(handler, data):                       # a press on an inline button under the answer
            button = SimpleNamespace(data=data, answer=mock.AsyncMock(), edit_message_reply_markup=mock.AsyncMock(),
                                     edit_message_text=mock.AsyncMock(side_effect=lambda text, **kw: edits.append((text, kw))))
            asyncio.run(handler(SimpleNamespace(effective_user=SimpleNamespace(id=42), callback_query=button), ctx))
            return button

        with mock.patch.object(telegram_bot, "answer", lambda *args, **kwargs: result):
            asyncio.run(telegram_bot._answer(chat, ctx, "what is DSE?", "u", "English"))
        self.assertEqual(sent[0][0], clean)
        self.assertEqual(labels(sent[0][1]["reply_markup"]), [["📚 Resource"], ["👍", "👎"]])
        tap(telegram_bot.on_resource, "src:q1")                            # open
        self.assertEqual(edits[-1][0], f"{clean}\n\n{sources}")
        self.assertEqual(labels(edits[-1][1]["reply_markup"]), [["🔼 Hide resource"], ["👍", "👎"]])
        rated = tap(telegram_bot.on_rating, "rt:q1:1")                     # rating goes, Resource stays
        self.assertEqual(labels(rated.edit_message_reply_markup.call_args.args[0]), [["🔼 Hide resource"]])
        tap(telegram_bot.on_resource, "src:q1")                            # close
        self.assertEqual(edits[-1][0], clean)
        self.assertEqual(labels(edits[-1][1]["reply_markup"]), [["📚 Resource"]])
        old = tap(telegram_bot.on_resource, "src:from-before-a-restart")
        old.answer.assert_awaited_with(telegram_bot.GONE["English"], show_alert=True)

    def test_thai_and_burmese_lookalikes_are_mapped_to_khmer(self):
        self.assertEqual("វិស្วกម្ម (DSE) [1]၊".translate(answer_module.LOOKALIKES), "វិស្វកម្ម (DSE) [1],")

    def test_paginate_respects_limit(self):
        pages = paginate("\n\n".join("x" * 1000 for _ in range(10)), limit=4096)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(p) <= 4096 for p in pages))


if __name__ == "__main__":
    unittest.main()
