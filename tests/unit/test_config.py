"""Tests for configuration, log hygiene, and the quarantine of the old product.

T7.2 config and secrets, T7.3 logs without secrets, T8.1 no web app, T8.2 no legacy imports.
"""

import ast
import logging
from pathlib import Path

import pytest

import logging_config
from config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"
SALT = "fixture-salt-value"
V2_PACKAGES = ("ingestion", "store", "retrieval", "answer", "bot", "eval", "datasets", "scripts")
V2_FILES = ("cli.py", "config.py", "logging_config.py")


def v2_python_files():
    """Every Python file of the V2.0 product (never legacy/ or tests/)."""
    files = [ROOT / name for name in V2_FILES]
    for package in V2_PACKAGES:
        files += sorted((ROOT / package).rglob("*.py"))
    return files


def imported_modules(path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{alias.name}" for alias in node.names}
    return names


# ---------------------------------------------------------------- T7.2 configuration


def test_defaults_match_the_spec():
    config = load_config(environ={})
    assert config.qdrant_url == "http://localhost:6333"
    assert config.ollama_url == "http://127.0.0.1:11434"
    assert config.ollama_model == "gemma4:12b-it-qat"
    assert config.enable_llm is False
    assert config.abstain_threshold == 0.45
    assert config.kb_collection == "rupp_senior_projects_v1"
    assert config.telegram_bot_token is None and config.kb_salt is None


def test_missing_secrets_fail_loudly_without_values():
    with pytest.raises(ConfigError) as error:
        load_config(environ={}, require_secrets=True)
    assert "TELEGRAM_BOT_TOKEN" in str(error.value) and "KB_SALT" in str(error.value)

    config = load_config(environ={"TELEGRAM_BOT_TOKEN": TOKEN}, require_secrets=False)
    with pytest.raises(ConfigError) as error:
        config.require_secrets()
    assert "KB_SALT" in str(error.value)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "name, value",
    [
        ("ABSTAIN_THRESHOLD", "1.5"),
        ("ABSTAIN_THRESHOLD", "-0.1"),
        ("ABSTAIN_THRESHOLD", "not-a-number"),
        ("QDRANT_URL", "http://localhost:70000"),
        ("QDRANT_URL", "localhost:6333"),
        ("ENABLE_LLM", "maybe"),
        ("LOG_LEVEL", "LOUD"),
    ],
)
def test_invalid_values_are_rejected(name, value):
    with pytest.raises(ConfigError):
        load_config(environ={name: value})


def test_token_and_salt_never_appear_in_repr():
    config = load_config(environ={"TELEGRAM_BOT_TOKEN": TOKEN, "KB_SALT": SALT})
    for text in (repr(config), str(config), str(config.safe_dict())):
        assert TOKEN not in text and SALT not in text
    assert config.telegram_bot_token == TOKEN  # still usable by the bot


def test_env_file_is_read_only_when_present(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ABSTAIN_THRESHOLD=0.5\nENABLE_LLM=true\n", encoding="utf-8")
    config = load_config(env_file=env, environ={})
    assert (config.abstain_threshold, config.enable_llm) == (0.5, True)
    assert load_config(env_file=tmp_path / "missing.env", environ={}).abstain_threshold == 0.45


def test_env_example_is_complete_and_has_no_secrets():
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    expected = {
        "TELEGRAM_BOT_TOKEN",
        "KB_SALT",
        "QDRANT_URL",
        "OLLAMA_URL",
        "EMBED_MODEL",
        "OLLAMA_MODEL",
        "ENABLE_LLM",
        "ABSTAIN_THRESHOLD",
        "KB_COLLECTION",
        "LOG_LEVEL",
    }
    assert expected <= set(values)
    assert values["TELEGRAM_BOT_TOKEN"] == "" and values["KB_SALT"] == ""


# ---------------------------------------------------------------- T7.3 logs


def test_logs_do_not_contain_secrets(tmp_path):
    root_level = logging.getLogger().level
    files = logging_config.configure_logging(log_dir=tmp_path, secrets=[TOKEN, SALT], console=False)
    try:
        logger = logging.getLogger("rupp.test")
        logger.warning("token %s salt %s", TOKEN, SALT)
        logger.warning("another bot token 987654321:ZZFakeTokenForTestsOnly_zyxwvutsrqpo")
        logger.warning("contact sok.dara@example.invalid or +855 12 345 678")
        logging_config.log_startup(
            embed_model="BAAI/bge-m3@abc",
            llm_model="gemma4:12b-it-qat",
            qdrant_version="1.20.0",
            collection="rupp_senior_projects_v1",
            point_count=11,
        )
        logging_config.log_answer_validation("citation_rejected", reason="invented page")
        text = files.bot.read_text(encoding="utf-8")
        for secret in (TOKEN, SALT, "987654321:ZZFake", "sok.dara@example.invalid", "345 678"):
            assert secret not in text
        assert "startup embed_model=BAAI/bge-m3@abc" in text  # the model tags are logged
        assert "citation_rejected" in files.answer_validation.read_text(encoding="utf-8")
    finally:
        logging_config._clear_owned_handlers()
        logging.getLogger().setLevel(root_level)


# ---------------------------------------------------------------- T8.1 / T8.2 quarantine


def test_no_web_imports():
    for path in v2_python_files():
        for module in imported_modules(path):
            assert "web_app" not in module, path
            assert not module.startswith("http.server"), path
    for doc in ("README.md", ".env.example"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "WEB_PORT" not in text and "web_app" not in text, doc


def test_no_legacy_imports():
    banned = ("legacy", "curriculum", "src.")
    for path in v2_python_files():
        for module in imported_modules(path):
            assert not module.startswith(banned), (path, module)
        source = path.read_text(encoding="utf-8")
        for name in ("PROGRAM_NAMES", "REFUSED_PROGRAMS", "COURSE_LOOKUP", "CURRICULUM_LIST"):
            assert name not in source, (path, name)
    # The old tests stay in legacy/ and are not collected by the V2.0 suite.
    assert '"legacy"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
