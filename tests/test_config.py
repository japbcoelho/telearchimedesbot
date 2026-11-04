from __future__ import annotations

import importlib
from pathlib import Path

import pytest


CONFIG_ENV_NAMES = {
    "ALLOWED_CHAT_IDS",
    "ALLOWED_CHAT_USERNAMES",
    "ALLOW_ALL_GROUPS",
    "OWNER_USER_IDS",
    "PRIVATE_CHAT_NOTICE",
    "TRUSTED_FOOTER_BOT_USERNAMES",
    "BOT_API_BASE_URL",
    "BOT_TOKEN",
    "BOT_TOKEN_FILE",
    "DAILY_STAR_LIMIT",
    "DATA_PATH",
    "QUESTION_DIGEST_HOURS",
    "QUESTION_DIGEST_THREAD_ID",
    "TELEARCHIMEDES_STRICT_CONFIG",
    "TIMEZONE",
    "WEEKLY_RANKING_HOUR",
    "WEEKLY_RANKING_MINUTE",
    "WEEKLY_RANKING_WEEKDAY",
}


def reload_config(monkeypatch: pytest.MonkeyPatch):
    for name in CONFIG_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")

    import telearchimedes_bot.config as config

    return importlib.reload(config)


def test_bot_token_file_is_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("token-from-file\n", encoding="utf-8")
    config = reload_config(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("DATA_PATH", str(tmp_path / "stars.sqlite3"))
    monkeypatch.setenv("TELEARCHIMEDES_STRICT_CONFIG", "1")

    runtime_config = config.load_runtime_config(require_token=True)

    assert runtime_config.bot_token == "token-from-file"
    assert runtime_config.data_path == str(tmp_path / "stars.sqlite3")


def test_strict_config_rejects_direct_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = reload_config(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "direct-token")
    monkeypatch.setenv("DATA_PATH", str(tmp_path / "stars.sqlite3"))
    monkeypatch.setenv("TELEARCHIMEDES_STRICT_CONFIG", "1")

    with pytest.raises(config.ConfigError, match="BOT_TOKEN não deve ser usado"):
        config.load_runtime_config(require_token=True)


def test_strict_config_requires_absolute_data_path(monkeypatch: pytest.MonkeyPatch) -> None:
    config = reload_config(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "direct-token")
    monkeypatch.setenv("DATA_PATH", "stars.sqlite3")

    with pytest.raises(config.ConfigError, match="DATA_PATH deve ser absoluto"):
        config.load_runtime_config(require_token=True, strict=True)


def test_invalid_digest_hour_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("token\n", encoding="utf-8")
    config = reload_config(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("DATA_PATH", str(tmp_path / "stars.sqlite3"))
    monkeypatch.setenv("QUESTION_DIGEST_HOURS", "15,24")

    with pytest.raises(config.ConfigError, match="QUESTION_DIGEST_HOURS"):
        config.load_runtime_config(require_token=True)


def test_group_and_footer_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    config = reload_config(monkeypatch)
    defaults = config.load_runtime_config(read_secret_file=False)
    assert defaults.allowed_chat_usernames == set()
    assert defaults.allow_all_groups is False
    assert defaults.trusted_footer_bot_usernames == {"telelatexbot"}
    assert defaults.private_chat_notice == "Este bot só funciona nos grupos autorizados."

    monkeypatch.setenv("ALLOWED_CHAT_USERNAMES", "@GrupoDeEstudos, outrogrupo")
    monkeypatch.setenv("TRUSTED_FOOTER_BOT_USERNAMES", "@MeuLatexBot")
    monkeypatch.setenv("PRIVATE_CHAT_NOTICE", "Só no grupo X.")
    loaded = config.load_runtime_config(read_secret_file=False)
    assert loaded.allowed_chat_usernames == {"grupodeestudos", "outrogrupo"}
    assert loaded.trusted_footer_bot_usernames == {"meulatexbot"}
    assert loaded.private_chat_notice == "Só no grupo X."

    monkeypatch.setenv("ALLOWED_CHAT_USERNAMES", "grupo com espaço")
    with pytest.raises(config.ConfigError, match="ALLOWED_CHAT_USERNAMES"):
        config.load_runtime_config(read_secret_file=False)
