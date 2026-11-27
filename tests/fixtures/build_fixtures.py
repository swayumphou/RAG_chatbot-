"""Make the small fake documents used by the tests (task T1.3).

    python tests/fixtures/build_fixtures.py            # writes tests/fixtures/corpus/
    python tests/fixtures/build_fixtures.py --out DIR  # writes somewhere else

Every text below is invented for testing. Each file is kept under 100 KB.

    dse2024-001/report.pdf    4 pages: abstract, body, results, references, header/footer
    dse2024-001/slides.pptx   3 slides with speaker notes and one table
    dse2024-002/report.pdf    one scanned page (an image, no text layer) -> OCR
    dse2024-003/report.docx   headings and a table
    dse2024-004/report.md     headings, abstract, references, a 20-character paragraph
    dse2024-005/              empty folder (metadata-only project)
    dse2024-006/report.pdf    one blank page -> parse fails
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parent / "corpus"

A4_WIDTH, A4_HEIGHT = 595, 842


# ---------------------------------------------------------------- PDF


def write_text_pdf(path: Path):
    """A born-digital 4-page report with real headings and a header/footer."""
    import pymupdf

    pages = [
        [
            ("title", "Khmer Handwritten Text Recognition with Tesseract"),
            ("heading", "Abstract"),
            (
                "body",
                "Draft abstract from the PDF. This fixture report describes a pipeline that "
                "segments Khmer handwriting and reads it with Tesseract. The manifest abstract "
                "should replace this text in the index.",
            ),
        ],
        [
            ("heading", "1. Introduction"),
            (
                "body",
                "Handwritten Khmer forms are still common in Cambodian offices. Reading them by "
                "hand is slow, so this fixture project studies optical character recognition for "
                "Khmer script. Khmer has many stacked consonants, which makes segmentation harder "
                "than for Latin text.",
            ),
            ("heading", "2. Methodology"),
            (
                "body",
                "Each scanned form is converted to grayscale, cleaned with a median filter, and "
                "split into text lines with horizontal projection profiles. Lines are passed to "
                "Tesseract with a Khmer language model. We compare the default model with a model "
                "fine-tuned on synthetic handwriting samples.",
            ),
        ],
        [
            ("heading", "3. Results"),
            (
                "body",
                "On the synthetic test set the fine-tuned model reached higher character accuracy "
                "than the default model. Most remaining errors come from touching characters and "
                "from vowel signs written far above the base consonant.",
            ),
            ("heading", "4. Conclusion"),
            (
                "body",
                "Simple preprocessing plus a fine-tuned Tesseract model is a practical baseline "
                "for Khmer handwriting recognition in small offices.",
            ),
        ],
        [
            ("heading", "References"),
            (
                "body",
                "[1] Fixture Author A. An invented paper about OCR. Fixture Press, 2020.\n"
                "[2] Fixture Author B. Another invented paper about Khmer script. 2021.",
            ),
        ],
    ]
    sizes = {"title": 20, "heading": 16, "body": 11}

    doc = pymupdf.open()
    for number, blocks in enumerate(pages, start=1):
        page = doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)
        page.insert_text((72, 30), "RUPP Senior Project Fixture", fontsize=8)  # header
        page.insert_text((280, 820), f"Page {number}", fontsize=8)  # footer
        y = 90
        for kind, text in blocks:
            size = sizes[kind]
            if kind == "body":
                rect = pymupdf.Rect(72, y, A4_WIDTH - 72, y + 200)
                page.insert_textbox(rect, text, fontsize=size)
                y += 210
            else:
                page.insert_text((72, y + size), text, fontsize=size)
                y += size + 20
    doc.save(path)
    doc.close()


def write_scanned_pdf(path: Path):
    """One page that is only an image of text, so OCR is needed."""
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont

    lines = [
        "Scanned Exam Sheet Digitization",
        "This page is a picture of printed text.",
        "Optical character recognition must read it",
        "because the PDF has no text layer.",
    ]
    image = Image.new("L", (1700, 700), color=255)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=48)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((60, 60 + i * 130), line, fill=0, font=font)
    png = io.BytesIO()
    image.save(png, format="PNG", optimize=True)

    doc = pymupdf.open()
    page = doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)
    page.insert_image(pymupdf.Rect(20, 60, A4_WIDTH - 20, 60 + 245), stream=png.getvalue())
    doc.save(path, deflate=True)  # compress the image, or the file is > 1 MB
    doc.close()


def write_blank_pdf(path: Path):
    """One empty page: there is no text and nothing for OCR to read."""
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)
    doc.save(path)
    doc.close()


# ---------------------------------------------------------------- DOCX / PPTX / MD


def write_docx(path: Path):
    from docx import Document

    doc = Document()
    doc.add_heading("Campus Library Booking System", level=0)
    doc.add_heading("Abstract", level=1)
    doc.add_paragraph(
        "Draft abstract from the DOCX. This fixture report describes a web system for booking "
        "study rooms in a campus library."
    )
    doc.add_heading("Introduction", level=1)
    doc.add_paragraph(
        "Students often walk across campus only to find every study room taken. This fixture "
        "project lets them reserve a room online and lets librarians approve the bookings."
    )
    doc.add_heading("System Design", level=2)
    doc.add_paragraph("The system has three main tables, shown below.")
    table = doc.add_table(rows=3, cols=3)
    rows = [
        ["Table", "Purpose", "Owner"],
        ["room", "Study rooms and their capacity", "librarian"],
        ["booking", "Who reserved which room and when", "student"],
    ]
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            table.cell(r, c).text = value
    doc.add_heading("References", level=1)
    doc.add_paragraph("[1] Fixture Author C. An invented paper about room booking. 2019.")
    doc.save(path)


def write_pptx(path: Path):
    from pptx import Presentation
    from pptx.util import Inches

    deck = Presentation()

    slide = deck.slides.add_slide(deck.slide_layouts[1])  # title + content
    slide.shapes.title.text = "Khmer Handwriting OCR"
    slide.placeholders[1].text = "Fixture slides: problem, method, and results of the project."
    slide.notes_slide.notes_text_frame.text = "Speaker note: introduce the Khmer OCR problem."

    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Method"
    slide.placeholders[1].text = "Grayscale, median filter, line segmentation, then Tesseract."
    slide.notes_slide.notes_text_frame.text = "Speaker note: explain the preprocessing steps."

    slide = deck.slides.add_slide(deck.slide_layouts[5])  # title only
    slide.shapes.title.text = "Results"
    shape = slide.shapes.add_table(3, 2, Inches(1), Inches(2), Inches(6), Inches(1.5))
    cells = [["Model", "Character accuracy"], ["Default", "lower"], ["Fine-tuned", "higher"]]
    for r, values in enumerate(cells):
        for c, value in enumerate(values):
            shape.table.cell(r, c).text = value
    slide.notes_slide.notes_text_frame.text = "Speaker note: the fine-tuned model is better."
    deck.save(path)


MARKDOWN = """# Khmer News Topic Classification

