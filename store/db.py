"""SQLite metadata store: seniors, aliases, projects, documents, logs, relay.

The CSV manifest is the source of truth; this database is rebuilt from it by
`ingestion.run`. Portfolios, facet counts, and consent are read from here, so
they keep working even when Qdrant is down.

Simple rules:
- Every SQL statement uses `?` parameters. User text is never pasted into SQL.
- `tech_stack` and `keywords` are stored as JSON lists.
- Contact fields are only returned by `get_senior()`. All other reads return
  public fields only.
- Withdrawn seniors never match a name search.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from retrieval.intent import contains_khmer, normalize

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "kb.sqlite3"
SCHEMA_PATH = Path(__file__).resolve().with_name("schema.sql")

PUBLIC_SENIOR_FIELDS = (
    "senior_id, full_name_latin, full_name_khmer, name_normalized, "
    "program, cohort_year, supervisor_id, status"
)

# Facet name -> project column. Only these names are allowed in SQL.
FACET_COLUMNS = {
    "year": "year",
    "program": "program",
    "supervisor_id": "supervisor_id",
    "domain": "domain",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_bool(value) -> bool:
    """Read true/false from the manifest. 1/0 and yes/no are accepted too."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes", "y"):
        return True
    if text in ("", "0", "false", "no", "n"):
        return False
    raise ValueError(f"not a true/false value: {value!r}")


def split_list(value) -> list[str]:
    """'a; b;c' -> ['a', 'b', 'c']. A list is cleaned and returned."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(";")
    return [str(item).strip() for item in items if str(item).strip()]


def empty_to_none(value):
    text = str(value).strip() if value is not None else ""
    return text or None


def as_list(value) -> list:
    """Facet values may be one value or a list of values."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v not in (None, "")]
    return [value]


