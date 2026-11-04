from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import sys
from pathlib import Path

from . import config
from .bot import create_bot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="telearchimedesbot")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run", help="inicia o bot por long polling")
    subparsers.add_parser("config-check", help="valida a configuração sem acessar a rede")
    subparsers.add_parser(
        "preflight", help="valida configuração e estado local sem consumir updates"
    )

    args = parser.parse_args(argv)
    command = args.command or "run"

    try:
        if command == "run":
            return _run()
        if command == "config-check":
            runtime_config = config.load_runtime_config(require_token=True)
            _validate_paths(runtime_config)
            print("configuração válida")
            return 0
        if command == "preflight":
            runtime_config = config.load_runtime_config(require_token=True)
            _validate_paths(runtime_config)
            _check_database(runtime_config.data_path)
            print("preflight ok")
            return 0
    except config.ConfigError as exc:
        print(f"erro de configuração: {exc}", file=sys.stderr)
        return 2

    parser.print_help(sys.stderr)
    return 2


def _run() -> int:
    runtime_config = config.load_runtime_config(require_token=True)
    _validate_paths(runtime_config)
    bot = create_bot(runtime_config)
    if bot is None:
        raise config.ConfigError("BOT_TOKEN_FILE ou BOT_TOKEN deve estar configurado.")

    def request_stop(_signum: int, _frame: object) -> None:
        bot.stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    bot.run()
    return 0


def _validate_paths(runtime_config: config.RuntimeConfig) -> None:
    if runtime_config.strict_config and not Path(runtime_config.data_path).is_absolute():
        raise config.ConfigError(
            "DATA_PATH deve ser absoluto quando TELEARCHIMEDES_STRICT_CONFIG=1."
        )


def _check_database(data_path: str) -> None:
    path = Path(data_path)
    parent = path.parent if str(path.parent) else Path(".")
    if not parent.exists():
        raise config.ConfigError(f"diretório do banco não existe: {parent}")
    if not os.access(parent, os.X_OK | os.W_OK):
        raise config.ConfigError(f"diretório do banco não é gravável: {parent}")
    if not path.exists():
        return
    if not os.access(path, os.R_OK | os.W_OK):
        raise config.ConfigError(f"banco não é legível e gravável: {path}")

    uri = path.absolute().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise config.ConfigError(f"falha ao abrir o banco SQLite: {exc}") from exc
    if result is None or result[0] != "ok":
        raise config.ConfigError("integridade do banco SQLite falhou.")


if __name__ == "__main__":
    raise SystemExit(main())
