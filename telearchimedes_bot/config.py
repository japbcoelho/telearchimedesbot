from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeConfig:
    bot_token: str | None
    bot_api_base_url: str
    data_path: str
    timezone: ZoneInfo
    daily_star_limit: int
    weekly_ranking_weekday: int
    weekly_ranking_hour: int
    weekly_ranking_minute: int
    question_digest_thread_id: int
    question_digest_hours: tuple[int, ...]
    allowed_chat_ids: set[int]
    allowed_chat_usernames: set[str]
    allow_all_groups: bool
    owner_user_ids: set[int]
    private_chat_notice: str
    trusted_footer_bot_usernames: set[str]
    strict_config: bool


DEFAULT_PRIVATE_CHAT_NOTICE = "Este bot só funciona nos grupos autorizados."
DEFAULT_TRUSTED_FOOTER_BOT_USERNAMES = "telelatexbot"

load_dotenv()


def _text_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _read_secret_file(path_value: str, *, variable_name: str) -> str:
    path = Path(path_value)
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{variable_name} aponta para um arquivo ilegível: {path}") from exc

    value = value.rstrip("\r\n")
    if not value:
        raise ConfigError(f"{variable_name} aponta para um arquivo vazio: {path}")
    return value


def _secret_env(
    name: str,
    *,
    errors: list[str],
    strict: bool,
    read_secret_file: bool,
) -> str | None:
    file_name = f"{name}_FILE"
    file_path = _text_env(file_name)
    direct_value = _text_env(name)

    if strict and direct_value:
        errors.append(f"{name} não deve ser usado em produção; use {file_name}.")

    if file_path:
        if not read_secret_file:
            return None
        try:
            return _read_secret_file(file_path, variable_name=file_name)
        except ConfigError as exc:
            errors.append(str(exc))
            return None
    return direct_value


