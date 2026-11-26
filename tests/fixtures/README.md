# Test Fixtures

All fixtures are synthetic and small. Do not commit real student documents, names, emails, phone numbers, student IDs, or grades.

## Layout

```text
tests/fixtures/
├── build_fixtures.py          # generates the binary fixtures
├── manifest/
│   ├── projects.csv           # valid fixture manifest
│   ├── seniors.csv            # valid fixture manifest
│   └── invalid/               # one rejection case per file (T1.5)
├── corpus/
│   ├── dse2024-001/report.pdf
│   ├── dse2024-001/slides.pptx
│   ├── dse2024-002/report.pdf      # scanned / OCR
│   ├── dse2024-003/report.docx
│   ├── dse2024-004/report.md
│   └── dse2024-006/report.pdf      # blank / failed
└── golden/
    └── sample.csv             # small evaluation subset
```

## Regenerate

```powershell
python tests/fixtures/build_fixtures.py
```

The fixture builder uses PyMuPDF, python-docx, python-pptx, and Pillow. Keep every generated file under 100 KB.

## Rules

- Names such as `Sok Dara` and `Chan Sreyneang` are invented.
- URLs in fixtures must not resolve to a real person.
- A fixture that fails to parse is still committed if the test needs it (for example, the blank PDF).
- The golden-set fixture uses the same header as `eval/golden_set.csv`.
