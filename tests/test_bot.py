from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from telearchimedes_bot.api import TelegramApiError
from telearchimedes_bot.bot import ArchimedesBot, _message_link_body_has_text
from telearchimedes_bot.store import StarStore


TZ = ZoneInfo("America/Sao_Paulo")


class FakeApi:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.deleted_messages: list[dict[str, Any]] = []
        self.commands: list[dict[str, str]] = []
        self.next_message_id = 1000

    def get_me(self) -> dict[str, Any]:
        return {"id": 999, "username": "ArquimedesBot"}

    def get_updates(self, **_payload: Any) -> list[dict[str, Any]]:
        raise KeyboardInterrupt

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        self.commands = commands
        return True

    def send_message(self, **payload: Any) -> None:
        self.next_message_id += 1
        payload = payload | {"message_id": self.next_message_id}
        self.messages.append(payload)
        return {"message_id": self.next_message_id}

    def delete_message(self, **payload: Any) -> None:
        self.deleted_messages.append(payload)


def make_bot(
    tmp_path: Path,
    *,
    now: datetime | None = None,
    allowed_chat_ids: set[int] | None = None,
    allowed_chat_usernames: set[str] | None = None,
    message_link_exists: Callable[[str], bool] | None = None,
    question_digest_hours: tuple[int, ...] = (15,),
) -> tuple[ArchimedesBot, FakeApi]:
    api = FakeApi()
    store = StarStore(tmp_path / "stars.sqlite3")
    bot = ArchimedesBot(
        api,
        store,
        bot_username="ArquimedesBot",
        allowed_chat_ids={-100} if allowed_chat_ids is None else allowed_chat_ids,
        allowed_chat_usernames=allowed_chat_usernames,
        daily_star_limit=3,
        weekly_ranking_weekday=6,
        weekly_ranking_hour=20,
        weekly_ranking_minute=0,
        question_digest_hours=question_digest_hours,
        message_link_exists=message_link_exists or (lambda _link: True),
        now=lambda: now or datetime(2026, 7, 5, 19, 0, tzinfo=TZ),
    )
    return bot, api


def thanks_message(
    message_id: int = 10, *, giver_id: int = 1, receiver_id: int = 2
) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "message_thread_id": 77,
        "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
        "from": {"id": giver_id, "first_name": "Ana", "username": "ana"},
        "text": "/obrigado",
        "reply_to_message": {
            "message_id": 5,
            "from": {"id": receiver_id, "first_name": "Bruno", "username": "bruno"},
            "text": "Resposta",
        },
    }


def latex_rich_reply(author: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": 6,
        "from": {
            "id": 999,
            "first_name": "TeleLaTeXBot",
            "username": "telelatexbot",
            "is_bot": True,
        },
        "rich_message": {
            "blocks": [
                {"type": "paragraph", "text": "Resposta renderizada"},
                {"type": "paragraph", "text": "────────────"},
                {
                    "type": "paragraph",
                    "text": ["Código enviado por ", author],
                },
            ]
        },
    }


def test_run_registers_all_autocomplete_commands(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    try:
        bot.run()
    except KeyboardInterrupt:
        pass

    command_names = {command["command"] for command in api.commands}
    assert {
        "obrigado",
        "obrigada",
        "duvida",
        "pendentes",
        "resolvida",
        "resolvido",
        "ranking",
        "help",
        "start",
    } <= command_names


def test_obrigado_reply_gives_star(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(thanks_message())

    message = api.messages[0]
    assert message["chat_id"] == -100
    assert message["message_thread_id"] == 77
    assert message["reply_to_message_id"] == 10
    assert "Bruno (@bruno)" in message["text"]
    assert "Total geral: <b>1</b>" in message["text"]
    assert "Você ainda pode dar <b>2</b> estrelas hoje." in message["text"]


def test_obrigado_reply_to_anonymous_group_message_credits_group(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 10,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": {
                "message_id": 5,
                "from": {
                    "id": 1087968824,
                    "first_name": "Group",
                    "username": "GroupAnonymousBot",
                    "is_bot": True,
                },
                "sender_chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
                "text": "Resposta como grupo",
            },
        }
    )

    assert "⭐ Estudos ganhou uma estrela" in api.messages[0]["text"]
    assert "@Estudos" not in api.messages[0]["text"]
    assert "Estrelas contam apenas para pessoas" not in api.messages[0]["text"]

    bot.handle_message(
        {
            "message_id": 20,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/ranking",
        }
    )

    assert "🥇 Ouro Estudos — <b>1</b> ⭐" in api.messages[-1]["text"]


def test_plain_thanks_message_sends_usage_reminder(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 12,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "valeu demais!",
        }
    )

    assert api.messages[0]["reply_to_message_id"] == 12
    assert "Responda à mensagem de quem te ajudou com /obrigado" in api.messages[0]["text"]


def test_help_explains_commands(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 12,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/help",
        }
    )

    text = api.messages[0]["text"]
    assert "/obrigado" in text
    assert "/duvida" in text
    assert "/pendentes" in text
    assert "/resolvida" in text
    assert "/ranking" in text
    assert "bot de LaTeX" in text
    assert "sem legenda também são aceitos" in text
    assert "descrição depois do comando é opcional" in text