def _int_env(
    name: str,
    default: int,
    *,
    errors: list[str],
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = _text_env(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        errors.append(f"{name} deve ser um número inteiro.")
        return default
    if minimum is not None and parsed < minimum:
        errors.append(f"{name} deve ser maior ou igual a {minimum}.")
    if maximum is not None and parsed > maximum:
        errors.append(f"{name} deve ser menor ou igual a {maximum}.")
    return parsed


def _bool_env(name: str, default: bool) -> bool:
    value = _text_env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on", "sim"}


def _chat_ids(value: str | None, *, errors: list[str], name: str = "ALLOWED_CHAT_IDS") -> set[int]:
    if not value:
        return set()
    ids: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            errors.append(f"{name} deve conter apenas IDs inteiros separados por vírgula.")
            break
    return ids


def _usernames(value: str | None, *, errors: list[str], name: str) -> set[str]:
    usernames: set[str] = set()
    for part in (value or "").split(","):
        username = part.strip().removeprefix("@")
        if not username:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", username):
            errors.append(f"{name} deve conter apenas @usernames separados por vírgula.")
            break
        usernames.add(username.lower())
    return usernames


def _question_digest_hours(value: str | None, *, errors: list[str]) -> tuple[int, ...]:
    value = value or "15"
    hours: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour = int(part)
        except ValueError:
            errors.append("QUESTION_DIGEST_HOURS deve conter horas inteiras separadas por vírgula.")
            continue
        if hour < 0 or hour > 23:
            errors.append("QUESTION_DIGEST_HOURS deve conter apenas valores entre 0 e 23.")
            continue
        hours.add(hour)
    if not hours:
        errors.append("QUESTION_DIGEST_HOURS deve conter ao menos uma hora.")
        return (15,)
    return tuple(sorted(hours))


def _timezone(value: str | None, *, errors: list[str]) -> ZoneInfo:
    value = value or "America/Sao_Paulo"
    try:
        return ZoneInfo(value)
    except Exception:
        errors.append(f"TIMEZONE inválido: {value}.")
        return ZoneInfo("America/Sao_Paulo")


def load_runtime_config(
    *,
    require_token: bool = False,
    strict: bool | None = None,
    read_secret_file: bool = True,
) -> RuntimeConfig:
    errors: list[str] = []
    strict_config = _bool_env("TELEARCHIMEDES_STRICT_CONFIG", False) if strict is None else strict
    bot_token = _secret_env(
        "BOT_TOKEN",
        errors=errors,
        strict=strict_config,
        read_secret_file=read_secret_file,
    )
    bot_api_base_url = _text_env("BOT_API_BASE_URL", "https://api.telegram.org") or ""
    data_path = _text_env("DATA_PATH", "stars.sqlite3") or ""

    if require_token and not bot_token:
        errors.append("BOT_TOKEN_FILE ou BOT_TOKEN deve estar configurado.")
    if strict_config and not Path(data_path).is_absolute():
        errors.append("DATA_PATH deve ser absoluto quando TELEARCHIMEDES_STRICT_CONFIG=1.")

    runtime_config = RuntimeConfig(
        bot_token=bot_token,
        bot_api_base_url=bot_api_base_url,
        data_path=data_path,
        timezone=_timezone(_text_env("TIMEZONE"), errors=errors),
        daily_star_limit=_int_env("DAILY_STAR_LIMIT", 3, errors=errors, minimum=1),
        weekly_ranking_weekday=_int_env(
            "WEEKLY_RANKING_WEEKDAY",
            6,
            errors=errors,
            minimum=0,
            maximum=6,
        ),
        weekly_ranking_hour=_int_env(
            "WEEKLY_RANKING_HOUR",
            20,
            errors=errors,
            minimum=0,
            maximum=23,
        ),
        weekly_ranking_minute=_int_env(
            "WEEKLY_RANKING_MINUTE",
            0,
            errors=errors,
            minimum=0,
            maximum=59,
        ),
        question_digest_thread_id=_int_env(
            "QUESTION_DIGEST_THREAD_ID",
            0,
            errors=errors,
            minimum=0,
        ),
        question_digest_hours=_question_digest_hours(
            _text_env("QUESTION_DIGEST_HOURS"),
            errors=errors,
        ),
        allowed_chat_ids=_chat_ids(_text_env("ALLOWED_CHAT_IDS"), errors=errors),
        allowed_chat_usernames=_usernames(
            _text_env("ALLOWED_CHAT_USERNAMES"), errors=errors, name="ALLOWED_CHAT_USERNAMES"
        ),
        allow_all_groups=_bool_env("ALLOW_ALL_GROUPS", False),
        owner_user_ids=_chat_ids(_text_env("OWNER_USER_IDS"), errors=errors, name="OWNER_USER_IDS"),
        private_chat_notice=_text_env("PRIVATE_CHAT_NOTICE", DEFAULT_PRIVATE_CHAT_NOTICE)
        or DEFAULT_PRIVATE_CHAT_NOTICE,
        trusted_footer_bot_usernames=_usernames(
            _text_env("TRUSTED_FOOTER_BOT_USERNAMES", DEFAULT_TRUSTED_FOOTER_BOT_USERNAMES),
            errors=errors,
            name="TRUSTED_FOOTER_BOT_USERNAMES",
        ),
        strict_config=strict_config,
    )

    if errors:
        raise ConfigError("; ".join(errors))
    return runtime_config


_DEFAULT_CONFIG = load_runtime_config(strict=False, read_secret_file=False)
BOT_TOKEN = _DEFAULT_CONFIG.bot_token
BOT_API_BASE_URL = _DEFAULT_CONFIG.bot_api_base_url
DATA_PATH = _DEFAULT_CONFIG.data_path
TIMEZONE = _DEFAULT_CONFIG.timezone
DAILY_STAR_LIMIT = _DEFAULT_CONFIG.daily_star_limit
WEEKLY_RANKING_WEEKDAY = _DEFAULT_CONFIG.weekly_ranking_weekday
WEEKLY_RANKING_HOUR = _DEFAULT_CONFIG.weekly_ranking_hour
WEEKLY_RANKING_MINUTE = _DEFAULT_CONFIG.weekly_ranking_minute
QUESTION_DIGEST_THREAD_ID = _DEFAULT_CONFIG.question_digest_thread_id
QUESTION_DIGEST_HOURS = _DEFAULT_CONFIG.question_digest_hours
ALLOWED_CHAT_IDS = _DEFAULT_CONFIG.allowed_chat_ids
TELEARCHIMEDES_STRICT_CONFIG = _DEFAULT_CONFIG.strict_config


def is_configured() -> bool:
    return bool(BOT_TOKEN)
