"""Structured logging, counters, and log-file helpers for RUPP-SPKB v2.0.

The module owns three observability surfaces from the T7.3 card:

* ``logs/bot.log`` - bot updates, errors, throttle events, and dropped messages.
* ``logs/answer_validation.log`` - citation rejections and extractive fallbacks.
* ``logs/ingest_<timestamp>.json`` - one structured ingestion report per run.

It also provides an in-process :class:`Counters` object for the runtime signals
that are cheap to keep in memory (abstention rate, LLM rejection rate, Telegram
429s, failed sends, zero-result queries, and retrieval latency percentiles).

Log records are redacted before they reach a handler: configured secrets,
Telegram bot tokens, email addresses, and Cambodian mobile numbers are replaced.
Callers must still avoid putting raw Telegram IDs, other contact fields, or full
document text into a message.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

DEFAULT_LOG_DIR: Final = Path(__file__).resolve().parent / "logs"
BOT_LOG_NAME: Final = "bot.log"
ANSWER_VALIDATION_LOG_NAME: Final = "answer_validation.log"
INGEST_LOG_PREFIX: Final = "ingest_"

VALIDATION_LOGGER_NAME: Final = "rupp.answer.validation"
STARTUP_LOGGER_NAME: Final = "rupp.startup"

# Architecture section 14 alerts.
ALERT_THRESHOLDS: Final[dict[str, float]] = {
    "retrieval_p95_ms": 2_000.0,
    "abstention_rate": 0.40,
    "llm_rejection_rate": 0.10,
    "telegram_429_last_hour": 5.0,
}

_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Cambodian mobile formats: 012 345 678 and +855 12 345 678.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?855|0)(?:[\s().-]*\d){8,9}(?!\d)")
_MIN_SECRET_LENGTH = 4

_owned_handlers: list[tuple[logging.Logger, logging.Handler]] = []


@dataclass(frozen=True, slots=True)
class LogFiles:
    """Paths created by :func:`configure_logging`."""

    log_dir: Path
    bot: Path
    answer_validation: Path


@dataclass(frozen=True, slots=True)
class CounterSnapshot:
    """Point-in-time view of :class:`Counters`."""

    retrieval_queries: int
    abstentions: int
    llm_attempts: int
    llm_rejections: int
    telegram_429: int
    failed_sends: int
    zero_result_queries: int
    retrieval_p50_ms: float | None
    retrieval_p95_ms: float | None
    abstention_rate: float
    llm_rejection_rate: float
    telegram_429_last_hour: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly snapshot."""

        return {
            "retrieval_queries": self.retrieval_queries,
            "abstentions": self.abstentions,
            "llm_attempts": self.llm_attempts,
            "llm_rejections": self.llm_rejections,
            "telegram_429": self.telegram_429,
            "failed_sends": self.failed_sends,
            "zero_result_queries": self.zero_result_queries,
            "retrieval_p50_ms": self.retrieval_p50_ms,
            "retrieval_p95_ms": self.retrieval_p95_ms,
            "abstention_rate": self.abstention_rate,
            "llm_rejection_rate": self.llm_rejection_rate,
            "telegram_429_last_hour": self.telegram_429_last_hour,
        }