def test_bot_allows_group_listed_by_username(tmp_path: Path) -> None:
    bot, api = make_bot(
        tmp_path, allowed_chat_ids=set(), allowed_chat_usernames={"@GrupoDeEstudos"}
    )

    bot.handle_message(
        {
            "message_id": 12,
            "chat": {
                "id": -200,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/help",
        }
    )

    assert "<b>Arquimedes</b>" in api.messages[-1]["text"]


def test_bot_stays_silent_in_private_for_non_owners(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 12,
            "chat": {"id": 1, "type": "private", "first_name": "Ana"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/help",
        }
    )

    assert api.messages == []


def test_bot_rejects_private_chat(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.owner_user_ids = {1}

    bot.handle_message(
        {
            "message_id": 12,
            "chat": {"id": 1, "type": "private", "first_name": "Ana"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/help",
        }
    )

    assert api.messages[-1]["text"] == "Este bot só funciona nos grupos autorizados."


def test_bot_stays_silent_in_other_group(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, allowed_chat_ids=set(), allowed_chat_usernames={"grupodeestudos"})

    bot.handle_message(
        {
            "message_id": 12,
            "chat": {"id": -200, "type": "supergroup", "title": "Outro", "username": "outrogrupo"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/help",
        }
    )

    assert api.messages == []


def test_bot_works_in_every_listed_group(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, allowed_chat_ids={-1001234567890, -100777})

    for chat_id in (-1001234567890, -100777):
        bot.handle_message(
            {
                "message_id": 12,
                "chat": {"id": chat_id, "type": "supergroup", "title": "Grupo"},
                "from": {"id": 1, "first_name": "Ana", "username": "ana"},
                "text": "/help",
            }
        )
        assert "<b>Arquimedes</b>" in api.messages[-1]["text"]


def test_plain_thanks_reminder_replaces_previous_reminder_in_same_topic(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 12,
            "message_thread_id": 77,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "valeu!",
        }
    )
    first_reminder_id = api.messages[0]["message_id"]

    bot.handle_message(
        {
            "message_id": 13,
            "message_thread_id": 77,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
            "text": "obg!",
        }
    )

    assert api.deleted_messages == [{"chat_id": -100, "message_id": first_reminder_id}]
    assert api.messages[-1]["reply_to_message_id"] == 13


def test_obrigado_reply_to_latex_bot_message_credits_footer_author(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    text = "x = 3\n\nMensagem respondida por: @clara"

    bot.handle_message(
        {
            "message_id": 13,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": {
                "message_id": 6,
                "from": {
                    "id": 999,
                    "first_name": "TeleLaTeXBot",
                    "username": "telelatexbot",
                    "is_bot": True,
                },
                "text": text,
                "entities": [
                    {
                        "type": "text_link",
                        "offset": text.index("@clara"),
                        "length": len("@clara"),
                        "url": "tg://user?id=456",
                    }
                ],
            },
        }
    )

    assert "@clara" in api.messages[0]["text"]
    assert "Estrelas contam apenas para pessoas" not in api.messages[0]["text"]

    bot.handle_message(
        {
            "message_id": 20,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/ranking",
        }
    )

    ranking = api.messages[-1]["text"]
    assert "@clara" in ranking
    assert "<b>1</b> ⭐" in ranking


def test_obrigado_reply_to_latex_bot_rich_message_credits_footer_author(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 13,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": {
                "message_id": 6,
                "from": {
                    "id": 999,
                    "first_name": "TeleLaTeXBot",
                    "username": "telelatexbot",
                    "is_bot": True,
                },
                "rich_message": {
                    "html": (
                        r"<tg-math>x=3</tg-math><br><br>"
                        "\n\n"
                        r'<i>Mensagem respondida por:</i> <a href="tg://user?id=456">@clara</a>'
                    )
                },
            },
        }
    )

    assert "@clara" in api.messages[0]["text"]
    assert "Estrelas contam apenas para pessoas" not in api.messages[0]["text"]


def test_obrigado_reads_current_latex_rich_footer_with_user_id(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 14,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": latex_rich_reply(
                {
                    "type": "text_mention",
                    "text": "@clara",
                    "user": {
                        "id": 456,
                        "is_bot": False,
                        "first_name": "Clara",
                        "username": "clara",
                    },
                }
            ),
        }
    )

    assert "Clara (@clara)</a> ganhou uma estrela" in api.messages[0]["text"]
    assert "Estrelas contam apenas para pessoas" not in api.messages[0]["text"]


def test_obrigado_reads_latex_profile_identity_without_username(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 15,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": latex_rich_reply(
                {
                    "type": "text_mention",
                    "text": "Diego Souza",
                    "user": {
                        "id": 457,
                        "is_bot": False,
                        "first_name": "Diego",
                        "last_name": "Souza",
                    },
                }
            ),
        }
    )

    response = api.messages[0]["text"]
    assert 'tg://user?id=457">Diego Souza</a> ganhou uma estrela' in response
    assert "Estrelas contam apenas para pessoas" not in response


def test_obrigado_reads_latex_text_link_profile_identity(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 16,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": latex_rich_reply(
                {
                    "type": "text_link",
                    "text": "Pessoa sem arroba",
                    "url": "tg://user?id=458",
                }
            ),
        }
    )

    assert 'tg://user?id=458">Pessoa sem arroba</a> ganhou uma estrela' in (api.messages[0]["text"])


def test_obrigado_maps_current_latex_anonymous_footer_to_group(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 17,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": latex_rich_reply(
                {
                    "type": "mention",
                    "text": "@grupodeestudos",
                    "username": "grupodeestudos",
                }
            ),
        }
    )

    assert "⭐ Estudos ganhou uma estrela" in api.messages[0]["text"]
    assert "Estrelas contam apenas para pessoas" not in api.messages[0]["text"]


def test_obrigado_maps_old_group_anonymous_bot_footer_to_group(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 18,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": latex_rich_reply(
                {
                    "type": "text_mention",
                    "text": "@GroupAnonymousBot",
                    "user": {
                        "id": 1087968824,
                        "is_bot": True,
                        "first_name": "GroupAnonymousBot",
                        "username": "GroupAnonymousBot",
                    },
                }
            ),
        }
    )

    assert "⭐ Estudos ganhou uma estrela" in api.messages[0]["text"]
    assert "Estrelas contam apenas para pessoas" not in api.messages[0]["text"]


def test_obrigado_reads_markdown_profile_link_from_latex_footer(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 19,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": {
                "message_id": 6,
                "from": {
                    "id": 999,
                    "first_name": "TeleLaTeXBot",
                    "username": "telelatexbot",
                    "is_bot": True,
                },
                "text": ("Resposta\n────────────\nCódigo enviado por [Diego](tg://user?id=459)"),
            },
        }
    )

    assert 'tg://user?id=459">Diego</a> ganhou uma estrela' in api.messages[0]["text"]


def test_obrigado_rejects_self_star_from_latex_profile_identity(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 20,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": latex_rich_reply(
                {
                    "type": "text_mention",
                    "text": "@ana",
                    "user": {
                        "id": 1,
                        "is_bot": False,
                        "first_name": "Ana",
                        "username": "ana",
                    },
                }
            ),
        }
    )

    assert api.messages[0]["text"] == "Você não pode dar estrela para si mesmo."


def test_obrigado_refuses_latex_footer_with_only_a_name(tmp_path: Path) -> None:
    for index, label in enumerate(("Pessoa Sem Arroba", "+55 11 99999-0000"), start=1):
        bot, api = make_bot(tmp_path / str(index))
        bot.handle_message(
            {
                "message_id": 30 + index,
                "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
                "from": {"id": index, "first_name": "Agradecedor"},
                "text": "/obrigado",
                "reply_to_message": {
                    "message_id": 6,
                    "from": {
                        "id": 999,
                        "first_name": "TeleLaTeXBot",
                        "username": "telelatexbot",
                        "is_bot": True,
                    },
                    "text": f"Resposta\n────────────\nCódigo enviado por {label}",
                },
            }
        )

        assert "Não consegui identificar quem enviou esse código" in api.messages[0]["text"]
        assert "ganhou uma estrela" not in api.messages[0]["text"]


def test_obrigado_does_not_credit_member_with_same_display_name(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.store.upsert_user(
        {"id": 600000001, "first_name": "Diego", "username": "diego_exemplo"},
        updated_at="now",
    )

    bot.handle_message(
        {
            "message_id": 31,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": {
                "message_id": 6,
                "from": {
                    "id": 999,
                    "first_name": "TeleLaTeXBot",
                    "username": "telelatexbot",
                    "is_bot": True,
                },
                "text": "Resposta\n────────────\nCódigo enviado por Diego",
            },
        }
    )

    assert "ganhou uma estrela" not in api.messages[0]["text"]
    assert bot.store.all_time_ranking(chat_id=-100) == []


