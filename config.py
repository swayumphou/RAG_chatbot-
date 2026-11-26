"""Configuration and fixed contract values for RUPP-SPKB v2.0.

The module reads settings from the process environment only. A sibling ``.env``
file is loaded when it exists, but real environment variables always win and
``.env`` is never written or required.

Secrets are optional while a :class:`Config` is being built so that offline
tooling, tests, and ``--check`` paths can import this module without a token.
The bot entry point must call :func:`get_config` with ``require_secrets=True``
(or :meth:`Config.require_secrets`) so a missing secret fails at startup with a
readable message instead of a traceback.

The constants below are the fixed values from
``implementation/spec/INTERFACES.md`` section 6. They are not environment
variables; every module should import them from here rather than restating
their values.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

# --- Fixed contract values (implementation/spec/INTERFACES.md section 6) ----

COLLECTION: Final = "rupp_senior_projects_v1"

CHUNK_SIZE: Final = 700
CHUNK_OVERLAP: Final = 100
SLIDE_CHUNK_SIZE: Final = 300
SLIDE_CHUNK_OVERLAP: Final = 50
MIN_CHUNK_CHARS: Final = 80

PREFETCH_K: Final = 40
FUSION_LIMIT: Final = 10
TOP_K: Final = 5
RRF_K: Final = 60
FUZZY_NAME_THRESHOLD: Final = 88

NUM_CTX: Final = 8192
NUM_PREDICT: Final = 600
TEMPERATURE: Final = 0.0

# Placeholder only. Phase 6 replaces this with the value chosen by the
# threshold sweep on the negative slice.
ABSTAIN_THRESHOLD: Final = 0.45

MAX_MESSAGE: Final = 4096
MIN_SEND_GAP: Final = 1.0
DEDUPE_SECONDS: Final = 30
STATE_TTL: Final = 600

# --- Environment defaults ---------------------------------------------------

DEFAULT_QDRANT_URL: Final = "http://localhost:6333"
DEFAULT_OLLAMA_URL: Final = "http://127.0.0.1:11434"
DEFAULT_EMBED_MODEL: Final = "BAAI/bge-m3"
DEFAULT_OLLAMA_MODEL: Final = "gemma4:12b-it-qat"

DEFAULT_ENV_FILE: Final = Path(__file__).resolve().with_name(".env")

_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: Final = frozenset({"0", "false", "no", "off"})
_SECRET_FIELDS: Final = frozenset({"telegram_bot_token", "kb_salt"})
_LOG_LEVELS: Final = frozenset(
    {"CRITICAL", "FATAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG", "NOTSET"}
)


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid.

    Messages name the offending environment variable but never contain the
    value of a secret.
    """