class Counters:
    """Thread-safe in-process counters for the T7.3 runtime signals.

    Percentiles are computed from a bounded rolling sample. The canonical P50 /
    P95 values for evaluation still come from ``query_log``; these counters are
    for live operations and the startup/diagnostic paths.
    """

    def __init__(self, *, max_latency_samples: int = 10_000) -> None:
        if max_latency_samples < 1:
            raise ValueError("max_latency_samples must be at least 1.")
        self._lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=max_latency_samples)
        self._telegram_429_times: deque[float] = deque()
        self._retrieval_queries = 0
        self._abstentions = 0
        self._zero_result_queries = 0
        self._llm_attempts = 0
        self._llm_rejections = 0
        self._telegram_429 = 0
        self._failed_sends = 0

    def record_retrieval(
        self,
        latency_ms: float,
        *,
        abstained: bool = False,
        zero_results: bool = False,
    ) -> None:
        """Record one answered/abstained retrieval and its latency."""

        if not math.isfinite(latency_ms) or latency_ms < 0:
            raise ValueError("latency_ms must be a finite, non-negative number.")
        with self._lock:
            self._retrieval_queries += 1
            self._latencies.append(float(latency_ms))
            if abstained:
                self._abstentions += 1
            if zero_results:
                self._zero_result_queries += 1

    def record_llm_attempt(self, *, rejected: bool = False) -> None:
        """Record an LLM call and whether the answer was rejected."""

        with self._lock:
            self._llm_attempts += 1
            if rejected:
                self._llm_rejections += 1

    def record_telegram_429(self, *, now: float | None = None) -> None:
        """Record a Telegram 429 response."""

        with self._lock:
            self._telegram_429 += 1
            self._telegram_429_times.append(time.monotonic() if now is None else now)

    def record_failed_send(self) -> None:
        """Record a Telegram send that did not succeed."""

        with self._lock:
            self._failed_sends += 1

    def snapshot(self, *, now: float | None = None) -> CounterSnapshot:
        """Return a consistent snapshot and prune old 429 timestamps."""

        cutoff = (time.monotonic() if now is None else now) - 3_600.0
        with self._lock:
            while self._telegram_429_times and self._telegram_429_times[0] < cutoff:
                self._telegram_429_times.popleft()

            latencies = list(self._latencies)
            retrieval_queries = self._retrieval_queries
            abstentions = self._abstentions
            llm_attempts = self._llm_attempts
            llm_rejections = self._llm_rejections
            telegram_429_last_hour = len(self._telegram_429_times)
            return CounterSnapshot(
                retrieval_queries=retrieval_queries,
                abstentions=abstentions,
                llm_attempts=llm_attempts,
                llm_rejections=llm_rejections,
                telegram_429=self._telegram_429,
                failed_sends=self._failed_sends,
                zero_result_queries=self._zero_result_queries,
                retrieval_p50_ms=_percentile(latencies, 0.50),
                retrieval_p95_ms=_percentile(latencies, 0.95),
                abstention_rate=(abstentions / retrieval_queries if retrieval_queries else 0.0),
                llm_rejection_rate=(llm_rejections / llm_attempts if llm_attempts else 0.0),
                telegram_429_last_hour=telegram_429_last_hour,
            )

    def reset(self) -> None:
        """Clear all counters. Intended for tests and long-lived diagnostics."""

        with self._lock:
            self._latencies.clear()
            self._telegram_429_times.clear()
            self._retrieval_queries = 0
            self._abstentions = 0
            self._zero_result_queries = 0
            self._llm_attempts = 0
            self._llm_rejections = 0
            self._telegram_429 = 0
            self._failed_sends = 0


# Shared process-level counters. Tests can construct their own Counters or call
# counters.reset().
counters: Final = Counters()


def evaluate_alerts(
    snapshot: CounterSnapshot,
    thresholds: Mapping[str, float] | None = None,
) -> list[str]:
    """Return human-readable alerts for signals above the Architecture §14 limits."""

    limits = ALERT_THRESHOLDS if thresholds is None else thresholds
    alerts: list[str] = []

    p95 = snapshot.retrieval_p95_ms
    if p95 is not None and p95 > limits["retrieval_p95_ms"]:
        alerts.append(f"retrieval P95 {p95:.0f} ms > {limits['retrieval_p95_ms']:.0f} ms")
    if snapshot.abstention_rate > limits["abstention_rate"]:
        alerts.append(
            f"abstention rate {snapshot.abstention_rate:.0%} > " f"{limits['abstention_rate']:.0%}"
        )
    if snapshot.llm_rejection_rate > limits["llm_rejection_rate"]:
        alerts.append(
            f"LLM rejection rate {snapshot.llm_rejection_rate:.0%} > "
            f"{limits['llm_rejection_rate']:.0%}"
        )
    if snapshot.telegram_429_last_hour > limits["telegram_429_last_hour"]:
        alerts.append(
            f"Telegram 429s {snapshot.telegram_429_last_hour}/hour > "
            f"{limits['telegram_429_last_hour']:.0f}/hour"
        )
    return alerts