def test_obrigado_credits_unknown_username_from_latex_footer(tmp_path: Path) -> None:
    for index, label in enumerate(("@usuario_novo",), start=1):
        bot, api = make_bot(tmp_path / str(index))
        bot.handle_message(
            {
                "message_id": 30 + index,
                "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
                "from": {"id": index, "first_name": "Agradecedor"},
                "text": "/obrigado",
                "reply_to_message": {
                    "message_id": 6,
                    "from": {
                        "id": 999,
                        "first_name": "TeleLaTeXBot",
                        "username": "telelatexbot",
                        "is_bot": True,
                    },
                    "text": f"Resposta\n────────────\nCódigo enviado por {label}",
                },
            }
        )

        assert label in api.messages[0]["text"]
        assert "ganhou uma estrela" in api.messages[0]["text"]
        assert "Estrelas contam apenas para pessoas" not in api.messages[0]["text"]


def test_obrigado_reply_to_latex_bot_plain_footer_credits_known_username(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.store.upsert_user(
        {"id": 600000001, "first_name": "Diego", "username": "diego_exemplo"},
        updated_at="now",
    )

    bot.handle_message(
        {
            "message_id": 13,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {
                "id": 1087968824,
                "first_name": "Group",
                "username": "GroupAnonymousBot",
                "is_bot": True,
            },
            "text": "/obrigado",
            "reply_to_message": {
                "message_id": 6,
                "from": {
                    "id": 999,
                    "first_name": "TeleLaTeXBot",
                    "username": "telelatexbot",
                    "is_bot": True,
                },
                "text": "x = 3\n\nMensagem respondida por: @diego_exemplo",
            },
        }
    )

    assert "Diego (@diego_exemplo)" in api.messages[0]["text"]
    assert "Estrelas contam apenas para pessoas" not in api.messages[0]["text"]


def test_obrigado_reply_to_latex_bot_nested_footer_credits_known_username(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.store.upsert_user(
        {"id": 600000001, "first_name": "Diego", "username": "diego_exemplo"},
        updated_at="now",
    )

    bot.handle_message(
        {
            "message_id": 13,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": {
                "message_id": 6,
                "from": {
                    "id": 999,
                    "first_name": "TeleLaTeXBot",
                    "username": "telelatexbot",
                    "is_bot": True,
                },
                "rich_text": [
                    {"text": "Mensagem respondida por:"},
                    {"text": "@diego_exemplo"},
                ],
            },
        }
    )

    assert "Diego (@diego_exemplo)" in api.messages[0]["text"]
    assert "Estrelas contam apenas para pessoas" not in api.messages[0]["text"]


def test_obrigado_is_limited_to_three_stars_per_day(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    for index in range(4):
        bot.handle_message(thanks_message(message_id=10 + index, receiver_id=100 + index))

    assert "Sua cota diária de estrelas acabou; será renovada amanhã." in api.messages[-2]["text"]
    assert "Você já deu suas 3 estrelas de hoje." in api.messages[-1]["text"]


def test_obrigado_remaining_star_message_uses_singular(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(thanks_message(message_id=10, receiver_id=100))
    bot.handle_message(thanks_message(message_id=11, receiver_id=101))

    assert "Você ainda pode dar <b>uma</b> estrela hoje." in api.messages[-1]["text"]


def test_duvida_reply_registers_pending_question(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "message_thread_id": 77,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                "text": "Como resolve?",
            },
        }
    )

    assert "Dúvida" in api.messages[-1]["text"]
    assert "#1" in api.messages[-1]["text"]

    digest = bot._question_digest_text(-100)
    assert "Dúvidas pendentes" in digest
    assert "Como resolve?" in digest
    assert "Bruno (@bruno)" not in digest
    assert "https://t.me/grupodeestudos/21" in digest


def test_duvida_reply_to_photo_uses_description_after_command(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    description = (
        "Se L e M são pontos de tangência, o arco LM vale 110, mas como associar isso ao ângulo x?"
    )

    bot.handle_message(
        {
            "message_id": 30,
            "message_thread_id": 77,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {
                "id": 2,
                "first_name": "Diego",
                "last_name": "A",
                "username": "diego",
            },
            "text": f"/duvida@ArquimedesBot\n{description}",
            "reply_to_message": {
                "message_id": 21,
                "message_thread_id": 77,
                "from": {
                    "id": 2,
                    "first_name": "Diego",
                    "last_name": "A",
                    "username": "diego",
                },
                "photo": [{"file_id": "problem-photo"}],
            },
        }
    )

    questions = bot.store.open_questions(chat_id=-100)
    assert len(questions) == 1
    assert questions[0].message_id == 21
    assert questions[0].author_id == 2
    assert questions[0].preview == f"{description[:79].rstrip()}…"
    assert questions[0].link == "https://t.me/grupodeestudos/21"
    assert "Dúvida" in api.messages[-1]["text"]
    assert "#1" in api.messages[-1]["text"]


def test_duvida_reply_to_captioned_photo_uses_caption_without_description(
    tmp_path: Path,
) -> None:
    bot, _api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                "photo": [{"file_id": "problem-photo"}],
                "caption": "Calcule o valor do ângulo x.",
            },
        }
    )

    questions = bot.store.open_questions(chat_id=-100)
    assert len(questions) == 1
    assert questions[0].preview == "Calcule o valor do ângulo x."


def test_duvida_reply_to_photo_without_caption_registers_image(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                "photo": [{"file_id": "problem-photo"}],
            },
        }
    )

    questions = bot.store.open_questions(chat_id=-100)
    assert len(questions) == 1
    assert questions[0].message_id == 21
    assert questions[0].preview == "Imagem"
    assert "registrada como pendente" in api.messages[-1]["text"]


def test_duvida_reply_to_image_document_without_caption_uses_file_name(
    tmp_path: Path,
) -> None:
    bot, _api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                "document": {
                    "file_id": "problem-image",
                    "file_name": "exercicio.png",
                    "mime_type": "image/png",
                },
            },
        }
    )

    questions = bot.store.open_questions(chat_id=-100)
    assert len(questions) == 1
    assert questions[0].preview == "Imagem: exercicio.png"


def test_duvida_inline_description_takes_precedence_over_target_caption(
    tmp_path: Path,
) -> None:
    bot, _api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida@ArquimedesBot Como o arco se relaciona com x?",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                "photo": [{"file_id": "problem-photo"}],
                "caption": "Encontre x.",
            },
        }
    )

    questions = bot.store.open_questions(chat_id=-100)
    assert len(questions) == 1
    assert questions[0].preview == "Como o arco se relaciona com x?"