## Abstract

Draft abstract from the Markdown file. This fixture report classifies Khmer news articles
into topics after splitting the text into words.

## Methods

Articles are cleaned, segmented into words with khmer-nltk, and turned into word count
features. A linear classifier is trained on these features and tested on held-out articles.

## Notes

Short paragraph only

## References

1. Fixture Author D. An invented paper about Khmer text classification. 2022.
"""


# ---------------------------------------------------------------- main


def build_all(out: Path = DEFAULT_OUT) -> Path:
    out = Path(out)
    for number in range(1, 7):
        name = f"dse2024-{number:03d}"
        (out / name).mkdir(parents=True, exist_ok=True)

    write_text_pdf(out / "dse2024-001" / "report.pdf")
    write_pptx(out / "dse2024-001" / "slides.pptx")
    write_scanned_pdf(out / "dse2024-002" / "report.pdf")
    write_docx(out / "dse2024-003" / "report.docx")
    (out / "dse2024-004" / "report.md").write_text(MARKDOWN, encoding="utf-8")
    (out / "dse2024-005" / ".gitkeep").write_text("", encoding="utf-8")  # keeps the empty folder
    write_blank_pdf(out / "dse2024-006" / "report.pdf")
    return out


def main():
    parser = argparse.ArgumentParser(description="Build the synthetic test corpus.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = build_all(args.out)
    for path in sorted(out.rglob("*")):
        if path.is_file():
            size = path.stat().st_size
            warning = "  <-- larger than 100 KB!" if size > 100_000 else ""
            print(f"{path.relative_to(out).as_posix():<28} {size:>7} bytes{warning}")


if __name__ == "__main__":
    main()