@dataclass(frozen=True, slots=True)
class Config:
    """Immutable configuration used by every V2.0 module.

    Secret fields use ``repr=False`` so ``repr(config)``, tracebacks, and log
    formatters that fall back to ``repr`` cannot expose them. Use
    :meth:`safe_dict` when a settings snapshot is needed for logs.
    """

    qdrant_url: str = DEFAULT_QDRANT_URL
    ollama_url: str = DEFAULT_OLLAMA_URL
    embed_model: str = DEFAULT_EMBED_MODEL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    enable_llm: bool = False
    abstain_threshold: float = ABSTAIN_THRESHOLD
    kb_collection: str = COLLECTION
    log_level: str = "INFO"

    telegram_bot_token: str | None = field(default=None, repr=False)
    kb_salt: str | None = field(default=None, repr=False)

    # Fixed values mirrored on the instance for injectable tests and callers
    # that prefer ``config.top_k`` over importing a module constant.
    chunk_size: int = CHUNK_SIZE
    chunk_overlap: int = CHUNK_OVERLAP
    slide_chunk_size: int = SLIDE_CHUNK_SIZE
    slide_chunk_overlap: int = SLIDE_CHUNK_OVERLAP
    min_chunk_chars: int = MIN_CHUNK_CHARS
    prefetch_k: int = PREFETCH_K
    fusion_limit: int = FUSION_LIMIT
    top_k: int = TOP_K
    rrf_k: int = RRF_K
    fuzzy_name_threshold: int = FUZZY_NAME_THRESHOLD
    num_ctx: int = NUM_CTX
    num_predict: int = NUM_PREDICT
    temperature: float = TEMPERATURE
    max_message: int = MAX_MESSAGE
    min_send_gap: float = MIN_SEND_GAP
    dedupe_seconds: int = DEDUPE_SECONDS
    state_ttl: int = STATE_TTL

    def __post_init__(self) -> None:
        _validate_url("QDRANT_URL", self.qdrant_url)
        _validate_url("OLLAMA_URL", self.ollama_url)
        _require_text("EMBED_MODEL", self.embed_model)
        _require_text("OLLAMA_MODEL", self.ollama_model)
        _require_text("KB_COLLECTION", self.kb_collection)
        _require_text("LOG_LEVEL", self.log_level)

        normalized_level = self.log_level.strip().upper()
        if normalized_level not in _LOG_LEVELS:
            raise ConfigError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
        object.__setattr__(self, "log_level", normalized_level)

        if not isinstance(self.enable_llm, bool):
            raise ConfigError("ENABLE_LLM must be a boolean.")
        if isinstance(self.abstain_threshold, bool) or not isinstance(
            self.abstain_threshold, (int, float)
        ):
            raise ConfigError("ABSTAIN_THRESHOLD must be a number between 0 and 1.")
        if not math.isfinite(self.abstain_threshold):
            raise ConfigError("ABSTAIN_THRESHOLD must be a finite number.")
        if not 0.0 <= self.abstain_threshold <= 1.0:
            threshold = self.abstain_threshold
            raise ConfigError(f"ABSTAIN_THRESHOLD must be between 0 and 1 (got {threshold!r}).")

        for name in _SECRET_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ConfigError(f"{name} must be a string value.")
            normalized = value.strip()
            object.__setattr__(self, name, normalized or None)

    def missing_secrets(self) -> tuple[str, ...]:
        """Return the names of required secrets that are absent."""

        missing: list[str] = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.kb_salt:
            missing.append("KB_SALT")
        return tuple(missing)

    def require_secrets(self) -> None:
        """Fail loudly when a production secret is missing.

        This is deliberately separate from construction so unit tests and the
        offline CLI can load configuration without a Telegram token.
        """

        missing = self.missing_secrets()
        if missing:
            names = ", ".join(missing)
            raise ConfigError(
                f"Missing required environment variable(s): {names}. "
                "Set them in the environment or copy .env.example to .env and "
                "fill in the placeholders."
            )

    def safe_dict(self) -> dict[str, object]:
        """Return all settings with secret values masked, for logs/diagnostics."""

        snapshot: dict[str, object] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name in _SECRET_FIELDS:
                snapshot[item.name] = "***" if value else None
            else:
                snapshot[item.name] = value
        return snapshot

    def startup_fields(self) -> dict[str, object]:
        """Return config-sourced fields for ``logging_config.log_startup``.

        The caller adds the runtime-only ``qdrant_version`` and ``point_count``
        arguments. No secret is ever included.
        """

        return {
            "embed_model": self.embed_model,
            "llm_model": self.ollama_model,
            "enable_llm": self.enable_llm,
            "collection": self.kb_collection,
        }


