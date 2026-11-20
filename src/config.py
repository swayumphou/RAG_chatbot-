"""Central settings. Every value can be overridden by an environment variable, or by a NAME=value
line in an optional `.env` file in the project folder."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_env_file = ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8-sig").splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _key, _value = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _value.strip())


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


# --- folders -----------------------------------------------------------------------------------
OFFICIAL_DIR = ROOT / "data" / "official"             # official sources + their registry, sources.csv
PRACTICE_DIR = ROOT / "data" / "practice"             # learning material, kept apart from official answers
SOURCES_CSV = OFFICIAL_DIR / "sources.csv"
INDEX_DIR = ROOT / "index"                            # generated: SQLite, Qdrant, ingest report, bot lock
SQLITE_PATH = INDEX_DIR / "kb.sqlite3"
QDRANT_PATH = INDEX_DIR / "qdrant"
EVALUATION_DIR = ROOT / "evaluation"                  # golden_set.csv + the latest evaluation report
WEB_PAGE = ROOT / "web" / "index.html"
COLLECTIONS = {"official": "official_v1", "practice": "practice_v1"}   # name -> Qdrant collection

# --- models served by Ollama (pin explicit tags, never "latest") ------------------------------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")  # not "localhost": +2 s per call on Windows
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3:567m")   # dense embeddings, 1024-d, multilingual
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")  # writes the answers
EMBED_TIMEOUT, LLM_TIMEOUT = 300, 120

# --- chunking --------------------------------------------------------------------------------
CHUNK_SIZE, CHUNK_OVERLAP = 700, 100
SLIDE_CHUNK_SIZE, SLIDE_CHUNK_OVERLAP = 300, 50
MIN_CHUNK_CHARS = 60

# --- retrieval -------------------------------------------------------------------------------
FETCH_K = 40                                          # dense + sparse prefetch, fused by RRF
RRF_K = 60                                            # RRF smoothing constant
TOP_K = 5                                             # passages shown / given to the LLM
ABSTAIN_THRESHOLD = float(os.getenv("ABSTAIN_THRESHOLD", "0.49"))  # min dense cosine per passage.
#   Measured 2026-11-14 on the official corpus (evaluate.py --sweep): it stops clearly off-topic
#   questions only. In-domain questions the corpus cannot answer score as high as answerable ones,
#   so the second check is Gemma's "sources do not answer this" rule - keep ENABLE_LLM on.
FUZZY_TITLE_THRESHOLD = 88                            # similarity (0-100) for course-title lookup
PROGRAM_NAMES = {"DSE": "Data Science and Engineering", "ITE": "Information Technology Engineering",
                 "AIE": "Artificial Intelligence Engineering"}   # recognized in questions, records or not
DEFAULT_PROGRAMS = tuple(p.strip() for p in os.getenv("DEFAULT_PROGRAMS", "ITE,DSE").split(",") if p.strip())
#   a course or year question that names no program is answered for each of these, in separate sections

# --- generation ------------------------------------------------------------------------------
ENABLE_LLM = _flag("ENABLE_LLM", "true")              # false -> quote passages only (no Gemma)
NUM_CTX, NUM_PREDICT = 8192, 600

# --- interfaces ------------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")   # empty -> the bot asks for it at start
KB_SALT = os.getenv("KB_SALT", "change-me")           # salts the hashed Telegram user ids in the log
WEB_PORT = int(os.getenv("WEB_PORT", "8765"))
