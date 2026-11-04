from __future__ import annotations

from pathlib import Path

from telearchimedes_bot.__main__ import main


def test_preflight_accepts_token_file_and_existing_database(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("test-token\n", encoding="utf-8")
    data_path = tmp_path / "stars.sqlite3"

    monkeypatch.setenv("BOT_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("DATA_PATH", str(data_path))
    monkeypatch.setenv("TELEARCHIMEDES_STRICT_CONFIG", "1")

    assert main(["preflight"]) == 0
    assert "preflight ok" in capsys.readouterr().out


def test_config_check_rejects_missing_token(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN_FILE", raising=False)
    monkeypatch.setenv("DATA_PATH", str(tmp_path / "stars.sqlite3"))

    assert main(["config-check"]) == 2
    assert "BOT_TOKEN_FILE ou BOT_TOKEN" in capsys.readouterr().err