def load_config(
    *,
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    require_secrets: bool = False,
) -> Config:
    """Build a :class:`Config` from the environment.

    Args:
        env_file: Optional ``.env`` path. When ``environ`` is ``None`` and this
            is omitted, the repo-root ``.env`` is read if present. Passing
            ``environ`` makes the load deterministic and skips ``.env`` unless
            ``env_file`` is also given.
        environ: Mapping to read instead of ``os.environ`` (used by tests).
        require_secrets: Raise :class:`ConfigError` when ``TELEGRAM_BOT_TOKEN``
            or ``KB_SALT`` is absent.

    Raises:
        ConfigError: A value is malformed, out of range, or a required secret
            is missing. Messages never include secret values.
    """

    if environ is None:
        file_values = _read_env_file(Path(env_file) if env_file is not None else DEFAULT_ENV_FILE)
        values: Mapping[str, str] = {**file_values, **os.environ}
    else:
        file_values = _read_env_file(Path(env_file)) if env_file is not None else {}
        values = {**file_values, **environ}

    config = Config(
        qdrant_url=_text_setting(values, "QDRANT_URL", DEFAULT_QDRANT_URL),
        ollama_url=_text_setting(values, "OLLAMA_URL", DEFAULT_OLLAMA_URL),
        embed_model=_text_setting(values, "EMBED_MODEL", DEFAULT_EMBED_MODEL),
        ollama_model=_text_setting(values, "OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        enable_llm=_bool_setting(values, "ENABLE_LLM", default=False),
        abstain_threshold=_float_setting(
            values, "ABSTAIN_THRESHOLD", ABSTAIN_THRESHOLD, low=0.0, high=1.0
        ),
        kb_collection=_text_setting(values, "KB_COLLECTION", COLLECTION),
        log_level=_text_setting(values, "LOG_LEVEL", "INFO"),
        telegram_bot_token=_optional_secret(values, "TELEGRAM_BOT_TOKEN"),
        kb_salt=_optional_secret(values, "KB_SALT"),
    )

    if require_secrets:
        config.require_secrets()
    return config


_cached_config: Config | None = None


def get_config(*, require_secrets: bool = False, reload: bool = False) -> Config:
    """Return the process-wide config, loading it on first use.

    A later call with ``require_secrets=True`` re-checks the cached object, so
    importing this module never forces a token but bot startup still fails fast.
    """

    global _cached_config
    if reload or _cached_config is None:
        _cached_config = load_config(require_secrets=require_secrets)
    elif require_secrets:
        _cached_config.require_secrets()
    return _cached_config


def reset_config() -> None:
    """Drop the cached config. Intended for tests and reload-safe tooling."""

    global _cached_config
    _cached_config = None


def _read_env_file(path: Path) -> dict[str, str]:
    """Read a minimal ``KEY=VALUE`` .env file; return an empty map if absent.

    Only the subset of dotenv syntax this project needs is supported: blank
    lines, ``#`` comments, an optional ``export`` prefix, and single- or
    double-quoted values. Existing environment variables are not modified.
    """

    if not path.is_file():
        return {}

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise ConfigError(f"Could not read {path.name}: {reason}") from None

    values: dict[str, str] = {}
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()

        key, separator, raw_value = stripped.partition("=")
        key = key.strip()
        if not separator or not key:
            raise ConfigError(f"{path.name}:{lineno}: expected KEY=VALUE, got a malformed line.")
        if not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ConfigError(f"{path.name}:{lineno}: invalid environment variable name {key!r}.")

        value = raw_value.strip()
        if not value.startswith(('"', "'")) and " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        values[key] = value

    return values


def _raw_value(values: Mapping[str, str], name: str) -> str | None:
    raw = values.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ConfigError(f"{name} must be a string value.")
    return raw.strip()


def _text_setting(values: Mapping[str, str], name: str, default: str) -> str:
    raw = _raw_value(values, name)
    if raw is None:
        return default
    if not raw:
        raise ConfigError(f"{name} must not be empty.")
    return raw


def _optional_secret(values: Mapping[str, str], name: str) -> str | None:
    raw = _raw_value(values, name)
    return raw or None


def _bool_setting(values: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = _raw_value(values, name)
    if raw is None:
        return default

    normalized = raw.lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be one of true/false, yes/no, or 1/0 (got {raw!r}).")


def _float_setting(
    values: Mapping[str, str],
    name: str,
    default: float,
    *,
    low: float,
    high: float,
) -> float:
    raw = _raw_value(values, name)
    if raw is None:
        return default

    try:
        number = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number (got {raw!r}).") from None
    if not math.isfinite(number):
        raise ConfigError(f"{name} must be a finite number (got {raw!r}).")
    if not low <= number <= high:
        raise ConfigError(f"{name} must be between {low:g} and {high:g} (got {number:g}).")
    return number


def _validate_url(name: str, value: str) -> None:
    _require_text(name, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ConfigError(f"{name} must be an http:// or https:// URL.")
    if not parsed.hostname:
        raise ConfigError(f"{name} must include a hostname.")

    try:
        port = parsed.port
    except ValueError:
        raise ConfigError(f"{name} has a port outside the range 1-65535.") from None
    if port is not None and not 1 <= port <= 65535:
        raise ConfigError(f"{name} has a port outside the range 1-65535.")


def _require_text(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ConfigError(f"{name} must not be empty.")