def test_duvida_without_reply_explains_usage(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
        }
    )

    assert api.deleted_messages == [{"chat_id": -100, "message_id": 30}]
    assert api.messages == []
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_duvida_reply_to_message_without_text_explains_usage(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
            },
        }
    )

    assert "texto, imagem ou outro conteúdo" in api.messages[-1]["text"]
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_duvida_reply_to_bot_message_explains_usage(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 123, "first_name": "Outro Bot", "is_bot": True},
                "text": "Mensagem automática",
            },
        }
    )

    assert "não em mensagens de bots" in api.messages[-1]["text"]
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_duvida_reply_to_archimedes_message_explains_usage(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 999, "first_name": "Arquimedes", "username": "ArquimedesBot"},
                "text": "Use /help.",
            },
        }
    )

    assert "mensagem original do problema" in api.messages[-1]["text"]
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_duvida_reply_to_archimedes_digest_text_explains_usage(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 30,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 1234, "first_name": "Arquimedes antigo", "is_bot": True},
                "text": "📌 Dúvidas pendentes\n\n• #1 — Como resolve?",
            },
        }
    )

    assert "mensagem original do problema" in api.messages[-1]["text"]
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_duvida_same_message_stays_once(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    message = {
        "message_id": 21,
        "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
        "text": "Como resolve?",
    }

    for command_id in (30, 31):
        bot.handle_message(
            {
                "message_id": command_id,
                "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
                "from": {"id": 1, "first_name": "Ana", "username": "ana"},
                "text": "/duvida",
                "reply_to_message": message,
            }
        )

    questions = bot.store.open_questions(chat_id=-100)
    assert len(questions) == 1
    assert questions[0].message_id == 21
    assert "#1" in api.messages[-1]["text"]


def test_pendentes_command_lists_pending_questions_with_links(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.handle_message(
        {
            "message_id": 30,
            "message_thread_id": 77,
            "chat": {
                "id": -100,
                "type": "supergroup",
                "title": "Estudos",
                "username": "grupodeestudos",
            },
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                "text": "Como resolve?",
            },
        }
    )

    bot.handle_message(
        {
            "message_id": 31,
            "message_thread_id": 77,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/pendentes",
        }
    )

    message = api.messages[-1]
    assert message["message_thread_id"] == 77
    assert "reply_to_message_id" not in message
    assert "Dúvidas pendentes" in message["text"]
    assert "https://t.me/grupodeestudos/21" in message["text"]
    assert "Como resolve?" in message["text"]
    assert "Bruno (@bruno)" not in message["text"]


def test_pendentes_command_without_pending_questions_says_empty(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 31,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/pendentes",
        }
    )

    assert "Não há dúvidas pendentes no momento." in api.messages[-1]["text"]


def test_pendentes_uses_twenty_character_preview_and_clickable_labels(
    tmp_path: Path,
) -> None:
    bot, api = make_bot(tmp_path)
    chat = {
        "id": -100,
        "type": "supergroup",
        "title": "Estudos",
        "username": "grupodeestudos",
    }
    for command_id, message_id, text in (
        (30, 21, "12345678901234567890"),
        (31, 22, "abcdefghijklmnopqrstu"),
    ):
        bot.handle_message(
            {
                "message_id": command_id,
                "chat": chat,
                "from": {"id": 1, "first_name": "Ana", "username": "ana"},
                "text": "/duvida",
                "reply_to_message": {
                    "message_id": message_id,
                    "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                    "text": text,
                },
            }
        )

    bot.handle_message(
        {
            "message_id": 32,
            "chat": chat,
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/pendentes",
        }
    )

    digest = api.messages[-1]["text"]
    assert '<a href="https://t.me/grupodeestudos/21"># dúvida 1</a>' in digest
    assert '<a href="https://t.me/grupodeestudos/22"># dúvida 2</a>' in digest
    assert "# dúvida 1</a> — 12345678901234567890\n" in digest
    assert "# dúvida 1</a> — 12345678901234567890..." not in digest
    assert "# dúvida 2</a> — abcdefghijklmnopqrst..." in digest
    assert "u" not in digest.rsplit("—", maxsplit=1)[-1]


def test_pendentes_collects_questions_from_all_topics_in_the_group(
    tmp_path: Path,
) -> None:
    bot, api = make_bot(tmp_path)
    chat = {
        "id": -100,
        "type": "supergroup",
        "title": "Estudos",
        "username": "grupodeestudos",
    }
    for thread_id, command_id, message_id, text in (
        (77, 30, 21, "Dúvida do primeiro tópico"),
        (88, 31, 22, "Dúvida do segundo tópico"),
    ):
        bot.handle_message(
            {
                "message_id": command_id,
                "message_thread_id": thread_id,
                "chat": chat,
                "from": {"id": 1, "first_name": "Ana", "username": "ana"},
                "text": "/duvida",
                "reply_to_message": {
                    "message_id": message_id,
                    "message_thread_id": thread_id,
                    "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                    "text": text,
                },
            }
        )

    bot.handle_message(
        {
            "message_id": 32,
            "message_thread_id": 99,
            "chat": chat,
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/pendentes",
        }
    )

    digest = api.messages[-1]["text"]
    assert "# dúvida 1" in digest
    assert "# dúvida 2" in digest
    assert "Dúvida do primeiro t..." in digest
    assert "Dúvida do segundo tó..." in digest
    assert api.messages[-1]["message_thread_id"] == 99


def test_duvidas_command_is_not_supported(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 31,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvidas",
        }
    )

    assert api.messages == []