def configure_logging(
    *,
    log_dir: str | Path | None = None,
    level: str | int | None = None,
    secrets: Iterable[str | None] = (),
    console: bool = True,
) -> LogFiles:
    """Configure the application loggers and create the log files.

    Args:
        log_dir: Directory for ``bot.log`` and ``answer_validation.log``.
            Defaults to the repository-root ``logs/`` directory.
        level: Log level name or number. Defaults to ``LOG_LEVEL`` or ``INFO``.
        secrets: Values to redact from every record. Bot startup should pass
            ``(config.telegram_bot_token, config.kb_salt)``.
        console: Also write to stderr. Disable for tests and daemon modes.

    Returns:
        The paths of the files that were opened.

    Repeated calls replace only the handlers installed by this module; handlers
    owned by pytest, the host process, or another library are left alone.
    """

    log_dir_path = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
    log_dir_path.mkdir(parents=True, exist_ok=True)

    log_level = _coerce_level(level)
    secret_values = _normalise_secrets(secrets)

    _clear_owned_handlers()

    root = logging.getLogger()
    root.setLevel(log_level)

    json_formatter = JsonFormatter(secret_values)
    console_formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    console_formatter.converter = time.gmtime

    bot_path = log_dir_path / BOT_LOG_NAME
    bot_handler = _build_file_handler(bot_path, log_level, json_formatter, secret_values)
    _attach(root, bot_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(console_formatter)
        console_handler.addFilter(RedactionFilter(secret_values))
        _attach(root, console_handler)

    validation_logger = logging.getLogger(VALIDATION_LOGGER_NAME)
    validation_logger.setLevel(log_level)
    # Keep propagation on so the root handlers also see validation records.
    # The dedicated file still receives every rejection/fallback.
    validation_logger.propagate = True

    validation_path = log_dir_path / ANSWER_VALIDATION_LOG_NAME
    validation_handler = _build_file_handler(
        validation_path, log_level, json_formatter, secret_values
    )
    _attach(validation_logger, validation_handler)

    return LogFiles(
        log_dir=log_dir_path,
        bot=bot_path,
        answer_validation=validation_path,
    )


def get_logger(name: str = "rupp") -> logging.Logger:
    """Return a named logger; handlers come from :func:`configure_logging`."""

    return logging.getLogger(name)


def get_answer_validation_logger() -> logging.Logger:
    """Return the logger whose records go to ``answer_validation.log``."""

    return logging.getLogger(VALIDATION_LOGGER_NAME)


def log_startup(
    *,
    embed_model: str,
    llm_model: str,
    qdrant_version: str,
    collection: str,
    point_count: int,
    enable_llm: bool = False,
    logger: logging.Logger | None = None,
) -> None:
    """Write the T7.3 startup tag line.

    Call this after :func:`configure_logging`; it prints the loaded embedding
    and LLM tags, the Qdrant version, the collection, and the point count.
    """

    payload = {
        "event": "startup",
        "embed_model": embed_model,
        "llm_model": llm_model,
        "enable_llm": enable_llm,
        "qdrant_version": qdrant_version,
        "collection": collection,
        "point_count": point_count,
    }
    message = (
        f"startup embed_model={embed_model} llm_model={llm_model} "
        f"enable_llm={str(enable_llm).lower()} qdrant_version={qdrant_version} "
        f"collection={collection} point_count={point_count}"
    )
    (logger or logging.getLogger(STARTUP_LOGGER_NAME)).info(message, extra={"data": payload})


def log_answer_validation(
    event: str,
    *,
    reason: str,
    project_id: str | None = None,
    page_number: int | None = None,
    query_id: str | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Log a citation rejection or extractive fallback without document text."""

    payload: dict[str, object] = {"event": event, "reason": reason}
    if project_id is not None:
        payload["project_id"] = project_id
    if page_number is not None:
        payload["page_number"] = page_number
    if query_id is not None:
        payload["query_id"] = query_id
    (logger or get_answer_validation_logger()).info(f"{event}: {reason}", extra={"data": payload})


def ingest_report_path(
    log_dir: str | Path | None = None,
    *,
    timestamp: datetime | None = None,
) -> Path:
    """Return ``logs/ingest_<UTC timestamp>.json`` and ensure the directory exists."""

    log_dir_path = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
    log_dir_path.mkdir(parents=True, exist_ok=True)
    stamp = timestamp if timestamp is not None else datetime.now(UTC)
    stamp = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    return log_dir_path / f"{INGEST_LOG_PREFIX}{stamp:%Y%m%dT%H%M%SZ}.json"


def write_ingest_report(
    report: Mapping[str, object],
    *,
    log_dir: str | Path | None = None,
    timestamp: datetime | None = None,
    secrets: Iterable[str | None] = (),
) -> Path:
    """Write one JSON ingestion report and return its path.

    The caller builds the report fields (per-project pages/chunks/status, point
    count, model revision, corpus hash). Secret values are redacted and the file
    is written as UTF-8 so Khmer text stays readable.
    """

    path = ingest_report_path(log_dir, timestamp=timestamp)
    secret_values = _normalise_secrets(secrets)
    payload = _redact_value(dict(report), secret_values)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def redact(text: str, secrets: Iterable[str | None] = ()) -> str:
    """Replace configured secrets, Telegram bot tokens, and emails in ``text``."""

    cleaned = text
    for secret in _normalise_secrets(secrets):
        cleaned = cleaned.replace(secret, "[secret]")
    cleaned = _TELEGRAM_TOKEN_RE.sub("[telegram-token]", cleaned)
    cleaned = _EMAIL_RE.sub("[email]", cleaned)
    return _PHONE_RE.sub("[phone]", cleaned)


class RedactionFilter(logging.Filter):
    """Redact a record's message, arguments, and structured ``data`` mapping."""

    def __init__(self, secrets: Iterable[str | None] = ()) -> None:
        super().__init__()
        self._secrets = _normalise_secrets(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self._secrets)
        record.args = ()
        data = getattr(record, "data", None)
        if isinstance(data, Mapping):
            record.data = _redact_value(dict(data), self._secrets)
        return True


class JsonFormatter(logging.Formatter):
    """Render one JSON object per line with UTF-8 text and redacted values."""

    def __init__(self, secrets: Iterable[str | None] = ()) -> None:
        super().__init__()
        self._secrets = _normalise_secrets(secrets)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {}
        data = getattr(record, "data", None)
        if isinstance(data, Mapping):
            payload.update(_redact_value(dict(data), self._secrets))

        payload.update(
            {
                "ts": datetime.fromtimestamp(record.created, tz=UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "level": record.levelname,
                "logger": record.name,
                "message": redact(record.getMessage(), self._secrets),
            }
        )
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info), self._secrets)
        if record.stack_info:
            payload["stack"] = redact(record.stack_info, self._secrets)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _attach(logger: logging.Logger, handler: logging.Handler) -> None:
    logger.addHandler(handler)
    _owned_handlers.append((logger, handler))


def _clear_owned_handlers() -> None:
    for logger, handler in _owned_handlers:
        logger.removeHandler(handler)
        handler.close()
    _owned_handlers.clear()


def _build_file_handler(
    path: Path,
    level: int,
    formatter: logging.Formatter,
    secrets: Iterable[str],
) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(RedactionFilter(secrets))
    return handler


def _normalise_secrets(secrets: Iterable[str | None]) -> tuple[str, ...]:
    if isinstance(secrets, str):
        secrets = (secrets,)
    unique: list[str] = []
    for secret in secrets:
        if not secret or len(secret) < _MIN_SECRET_LENGTH:
            continue
        if secret not in unique:
            unique.append(secret)
    return tuple(unique)


def _redact_value(value: object, secrets: tuple[str, ...]) -> object:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, Mapping):
        return {str(key): _redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secrets) for item in value]
    return value


def _coerce_level(level: str | int | None) -> int:
    raw: str | int = level if level is not None else os.environ.get("LOG_LEVEL", "INFO")
    if isinstance(raw, int):
        return raw

    name = raw.strip().upper()
    resolved = logging.getLevelNamesMapping().get(name)
    if resolved is None:
        raise ValueError(
            f"Invalid log level {raw!r}; use DEBUG, INFO, WARNING, ERROR, or CRITICAL."
        )
    return resolved


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)
