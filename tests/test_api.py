from __future__ import annotations

from telearchimedes_bot.api import TelegramApi


def test_get_updates_only_requests_messages() -> None:
    calls: list[tuple[str, dict]] = []
    api = TelegramApi(
        "test-token",
        request_func=lambda method, payload: calls.append((method, payload)) or [],
    )

    assert api.get_updates(offset=9, timeout=5) == []
    assert calls == [
        (
            "getUpdates",
            {
                "timeout": 5,
                "allowed_updates": ["message"],
                "offset": 9,
            },
        )
    ]


def test_set_my_commands_uses_bot_api_payload() -> None:
    calls: list[tuple[str, dict]] = []
    api = TelegramApi(
        "test-token",
        request_func=lambda method, payload: calls.append((method, payload)) or True,
    )
    commands = [{"command": "pendentes", "description": "Listar dúvidas pendentes"}]

    assert api.set_my_commands(commands) is True
    assert calls == [("setMyCommands", {"commands": commands})]


def test_send_message_can_target_topic_and_reply() -> None:
    calls: list[tuple[str, dict]] = []
    api = TelegramApi(
        "test-token",
        request_func=lambda method, payload: calls.append((method, payload)) or {"message_id": 1},
    )

    api.send_message(
        chat_id=-100,
        text="Oi",
        parse_mode="HTML",
        message_thread_id=77,
        reply_to_message_id=10,
    )

    assert calls == [
        (
            "sendMessage",
            {
                "chat_id": -100,
                "text": "Oi",
                "disable_web_page_preview": True,
                "parse_mode": "HTML",
                "message_thread_id": 77,
                "reply_parameters": {"message_id": 10, "allow_sending_without_reply": True},
            },
        )
    ]


def test_delete_message_uses_bot_api_payload() -> None:
    calls: list[tuple[str, dict]] = []
    api = TelegramApi(
        "test-token",
        request_func=lambda method, payload: calls.append((method, payload)) or True,
    )

    assert api.delete_message(chat_id=-100, message_id=55) is True
    assert calls == [("deleteMessage", {"chat_id": -100, "message_id": 55})]