def test_resolvida_reply_closes_pending_question(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    question = {
        "message_id": 21,
        "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
        "text": "Como resolve?",
    }
    bot.handle_message(
        {
            "message_id": 30,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": question,
        }
    )

    bot.handle_message(
        {
            "message_id": 31,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 3, "first_name": "Carla", "username": "carla"},
            "text": "/resolvida",
            "reply_to_message": question,
        }
    )

    assert "marcada como resolvida" in api.messages[-1]["text"]
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_resolvida_by_visible_number_closes_pending_question(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.handle_message(
        {
            "message_id": 30,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                "text": "Como resolve?",
            },
        }
    )

    bot.handle_message(
        {
            "message_id": 31,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 3, "first_name": "Carla", "username": "carla"},
            "text": "/resolvida #1",
        }
    )

    assert "marcada como resolvida" in api.messages[-1]["text"]
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_resolvida_by_visible_number_renumbers_pending_questions(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    chat = {"id": -100, "type": "supergroup", "title": "Estudos", "username": "grupodeestudos"}
    for message_id, text in ((21, "Primeira?"), (22, "Segunda?"), (23, "Terceira?")):
        bot.handle_message(
            {
                "message_id": 100 + message_id,
                "chat": chat,
                "from": {"id": 1, "first_name": "Ana", "username": "ana"},
                "text": "/duvida",
                "reply_to_message": {
                    "message_id": message_id,
                    "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                    "text": text,
                },
            }
        )

    bot.handle_message(
        {
            "message_id": 31,
            "chat": chat,
            "from": {"id": 3, "first_name": "Carla", "username": "carla"},
            "text": "/resolvida #2",
        }
    )

    digest = bot._question_digest_text(-100)
    assert "Segunda?" not in digest
    assert "># dúvida 1</a> — Primeira?" in digest
    assert "># dúvida 2</a> — Terceira?" in digest
    assert "># dúvida 3</a>" not in digest
    assert "marcada como resolvida" in api.messages[-1]["text"]


def test_pendentes_closes_questions_with_missing_links(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, message_link_exists=lambda link: not link.endswith("/22"))
    chat = {"id": -100, "type": "supergroup", "title": "Estudos", "username": "grupodeestudos"}
    for message_id, text in ((21, "Ainda existe?"), (22, "Foi apagada?")):
        bot.handle_message(
            {
                "message_id": 100 + message_id,
                "chat": chat,
                "from": {"id": 1, "first_name": "Ana", "username": "ana"},
                "text": "/duvida",
                "reply_to_message": {
                    "message_id": message_id,
                    "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                    "text": text,
                },
            }
        )

    bot.handle_message(
        {
            "message_id": 31,
            "chat": chat,
            "from": {"id": 3, "first_name": "Carla", "username": "carla"},
            "text": "/pendentes",
        }
    )

    open_questions = bot.store.open_questions(chat_id=-100)
    assert [question.message_id for question in open_questions] == [21]
    assert "Ainda existe?" in api.messages[-1]["text"]
    assert "Foi apagada?" not in api.messages[-1]["text"]
    assert "># dúvida 1</a>" in api.messages[-1]["text"]
    assert "># dúvida 2</a>" not in api.messages[-1]["text"]


def test_message_link_body_without_text_is_not_valid_question() -> None:
    assert _message_link_body_has_text('<div class="tgme_widget_message_wrap"></div>') is False
    assert (
        _message_link_body_has_text(
            '<div class="tgme_widget_message_wrap">'
            '<div class="tgme_widget_message_text js-message_text">Como resolve?</div>'
            "</div>"
        )
        is True
    )


def test_question_digest_is_sent_once_daily_at_15_replacing_previous_message(
    tmp_path: Path,
) -> None:
    bot, api = make_bot(tmp_path, now=datetime(2026, 7, 7, 14, 59, tzinfo=TZ))
    bot.handle_message(
        {
            "message_id": 30,
            "message_thread_id": 77,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/duvida",
            "reply_to_message": {
                "message_id": 21,
                "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                "text": "Como resolve?",
            },
        }
    )
    api.messages.clear()

    bot.post_question_digests_if_due()
    assert api.messages == []

    bot._now = lambda: datetime(2026, 7, 7, 15, 0, tzinfo=TZ)
    bot.post_question_digests_if_due()
    bot.post_question_digests_if_due()
    first_digest_id = api.messages[-1]["message_id"]

    bot._now = lambda: datetime(2026, 7, 7, 23, 59, tzinfo=TZ)
    bot.post_question_digests_if_due()
    bot._now = lambda: datetime(2026, 7, 8, 14, 59, tzinfo=TZ)
    bot.post_question_digests_if_due()
    assert len(api.messages) == 1

    bot._now = lambda: datetime(2026, 7, 8, 15, 0, tzinfo=TZ)
    bot.post_question_digests_if_due()
    bot.post_question_digests_if_due()

    assert len(api.messages) == 2
    assert all(message["message_thread_id"] is None for message in api.messages)
    assert all("Dúvidas pendentes" in message["text"] for message in api.messages)
    assert api.deleted_messages == [{"chat_id": -100, "message_id": first_digest_id}]


def test_question_digest_send_failure_is_retried_without_resetting_schedule(
    tmp_path: Path,
) -> None:
    bot, api = make_bot(tmp_path, now=datetime(2026, 7, 7, 15, 0, tzinfo=TZ))
    bot.store.upsert_chat({"id": -100, "type": "supergroup", "title": "Estudos"}, updated_at="now")
    attempts = 0
    original_send_message = api.send_message

    def flaky_send_message(**payload: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TelegramApiError("temporary failure")
        return original_send_message(**payload)

    api.send_message = flaky_send_message  # type: ignore[method-assign]

    bot.post_question_digests_if_due()
    bot.post_question_digests_if_due()
    assert attempts == 1  # waits before trying again

    bot._now = lambda: datetime(2026, 7, 7, 15, 6, tzinfo=TZ)
    bot.post_question_digests_if_due()

    assert attempts == 2
    assert len(api.messages) == 1
    assert api.messages[0]["message_thread_id"] is None


def test_manual_pendentes_does_not_reset_daily_schedule(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, now=datetime(2026, 7, 7, 15, 0, tzinfo=TZ))
    chat = {
        "id": -100,
        "type": "supergroup",
        "title": "Estudos",
        "username": "grupodeestudos",
    }
    bot.store.upsert_chat(chat, updated_at="now")

    bot.post_question_digests_if_due()
    first_automatic_id = api.messages[-1]["message_id"]

    bot.handle_message(
        {
            "message_id": 40,
            "message_thread_id": 77,
            "chat": chat,
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/pendentes",
        }
    )
    manual_id = api.messages[-1]["message_id"]
    bot.post_question_digests_if_due()

    assert api.deleted_messages == []

    bot._now = lambda: datetime(2026, 7, 7, 23, 59, tzinfo=TZ)
    bot.post_question_digests_if_due()
    assert len(api.messages) == 2
    assert api.deleted_messages == []

    bot._now = lambda: datetime(2026, 7, 8, 15, 0, tzinfo=TZ)
    bot.post_question_digests_if_due()

    assert len(api.messages) == 3
    assert first_automatic_id != manual_id
    assert api.messages[-1]["message_thread_id"] is None
    assert api.deleted_messages == [
        {"chat_id": -100, "message_id": first_automatic_id},
        {"chat_id": -100, "message_id": manual_id},
    ]


def test_repeated_manual_pendentes_replaces_only_manual_report(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, now=datetime(2026, 7, 7, 15, 0, tzinfo=TZ))
    chat = {
        "id": -100,
        "type": "supergroup",
        "title": "Estudos",
        "username": "grupodeestudos",
    }
    bot.store.upsert_chat(chat, updated_at="now")
    bot.post_question_digests_if_due()
    automatic_id = api.messages[-1]["message_id"]

    for command_id in (40, 41):
        bot.handle_message(
            {
                "message_id": command_id,
                "message_thread_id": 77,
                "chat": chat,
                "from": {"id": 1, "first_name": "Ana", "username": "ana"},
                "text": "/pendentes",
            }
        )

    first_manual_id = api.messages[-2]["message_id"]
    second_manual_id = api.messages[-1]["message_id"]
    assert api.deleted_messages == [{"chat_id": -100, "message_id": first_manual_id}]
    assert automatic_id not in {deleted["message_id"] for deleted in api.deleted_messages}
    assert "reply_to_message_id" not in api.messages[-1]
    assert bot.store.manual_question_report_state(chat_id=-100, thread_key=77) == second_manual_id
    assert bot.store.question_digest_state(chat_id=-100, thread_key=0) == (
        "2026-07-07T15",
        automatic_id,
    )


def test_failed_question_report_cleanup_is_retried(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, now=datetime(2026, 7, 7, 15, 0, tzinfo=TZ))
    chat = {
        "id": -100,
        "type": "supergroup",
        "title": "Estudos",
        "username": "grupodeestudos",
    }
    bot.store.upsert_chat(chat, updated_at="now")
    bot.post_question_digests_if_due()
    first_automatic_id = api.messages[-1]["message_id"]
    original_delete_message = api.delete_message
    failed_once = False

    def flaky_delete_message(**payload: Any) -> None:
        nonlocal failed_once
        if payload["message_id"] == first_automatic_id and not failed_once:
            failed_once = True
            raise TelegramApiError("temporary failure")
        original_delete_message(**payload)

    api.delete_message = flaky_delete_message  # type: ignore[method-assign]
    bot._now = lambda: datetime(2026, 7, 8, 15, 0, tzinfo=TZ)
    bot.post_question_digests_if_due()

    assert bot.store.question_report_cleanup_message_ids(chat_id=-100) == [first_automatic_id]

    bot.post_question_digests_if_due()

    assert api.deleted_messages == [{"chat_id": -100, "message_id": first_automatic_id}]
    assert bot.store.question_report_cleanup_message_ids(chat_id=-100) == []


def test_automatic_digest_removes_deleted_question_and_renumbers(
    tmp_path: Path,
) -> None:
    bot, api = make_bot(
        tmp_path,
        now=datetime(2026, 7, 7, 15, 0, tzinfo=TZ),
        message_link_exists=lambda link: not link.endswith("/22"),
    )
    chat = {
        "id": -100,
        "type": "supergroup",
        "title": "Estudos",
        "username": "grupodeestudos",
    }
    for command_id, message_id, text in (
        (30, 21, "Primeira pergunta"),
        (31, 22, "Pergunta apagada"),
        (32, 23, "Terceira pergunta"),
    ):
        bot.handle_message(
            {
                "message_id": command_id,
                "chat": chat,
                "from": {"id": 1, "first_name": "Ana", "username": "ana"},
                "text": "/duvida",
                "reply_to_message": {
                    "message_id": message_id,
                    "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
                    "text": text,
                },
            }
        )
    api.messages.clear()

    bot.post_question_digests_if_due()

    digest = api.messages[-1]["text"]
    assert "Pergunta apagada" not in digest
    assert "# dúvida 1</a> — Primeira pergunta" in digest
    assert "# dúvida 2</a> — Terceira pergunta" in digest
    assert "# dúvida 3" not in digest
    assert [question.message_id for question in bot.store.open_questions(chat_id=-100)] == [21, 23]


def test_ranking_command_shows_all_time_top_ten_with_medals(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.handle_message(thanks_message(receiver_id=2))
    bot.handle_message(thanks_message(giver_id=3, receiver_id=2))
    bot.handle_message(thanks_message(giver_id=4, receiver_id=5))
    bot.handle_message(thanks_message(giver_id=6, receiver_id=7))

    bot.handle_message(
        {
            "message_id": 20,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/ranking",
        }
    )

    ranking = api.messages[-1]["text"]
    assert "Ranking global" in ranking
    assert "🥇 Ouro" in ranking
    assert "🥈 Prata" in ranking
    assert "🥉 Bronze" in ranking
    assert "Bruno (@bruno)" in ranking
    assert "<b>2</b> ⭐" in ranking


def test_ranking_command_without_stars_says_empty(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(
        {
            "message_id": 20,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/ranking",
        }
    )

    ranking = api.messages[-1]["text"]
    assert "Ainda ninguém recebeu estrelas." in ranking
    assert "🥇 Ouro" not in ranking


def test_ranking_with_two_winners_omits_bronze(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.handle_message(thanks_message(receiver_id=2))
    bot.handle_message(thanks_message(giver_id=3, receiver_id=5))

    bot.handle_message(
        {
            "message_id": 20,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/ranking",
        }
    )

    ranking = api.messages[-1]["text"]
    assert "🥇 Ouro" in ranking
    assert "🥈 Prata" in ranking
    assert "🥉 Bronze" not in ranking


def test_ranking_command_includes_previous_weeks(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, now=datetime(2026, 7, 12, 19, 0, tzinfo=TZ))
    bot.handle_message(thanks_message(receiver_id=2))
    bot._now = lambda: datetime(2026, 7, 19, 19, 0, tzinfo=TZ)
    bot.handle_message(thanks_message(message_id=11, giver_id=3, receiver_id=2))

    bot.handle_message(
        {
            "message_id": 20,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/ranking",
        }
    )

    assert "<b>2</b> ⭐" in api.messages[-1]["text"]


def test_scheduled_ranking_sends_global_total_once_to_known_chats(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, now=datetime(2026, 7, 5, 19, 0, tzinfo=TZ))
    bot.handle_message(thanks_message(receiver_id=2))
    bot._now = lambda: datetime(2026, 7, 12, 20, 1, tzinfo=TZ)
    bot.handle_message(thanks_message(message_id=11, giver_id=3, receiver_id=2))
    api.messages.clear()

    bot.post_weekly_rankings_if_due()
    bot.post_weekly_rankings_if_due()

    assert len(api.messages) == 1
    assert api.messages[0]["chat_id"] == -100
    assert "Ranking global" in api.messages[0]["text"]
    assert "Ranking semanal" not in api.messages[0]["text"]
    assert "Bruno (@bruno)" in api.messages[0]["text"]
    assert "<b>2</b> ⭐" in api.messages[0]["text"]


def topic_root(thread_id: int = 4) -> dict[str, Any]:
    return {
        "message_id": thread_id,
        "message_thread_id": thread_id,
        "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
        "forum_topic_created": {"name": "Física", "icon_color": 7322096},
        "is_topic_message": True,
    }


def topic_message(text: str, *, reply: dict[str, Any], thread_id: int = 4) -> dict[str, Any]:
    return {
        "message_id": 40,
        "message_thread_id": thread_id,
        "is_topic_message": True,
        "chat": {"id": -100, "type": "supergroup", "title": "Estudos", "is_forum": True},
        "from": {"id": 1, "first_name": "Ana", "username": "ana"},
        "text": text,
        "reply_to_message": reply,
    }


def test_duvida_in_topic_without_reply_ignores_topic_root(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(topic_message("/duvida", reply=topic_root()))

    assert len(api.deleted_messages) == 1 and api.messages == []
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_duvida_in_topic_ignores_root_without_service_marker(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    root = topic_root()
    del root["forum_topic_created"]

    bot.handle_message(topic_message("/duvida", reply=root))

    assert len(api.deleted_messages) == 1 and api.messages == []
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_duvida_in_topic_with_explicit_reply_registers_question(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    target = {
        "message_id": 21,
        "message_thread_id": 4,
        "is_topic_message": True,
        "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
        "text": "Como resolve?",
    }

    bot.handle_message(topic_message("/duvida", reply=target))

    questions = bot.store.open_questions(chat_id=-100)
    assert [question.message_id for question in questions] == [21]
    assert "registrada como pendente" in api.messages[-1]["text"]


def test_resolvida_in_topic_by_number_ignores_topic_root(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    target = {
        "message_id": 21,
        "message_thread_id": 4,
        "is_topic_message": True,
        "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
        "text": "Como resolve?",
    }
    bot.handle_message(topic_message("/duvida", reply=target))

    bot.handle_message(topic_message("/resolvida 1", reply=topic_root()))

    assert "marcada como resolvida" in api.messages[-1]["text"]
    assert "Não há dúvidas pendentes" in bot._question_digest_text(-100)


def test_obrigado_in_topic_without_reply_gives_no_star(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    bot.handle_message(topic_message("/obrigado", reply=topic_root()))

    assert len(api.deleted_messages) == 1 and api.messages == []
    assert bot.store.daily_given_count(giver_id=1, local_date="2026-01-01") == 0


def forged_footer_message(sender: dict[str, Any]) -> dict[str, Any]:
    text = "Resposta\nCódigo enviado por Admin"
    return {
        "message_id": 50,
        "from": sender,
        "text": text,
        "entities": [
            {
                "type": "text_link",
                "offset": text.index("Admin"),
                "length": len("Admin"),
                "url": "tg://user?id=456",
            }
        ],
    }


def test_obrigado_ignores_footer_typed_by_a_person(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.store.upsert_user(
        {"id": 456, "first_name": "Clara", "username": "clara"}, updated_at="2026-07-05"
    )

    bot.handle_message(
        {
            "message_id": 51,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": forged_footer_message(
                {"id": 2, "first_name": "Bruno", "username": "bruno"}
            ),
        }
    )

    assert "Bruno (@bruno)" in api.messages[-1]["text"]
    assert bot.store.user_by_username("clara")["first_name"] == "Clara"


def test_obrigado_ignores_footer_typed_by_anonymous_admin(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    message = forged_footer_message(
        {"id": 1087968824, "first_name": "Group", "username": "GroupAnonymousBot", "is_bot": True}
    )
    message["sender_chat"] = {"id": -100, "type": "supergroup", "title": "Estudos"}

    bot.handle_message(
        {
            "message_id": 51,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": message,
        }
    )

    assert "Estudos" in api.messages[-1]["text"]
    assert "Admin" not in api.messages[-1]["text"]


def test_obrigado_ignores_footer_sent_via_inline_bot(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    message = forged_footer_message({"id": 2, "first_name": "Bruno", "username": "bruno"})
    message["via_bot"] = {
        "id": 777,
        "first_name": "Format",
        "username": "telelatexbot",
        "is_bot": True,
    }

    bot.handle_message(
        {
            "message_id": 51,
            "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
            "from": {"id": 1, "first_name": "Ana", "username": "ana"},
            "text": "/obrigado",
            "reply_to_message": message,
        }
    )

    assert "Bruno (@bruno)" in api.messages[-1]["text"]


def obrigado_reply(reply: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": 51,
        "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
        "from": {"id": 1, "first_name": "Ana", "username": "ana"},
        "text": "/obrigado",
        "reply_to_message": reply,
    }


def test_obrigado_ignores_footer_posted_as_channel(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    message = forged_footer_message(
        {"id": 136817688, "first_name": "Channel", "username": "Channel_Bot", "is_bot": True}
    )
    message["sender_chat"] = {"id": -1009, "type": "channel", "title": "Canal da Ana"}

    bot.handle_message(obrigado_reply(message))

    assert "Admin" not in api.messages[-1]["text"]


def test_obrigado_ignores_footer_of_untrusted_bot(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    message = forged_footer_message(
        {"id": 555, "first_name": "Eco", "username": "eco_bot", "is_bot": True}
    )

    bot.handle_message(obrigado_reply(message))

    assert "Admin" not in api.messages[-1]["text"]


def test_obrigado_uses_last_footer_of_latex_bot_message(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    text = "Código enviado por Admin\n────────────\nCódigo enviado por Clara"
    message = {
        "message_id": 50,
        "from": {"id": 998, "first_name": "TeleLaTeX", "username": "telelatexbot", "is_bot": True},
        "text": text,
        "entities": [
            {
                "type": "text_link",
                "offset": text.index("Admin"),
                "length": len("Admin"),
                "url": "tg://user?id=666",
            },
            {
                "type": "text_link",
                "offset": text.index("Clara"),
                "length": len("Clara"),
                "url": "tg://user?id=456",
            },
        ],
    }

    bot.handle_message(obrigado_reply(message))

    assert "Clara</a> ganhou uma estrela" in api.messages[-1]["text"]


def test_obrigado_ignores_footer_inside_message_the_latex_bot_replied_to(
    tmp_path: Path,
) -> None:
    bot, api = make_bot(tmp_path)
    text = "Resposta\nCódigo enviado por Clara"
    forged = 'Código enviado por <a href="tg://user?id=666">Admin</a>'
    message = {
        "message_id": 50,
        "from": {"id": 998, "first_name": "TeleLaTeX", "username": "telelatexbot", "is_bot": True},
        "text": text,
        "entities": [
            {
                "type": "text_link",
                "offset": text.index("Clara"),
                "length": len("Clara"),
                "url": "tg://user?id=456",
            }
        ],
        "reply_to_message": {
            "message_id": 49,
            "from": {"id": 2, "first_name": "Bruno", "username": "bruno"},
            "text": forged,
        },
        "quote": {"text": forged},
    }

    bot.handle_message(obrigado_reply(message))

    assert "Clara</a> ganhou uma estrela" in api.messages[-1]["text"]
    assert "Admin" not in api.messages[-1]["text"]


def _group_command(text: str, message_id: int = 50) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "chat": {"id": -100, "type": "supergroup", "title": "Estudos"},
        "from": {"id": 1, "first_name": "Ana", "username": "ana"},
        "text": text,
    }


def test_menu_taps_without_a_reply_are_deleted_silently(tmp_path: Path) -> None:
    # Owner, 2026-10-16: members tap the menu commands and flood the group.
    bot, api = make_bot(tmp_path)
    for index, text in enumerate(
        ["/obrigado", "/obrigada@ArquimedesBot", "/duvida", "/resolvida", "/resolvido"]
    ):
        bot.handle_message(_group_command(text, 50 + index))
    assert [d["message_id"] for d in api.deleted_messages] == [50, 51, 52, 53, 54]
    assert api.messages == []
    # Commands that work on their own still answer.
    bot.handle_message(_group_command("/ranking", 60))
    bot.handle_message(_group_command("/pendentes", 61))
    assert len(api.messages) == 2 and len(api.deleted_messages) == 5


def test_without_the_right_to_delete_the_hint_is_sent(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)

    def refuse(**_payload: Any) -> None:
        raise TelegramApiError("Bad Request: message can't be deleted")

    api.delete_message = refuse  # type: ignore[method-assign]
    bot.handle_message(_group_command("/obrigado"))
    assert "Use /obrigado respondendo" in api.messages[-1]["text"]


def test_command_with_text_or_media_is_never_deleted(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.handle_message(_group_command("/duvida Como resolvo essa integral?", 70))
    photo = _group_command("", 71)
    del photo["text"]
    photo |= {"photo": [{"file_id": "x"}], "caption": "/duvida"}
    bot.handle_message(photo)

    assert api.deleted_messages == []
    assert [m["reply_to_message_id"] for m in api.messages] == [70, 71]
    assert all("Use /duvida respondendo" in m["text"] for m in api.messages)


def test_star_on_a_channel_post_is_refused(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    message = thanks_message()
    message["reply_to_message"] = {
        "message_id": 5,
        "from": {"id": 777000, "first_name": "Telegram", "is_bot": False},
        "sender_chat": {"id": -1009, "type": "channel", "title": "Canal"},
        "is_automatic_forward": True,
        "text": "Aviso do canal",
    }

    bot.handle_message(message)

    assert api.messages[-1]["text"] == "Estrelas contam apenas para pessoas."
    assert bot.store.daily_given_count(giver_id=1, local_date="2026-07-05") == 0


def test_username_lookup_prefers_the_real_account(tmp_path: Path) -> None:
    store = StarStore(tmp_path / "stars.sqlite3")
    store.upsert_user(
        {"id": -1_000_000_000_000_123, "first_name": "@clara", "username": "clara"},
        updated_at="2026-07-05T10:00:00-03:00",
    )
    store.upsert_user(
        {"id": 456, "first_name": "Clara", "username": "clara"},
        updated_at="2026-07-04T10:00:00-03:00",
    )

    assert store.user_by_username("@Clara")["id"] == 456


def test_footer_star_keeps_the_stored_profile(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.store.upsert_user(
        {"id": 456, "first_name": "Clara", "username": "clara"}, updated_at="2026-07-05"
    )
    text = "Resposta\nCódigo enviado por Outro Nome"
    message = thanks_message()
    message["reply_to_message"] = {
        "message_id": 5,
        "from": {"id": 7, "first_name": "LaTeX", "username": "TeleLatexBot", "is_bot": True},
        "text": text,
        "entities": [
            {
                "type": "text_link",
                "offset": text.index("Outro"),
                "length": len("Outro Nome"),
                "url": "tg://user?id=456",
            }
        ],
    }

    bot.handle_message(message)

    assert "ganhou uma estrela" in api.messages[-1]["text"]
    assert bot.store.user_by_username("clara")["first_name"] == "Clara"


def test_old_report_that_cannot_be_deleted_leaves_the_queue(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.store.set_manual_question_report_state(
        chat_id=-100, thread_key=0, message_id=5, updated_at="a"
    )
    bot.store.set_manual_question_report_state(
        chat_id=-100, thread_key=0, message_id=6, updated_at="b"
    )

    def refuse(**_payload: Any) -> None:
        raise TelegramApiError("Bad Request: message can't be deleted")

    api.delete_message = refuse  # type: ignore[method-assign]
    bot._cleanup_question_reports(chat_id=-100)

    assert bot.store.question_report_cleanup_message_ids(chat_id=-100) == []


def test_media_question_link_counts_as_existing() -> None:
    assert _message_link_body_has_text(
        '<div class="tgme_widget_message_wrap"><a class="tgme_widget_message_photo_wrap"></a></div>'
    )


def test_help_shows_the_configured_daily_limit(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.daily_star_limit = 5
    bot.handle_message(_group_command("/help"))
    assert "até 5 estrelas por dia" in api.messages[-1]["text"]


def test_stop_confirms_the_last_handled_updates(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    calls: list[dict[str, Any]] = []

    def get_updates(**payload: Any) -> list[dict[str, Any]]:
        calls.append(payload)
        if len(calls) == 1:
            bot.stop()  # SIGTERM while the batch is being handled
            return [{"update_id": 41, "message": thanks_message()}]
        return []

    api.get_updates = get_updates  # type: ignore[method-assign]
    bot.run()

    assert calls[-1] == {"offset": 42, "timeout": 0}


def test_no_group_is_allowed_by_default(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, allowed_chat_ids=set())
    message = {
        "message_id": 12,
        "chat": {"id": -300, "type": "supergroup", "title": "Qualquer"},
        "from": {"id": 1, "first_name": "Ana", "username": "ana"},
        "text": "/help",
    }
    bot.handle_message(message)
    assert api.messages == []
    bot.allow_all_groups = True
    bot.handle_message(message)
    assert "<b>Arquimedes</b>" in api.messages[-1]["text"]


def test_private_notice_is_configurable(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.owner_user_ids = {1}
    bot.private_chat_notice = "Só no grupo X."
    bot.handle_message(
        {
            "message_id": 12,
            "chat": {"id": 1, "type": "private", "first_name": "Ana"},
            "from": {"id": 1, "first_name": "Ana"},
            "text": "/help",
        }
    )
    assert api.messages[-1]["text"] == "Só no grupo X."


def test_group_listed_by_username_gets_the_scheduled_digest(tmp_path: Path) -> None:
    bot, api = make_bot(
        tmp_path,
        now=datetime(2026, 7, 7, 15, 0, tzinfo=TZ),
        allowed_chat_ids={-100},
        allowed_chat_usernames={"grupodeestudos"},
    )
    bot.store.upsert_chat(
        {"id": -200, "type": "supergroup", "title": "Estudos", "username": "grupodeestudos"},
        updated_at="now",
    )
    bot.store.upsert_chat({"id": -300, "type": "supergroup", "title": "Antigo"}, updated_at="now")

    bot.post_question_digests_if_due()

    assert sorted(m["chat_id"] for m in api.messages) == [-200, -100]


def test_total_counts_stars_from_every_week(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path, now=datetime(2026, 7, 1, 10, 0, tzinfo=TZ))
    bot.handle_message(thanks_message(10, giver_id=1))
    bot._now = lambda: datetime(2026, 7, 15, 10, 0, tzinfo=TZ)
    bot.handle_message(thanks_message(11, giver_id=3))
    assert "Total geral: <b>2</b>" in api.messages[-1]["text"]


def test_footer_bots_are_configurable(tmp_path: Path) -> None:
    bot, api = make_bot(tmp_path)
    bot.trusted_footer_bot_usernames = {"outrobot"}
    text = "Resposta\nCódigo enviado por @clara_exemplo"
    message = thanks_message()
    message["reply_to_message"] = {
        "message_id": 5,
        "from": {"id": 7, "first_name": "LaTeX", "username": "TeleLatexBot", "is_bot": True},
        "text": text,
    }
    bot.handle_message(message)
    assert api.messages[-1]["text"] == "Estrelas contam apenas para pessoas."