class DB:
    def __init__(self, path=DEFAULT_DB_PATH):
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The bot calls the database from worker threads, so one lock guards it.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.lock = threading.RLock()
        self.in_transaction = False

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------ helpers

    @contextmanager
    def transaction(self):
        """Group many writes into one commit. Everything is undone on error."""
        with self.lock:
            self.in_transaction = True
            try:
                yield self
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise
            finally:
                self.in_transaction = False

    def write(self, sql, params=()):
        with self.lock:
            cursor = self.conn.execute(sql, params)
            if not self.in_transaction:
                self.conn.commit()
            return cursor

    def rows(self, sql, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------ seniors

    def upsert_senior(self, row: dict):
        """Insert or update one senior, and rebuild their alias list.

        The Latin and Khmer full names are always added as aliases.
        `created_at` and `telegram_user_hash` are kept on update.
        """
        latin = row["full_name_latin"].strip()
        khmer = empty_to_none(row.get("full_name_khmer"))
        now = utc_now()
        self.write(
            """
            INSERT INTO senior (senior_id, full_name_latin, full_name_khmer, name_normalized,
                program, cohort_year, supervisor_id, contact_email, github, linkedin, website,
                consent_public_contact, consent_relay, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(senior_id) DO UPDATE SET
                full_name_latin = excluded.full_name_latin,
                full_name_khmer = excluded.full_name_khmer,
                name_normalized = excluded.name_normalized,
                program = excluded.program,
                cohort_year = excluded.cohort_year,
                supervisor_id = excluded.supervisor_id,
                contact_email = excluded.contact_email,
                github = excluded.github,
                linkedin = excluded.linkedin,
                website = excluded.website,
                consent_public_contact = excluded.consent_public_contact,
                consent_relay = excluded.consent_relay,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                row["senior_id"],
                latin,
                khmer,
                normalize(latin),
                row["program"],
                int(row["cohort_year"]),
                empty_to_none(row.get("supervisor_id")),
                empty_to_none(row.get("contact_email")),
                empty_to_none(row.get("github")),
                empty_to_none(row.get("linkedin")),
                empty_to_none(row.get("website")),
                int(parse_bool(row.get("consent_public_contact"))),
                int(parse_bool(row.get("consent_relay"))),
                (empty_to_none(row.get("status")) or "active").lower(),
                now,
                now,
            ),
        )
        aliases = [latin, khmer, *split_list(row.get("aliases"))]
        self.write("DELETE FROM senior_alias WHERE senior_id = ?", (row["senior_id"],))
        for alias in {normalize(a) for a in aliases if a}:
            script = "khmer" if contains_khmer(alias) else "latin"
            self.write(
                "INSERT OR IGNORE INTO senior_alias (senior_id, alias_normalized, script) "
                "VALUES (?, ?, ?)",
                (row["senior_id"], alias, script),
            )

    def senior_exists(self, senior_id) -> bool:
        return self.one("SELECT 1 FROM senior WHERE senior_id = ?", (senior_id,)) is not None

    def withdrawn_senior_ids(self) -> set[str]:
        rows = self.rows("SELECT senior_id FROM senior WHERE status = 'withdrawn'")
        return {r["senior_id"] for r in rows}

    def get_senior(self, senior_id) -> dict | None:
        """The full row, including contact fields. Only consent code should use it."""
        return self.one("SELECT * FROM senior WHERE senior_id = ?", (senior_id,))

    def senior_card(self, senior_id) -> dict:
        """Public fields only (no contact data). Used for candidate lists."""
        row = self.one(
            "SELECT senior_id, full_name_latin, full_name_khmer, program, cohort_year, "
            "supervisor_id FROM senior WHERE senior_id = ?",
            (senior_id,),
        )
        return row or {"senior_id": senior_id}

    def all_seniors(self) -> list[dict]:
        """Active seniors, public fields only."""
        return self.rows(
            f"SELECT {PUBLIC_SENIOR_FIELDS} FROM senior WHERE status = 'active' "
            "ORDER BY senior_id"
        )

    def alias_directory(self) -> list[tuple[str, str]]:
        """(senior_id, alias) pairs for active seniors."""
        rows = self.rows(
            "SELECT a.senior_id, a.alias_normalized FROM senior_alias a "
            "JOIN senior s ON s.senior_id = a.senior_id WHERE s.status = 'active'"
        )
        return [(r["senior_id"], r["alias_normalized"]) for r in rows]

    def alias_matches(self, alias) -> list[str]:
        """Every active senior that has exactly this alias."""
        rows = self.rows(
            "SELECT DISTINCT a.senior_id FROM senior_alias a "
            "JOIN senior s ON s.senior_id = a.senior_id "
            "WHERE a.alias_normalized = ? AND s.status = 'active' ORDER BY a.senior_id",
            (normalize(alias),),
        )
        return [r["senior_id"] for r in rows]

    def lookup_alias(self, alias) -> str | None:
        """The one senior with this alias. Two seniors with it -> None (ambiguous)."""
        matches = self.alias_matches(alias)
        return matches[0] if len(matches) == 1 else None

    def known_names(self) -> set[str]:
        """All normalized names and aliases of active seniors."""
        return {alias for _, alias in self.alias_directory()}

    def known_surnames(self) -> set[str]:
        """Family names of active seniors. Khmer names put the family name first."""
        surnames = set()
        for senior in self.all_seniors():
            for name in (senior["full_name_latin"], senior["full_name_khmer"]):
                words = normalize(name or "").split()
                if words:
                    surnames.add(words[0])
        return surnames

    def senior_names(self, senior_ids) -> list[str]:
        """Latin names of active seniors, in the same order as the ids."""
        names = []
        for senior_id in senior_ids:
            row = self.one(
                "SELECT full_name_latin FROM senior WHERE senior_id = ? AND status = 'active'",
                (senior_id,),
            )
            if row:
                names.append(row["full_name_latin"])
        return names

    def set_telegram_user_hash(self, senior_id, user_hash):
        """Link a senior to their (hashed) Telegram account, for the relay."""
        self.write(
            "UPDATE senior SET telegram_user_hash = ?, updated_at = ? WHERE senior_id = ?",
            (user_hash, utc_now(), senior_id),
        )

    def senior_by_telegram_hash(self, user_hash) -> dict | None:
        return self.one(
            "SELECT * FROM senior WHERE telegram_user_hash = ? AND status = 'active'",
            (user_hash,),
        )

    def prune_seniors(self, keep_ids) -> list[str]:
        """Delete seniors that left the manifest. Returns the deleted ids."""
        keep = set(keep_ids)
        old = [r["senior_id"] for r in self.rows("SELECT senior_id FROM senior")]
        removed = [sid for sid in old if sid not in keep]
        for senior_id in removed:
            self.write("DELETE FROM senior_alias WHERE senior_id = ?", (senior_id,))
            self.write("DELETE FROM authorship WHERE senior_id = ?", (senior_id,))
            self.write("DELETE FROM relay_message WHERE senior_id = ?", (senior_id,))
            self.write("DELETE FROM senior WHERE senior_id = ?", (senior_id,))
        return removed

    # ------------------------------------------------------------ projects

    def upsert_project(self, row: dict):
        self.write(
            """
            INSERT INTO project (project_id, title_en, title_khmer, abstract, year, term,
                program, domain, tech_stack, keywords, repo_url, demo_url, supervisor_id,
                permission, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                title_en = excluded.title_en,
                title_khmer = excluded.title_khmer,
                abstract = excluded.abstract,
                year = excluded.year,
                term = excluded.term,
                program = excluded.program,
                domain = excluded.domain,
                tech_stack = excluded.tech_stack,
                keywords = excluded.keywords,
                repo_url = excluded.repo_url,
                demo_url = excluded.demo_url,
                supervisor_id = excluded.supervisor_id,
                permission = excluded.permission
            """,
            (
                row["project_id"],
                row["title_en"].strip(),
                empty_to_none(row.get("title_khmer")),
                (row.get("abstract") or "").strip(),
                int(row["year"]),
                empty_to_none(row.get("term")),
                row["program"],
                empty_to_none(row.get("domain")),
                json.dumps(split_list(row.get("tech_stack")), ensure_ascii=False),
                json.dumps(split_list(row.get("keywords")), ensure_ascii=False),
                empty_to_none(row.get("repo_url")),
                empty_to_none(row.get("demo_url")),
                empty_to_none(row.get("supervisor_id")),
                (row.get("permission") or "pending").strip().lower(),
                utc_now(),
            ),
        )

    def upsert_authorship(self, project_id, senior_id, role=None):
        self.write(
            "INSERT INTO authorship (project_id, senior_id, role) VALUES (?, ?, ?) "
            "ON CONFLICT(project_id, senior_id) DO UPDATE SET role = excluded.role",
            (project_id, senior_id, role),
        )

    def replace_authorship(self, project_id, senior_ids):
        self.write("DELETE FROM authorship WHERE project_id = ?", (project_id,))
        for senior_id in senior_ids:
            self.upsert_authorship(project_id, senior_id)

    def upsert_document(self, row: dict):
        self.write(
            """
            INSERT INTO document (doc_id, project_id, doc_type, file_path, sha256, page_count,
                parser, parse_status, chunk_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                project_id = excluded.project_id,
                doc_type = excluded.doc_type,
                file_path = excluded.file_path,
                sha256 = excluded.sha256,
                page_count = excluded.page_count,
                parser = excluded.parser,
                parse_status = excluded.parse_status,
                chunk_count = excluded.chunk_count
            """,
            (
                row["doc_id"],
                row["project_id"],
                row["doc_type"],
                str(row["file_path"]),
                row["sha256"],
                row.get("page_count"),
                row.get("parser"),
                row["parse_status"],
                int(row.get("chunk_count") or 0),
            ),
        )

    def replace_documents(self, project_id, rows):
        self.write("DELETE FROM document WHERE project_id = ?", (project_id,))
        for row in rows:
            self.upsert_document(row)

    def prune_projects(self, keep_ids) -> list[str]:
        """Delete projects that are no longer eligible. Returns the deleted ids."""
        keep = set(keep_ids)
        old = [r["project_id"] for r in self.rows("SELECT project_id FROM project")]
        removed = [pid for pid in old if pid not in keep]
        for project_id in removed:
            self.write("DELETE FROM document WHERE project_id = ?", (project_id,))
            self.write("DELETE FROM authorship WHERE project_id = ?", (project_id,))
            self.write("DELETE FROM project WHERE project_id = ?", (project_id,))
        return removed

    def _finish_project(self, row: dict) -> dict:
        """Decode JSON lists and add the author ids (in manifest order)."""
        row["tech_stack"] = json.loads(row.get("tech_stack") or "[]")
        row["keywords"] = json.loads(row.get("keywords") or "[]")
        authors = self.rows(
            "SELECT senior_id FROM authorship WHERE project_id = ? ORDER BY rowid",
            (row["project_id"],),
        )
        row["senior_ids"] = [a["senior_id"] for a in authors]
        return row

    PROJECT_SELECT = """
        SELECT p.*,
            (SELECT COUNT(*) FROM document d
              WHERE d.project_id = p.project_id AND d.parse_status != 'failed') AS document_count,
            (SELECT COALESCE(SUM(d.chunk_count), 0) FROM document d
              WHERE d.project_id = p.project_id) AS chunk_count
        FROM project p
    """

    def get_project(self, project_id) -> dict | None:
        row = self.one(self.PROJECT_SELECT + " WHERE p.project_id = ?", (project_id,))
        return self._finish_project(row) if row else None

    def projects_for_senior(self, senior_id) -> list[dict]:
        """All granted projects of one senior, newest first."""
        rows = self.rows(
            self.PROJECT_SELECT
            + " JOIN authorship a ON a.project_id = p.project_id"
            " WHERE a.senior_id = ? AND p.permission = 'granted'"
            " ORDER BY p.year DESC, p.project_id DESC",
            (senior_id,),
        )
        return [self._finish_project(r) for r in rows]

    def documents_for_project(self, project_id) -> list[dict]:
        return self.rows(
            "SELECT * FROM document WHERE project_id = ? ORDER BY doc_id", (project_id,)
        )

    def indexed_chunk_counts(self) -> dict[str, int]:
        """project_id -> number of chunks, for projects that have chunks."""
        rows = self.rows(
            "SELECT project_id, SUM(chunk_count) AS n FROM document "
            "GROUP BY project_id HAVING SUM(chunk_count) > 0"
        )
        return {r["project_id"]: int(r["n"]) for r in rows}

    # ------------------------------------------------------------ facets

    def _facet_where(self, facets) -> tuple[str, list]:
        """Build "WHERE ..." for facet filters. Column names come from FACET_COLUMNS."""
        clauses = ["p.permission = 'granted'"]
        params = []
        for key, value in (facets or {}).items():
            values = as_list(value)
            if not values:
                continue
            marks = ", ".join("?" for _ in values)
            if key == "tech_stack":
                clauses.append(
                    f"EXISTS (SELECT 1 FROM json_each(p.tech_stack) j WHERE j.value IN ({marks}))"
                )
                params.extend(str(v) for v in values)
            elif key in FACET_COLUMNS:
                clauses.append(f"p.{FACET_COLUMNS[key]} IN ({marks})")
                params.extend(int(v) if key == "year" else str(v) for v in values)
            else:
                raise ValueError(f"unknown facet: {key}")
        return " AND ".join(clauses), params

    def list_projects(self, facets=None) -> list[dict]:
        """Granted projects that match the facets, newest first."""
        where, params = self._facet_where(facets)
        rows = self.rows(
            self.PROJECT_SELECT + f" WHERE {where} ORDER BY p.year DESC, p.project_id DESC",
            params,
        )
        return [self._finish_project(r) for r in rows]

    def facet_counts(self, facets=None) -> dict[str, int]:
        """Counts over the whole corpus with GROUP BY.

        Returns {"total": 12, "program=DSE": 8, "program=ITE": 4, "year=2024": 12, ...}.
        """
        where, params = self._facet_where(facets)
        total = self.one(f"SELECT COUNT(*) AS n FROM project p WHERE {where}", params)
        counts = {"total": total["n"]}
        for column in ("year", "program", "domain", "supervisor_id"):
            rows = self.rows(
                f"SELECT p.{column} AS value, COUNT(*) AS n FROM project p "
                f"WHERE {where} AND p.{column} IS NOT NULL GROUP BY p.{column}",
                params,
            )
            for r in rows:
                counts[f"{column}={r['value']}"] = r["n"]
        rows = self.rows(
            "SELECT j.value AS value, COUNT(DISTINCT p.project_id) AS n "
            f"FROM project p, json_each(p.tech_stack) j WHERE {where} GROUP BY j.value",
            params,
        )
        for r in rows:
            counts[f"tech_stack={r['value']}"] = r["n"]
        return counts

    def manifest_snapshot(self) -> tuple[list[dict], list[dict]]:
        """Current projects and seniors, used to report what changed in a new run."""
        projects = [self._finish_project(r) for r in self.rows("SELECT * FROM project")]
        seniors = self.rows("SELECT * FROM senior")
        for senior in seniors:
            aliases = self.rows(
                "SELECT alias_normalized FROM senior_alias WHERE senior_id = ?",
                (senior["senior_id"],),
            )
            senior["aliases"] = [a["alias_normalized"] for a in aliases]
        return projects, seniors

    # ------------------------------------------------------------ query log

    def log_query(self, **row) -> str:
        """Save one interaction. `tg_user_hash` must already be a salted hash."""
        if "tg_user_id" in row:
            raise ValueError("never log a raw Telegram id; pass tg_user_hash")
        retrieved = row.get("retrieved_json", "[]")
        if not isinstance(retrieved, str):
            retrieved = json.dumps(retrieved, ensure_ascii=False)
        query_id = row.get("query_id") or str(uuid.uuid4())
        self.write(
            """
            INSERT OR REPLACE INTO query_log (query_id, surface, tg_user_hash, tg_message_id,
                raw_query, normalized_query, intent, resolved_senior, retrieved_json,
                clicked_project, rating, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query_id,
                row.get("surface") or "telegram",
                row.get("tg_user_hash"),
                row.get("tg_message_id"),
                row["raw_query"],
                row.get("normalized_query") or "",
                row.get("intent") or "",
                row.get("resolved_senior"),
                retrieved,
                row.get("clicked_project"),
                row.get("rating"),
                row.get("latency_ms"),
                row.get("created_at") or utc_now(),
            ),
        )
        return query_id

    def rate(self, query_id, rating) -> bool:
        if int(rating) not in (-1, 1):
            raise ValueError("rating must be 1 or -1")
        cursor = self.write(
            "UPDATE query_log SET rating = ? WHERE query_id = ?", (int(rating), query_id)
        )
        return cursor.rowcount > 0

    def clicked_project(self, query_id, project_id) -> bool:
        cursor = self.write(
            "UPDATE query_log SET clicked_project = ? WHERE query_id = ?", (project_id, query_id)
        )
        return cursor.rowcount > 0

    def query_log_rows(self) -> list[dict]:
        return self.rows("SELECT * FROM query_log ORDER BY created_at, query_id")

    # ------------------------------------------------------------ relay

    def add_relay_message(self, senior_id, from_hash, body) -> str:
        relay_id = uuid.uuid4().hex[:12]
        self.write(
            "INSERT INTO relay_message (relay_id, senior_id, from_hash, body, status, created_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?)",
            (relay_id, senior_id, from_hash, body, utc_now()),
        )
        return relay_id

    def get_relay(self, relay_id) -> dict | None:
        return self.one("SELECT * FROM relay_message WHERE relay_id = ?", (relay_id,))

    def set_relay_status(self, relay_id, status):
        self.write("UPDATE relay_message SET status = ? WHERE relay_id = ?", (status, relay_id))

    def queued_relays(self, senior_id) -> list[dict]:
        return self.rows(
            "SELECT * FROM relay_message WHERE senior_id = ? AND status = 'queued' "
            "ORDER BY created_at",
            (senior_id,),
        )

    def relay_count_since(self, from_hash, since_iso) -> int:
        row = self.one(
            "SELECT COUNT(*) AS n FROM relay_message WHERE from_hash = ? AND created_at >= ?",
            (from_hash, since_iso),
        )
        return row["n"]
