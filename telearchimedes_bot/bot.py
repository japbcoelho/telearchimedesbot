from __future__ import annotations

import html
import logging
import re
import threading
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import blake2s
from typing import Any

from . import config
from .api import TelegramApi, TelegramApiError
from .store import PendingQuestion, RankingEntry, StarStore


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

GROUP_TYPES = {"group", "supergroup"}
THANK_COMMANDS = {"obrigado", "obrigada"}
QUESTION_COMMANDS = {"duvida", "dúvida"}
RESOLVED_COMMANDS = {"resolvida", "resolvido"}
PENDING_QUESTIONS_COMMANDS = {"pendentes"}
BOT_COMMANDS = [
    {"command": "obrigado", "description": "Dar estrela respondendo a quem ajudou"},
    {"command": "obrigada", "description": "Dar estrela respondendo a quem ajudou"},
    {"command": "duvida", "description": "Marcar uma mensagem como dúvida pendente"},
    {"command": "pendentes", "description": "Listar dúvidas pendentes"},
    {"command": "resolvida", "description": "Marcar uma dúvida como resolvida"},
    {"command": "resolvido", "description": "Marcar uma dúvida como resolvida"},
    {"command": "ranking", "description": "Ver o ranking global de estrelas"},
    {"command": "help", "description": "Ver os comandos do Arquimedes"},
    {"command": "start", "description": "Ver os comandos do Arquimedes"},
]
THANKS_REMINDER_RE = re.compile(
    r"\b(vlw|valeu|obg|obrigad[ao]s?|obrigadao|brigad[ao]s?|agradeco)\b"
)
THANKS_REMINDER_TEXT = (
    "Recebeu a ajuda que queria? Responda à mensagem de quem te ajudou com "
    "/obrigado para dar uma estrela e contar pontos no ranking."
)
LATEX_FOOTER_MARKERS = (
    "Código enviado por",
    "Mensagem respondida por:",
)
GROUP_ANONYMOUS_BOT_ID = 1087968824
GROUP_ANONYMOUS_BOT_USERNAME = "groupanonymousbot"
QUESTION_PREVIEW_LENGTH = 20
SCHEDULED_POST_RETRY_DELAY = timedelta(minutes=5)


class ArchimedesBot:
    def __init__(
        self,
        api: TelegramApi,
        store: StarStore,
        *,
        bot_username: str | None = None,
        bot_user_id: int | None = None,
        allowed_chat_ids: set[int] | None = None,
        allowed_chat_usernames: set[str] | None = None,
        allow_all_groups: bool = False,
        owner_user_ids: set[int] | None = None,
        private_chat_notice: str = config.DEFAULT_PRIVATE_CHAT_NOTICE,
        trusted_footer_bot_usernames: set[str] | None = None,
        daily_star_limit: int = 3,
        weekly_ranking_weekday: int = 6,
        weekly_ranking_hour: int = 20,
        weekly_ranking_minute: int = 0,
        question_digest_thread_id: int = 0,
        question_digest_hours: tuple[int, ...] = (15,),
        message_link_exists: Callable[[str], bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.api = api
        self.store = store
        self.offset: int | None = None
        self.bot_username = _normalize_username(bot_username)
        self.bot_user_id = bot_user_id
        # Only listed groups are served; every group only on explicit opt-in.
        self.allowed_chat_ids = allowed_chat_ids or set()
        self.allowed_chat_usernames = {
            name for name in map(_normalize_username, allowed_chat_usernames or ()) if name
        }
        self.allow_all_groups = allow_all_groups
        self.owner_user_ids = owner_user_ids or set()
        self.private_chat_notice = private_chat_notice
        # Bots whose author footer is trusted; Telegram sets a message's sender,
        # so it cannot be forged, unlike via_bot results or posts sent as a channel.
        self.trusted_footer_bot_usernames = {
            name
            for name in map(
                _normalize_username,
                {config.DEFAULT_TRUSTED_FOOTER_BOT_USERNAMES}
                if trusted_footer_bot_usernames is None
                else trusted_footer_bot_usernames,
            )
            if name
        }
        self.daily_star_limit = daily_star_limit
        self.weekly_ranking_weekday = weekly_ranking_weekday
        self.weekly_ranking_hour = weekly_ranking_hour
        self.weekly_ranking_minute = weekly_ranking_minute
        self.question_digest_thread_id = question_digest_thread_id
        self.question_digest_hours = tuple(sorted(set(question_digest_hours)))
        self._message_link_exists = message_link_exists or _message_link_exists
        self._now = now or (lambda: datetime.now(config.TIMEZONE))
        self._stop_requested = threading.Event()
        # Scheduled posts that failed wait before the next try, instead of
        # retrying (and re-checking every question link) on each poll.
        self._retry_after: dict[tuple[str, int], datetime] = {}

    def stop(self) -> None:
        self._stop_requested.set()

    def run(self) -> None:
        me = self.api.get_me()
        self.bot_username = _normalize_username(me.get("username"))
        self.bot_user_id = me.get("id") if isinstance(me.get("id"), int) else None
        try:
            self.api.set_my_commands(BOT_COMMANDS)
        except TelegramApiError:
            logger.warning("Could not set bot commands", exc_info=True)
        logger.info("Bot started as @%s", self.bot_username or "bot")
        if not (self.allow_all_groups or self.allowed_chat_ids or self.allowed_chat_usernames):
            logger.warning(
                "No group is allowed: set ALLOWED_CHAT_IDS, ALLOWED_CHAT_USERNAMES "
                "or ALLOW_ALL_GROUPS=1"
            )

        try:
            while not self._stop_requested.is_set():
                try:
                    self.post_weekly_rankings_if_due()
                    self.post_question_digests_if_due()
                    updates = self.api.get_updates(offset=self.offset, timeout=30)
                    for update in updates:
                        self.offset = update["update_id"] + 1
                        self.handle_update(update)
                    if updates:
                        self.offset = updates[-1]["update_id"] + 1
                except TelegramApiError:
                    logger.exception("Bot API error")
                    self._stop_requested.wait(3)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    logger.exception("Unexpected polling error")
                    self._stop_requested.wait(3)
        finally:
            self._confirm_handled_updates()
            self._close()

    def _confirm_handled_updates(self) -> None:
        """Tell Telegram the last batch was handled before exiting.

        getUpdates confirms a batch only on the next call with a higher offset;
        without this, a restart would hand the last batch back and give its
        stars, questions and replies twice.
        """
        if self.offset is None:
            return
        try:
            self.api.get_updates(offset=self.offset, timeout=0)
        except Exception:
            logger.warning("Could not confirm the last handled updates", exc_info=True)

    def _close(self) -> None:
        api_close = getattr(self.api, "close", None)
        if callable(api_close):
            api_close()
        self.store.close()
        logger.info("Bot stopped")

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if isinstance(message, dict):
            self.handle_message(message)

    def handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        now = self._now()
        now_iso = now.isoformat(timespec="seconds")

        if not self._chat_allowed(chat):
            # Other groups get no reply at all. In private only the owners are told
            # where it works; anyone else gets silence (owner, 2026-10-16).
            sender_id = (message.get("from") or {}).get("id")
            if chat.get("type") == "private" and sender_id in self.owner_user_ids:
                self._reply(message, self.private_chat_notice)
            return
        if not isinstance(chat_id, int):
            return

        self.store.upsert_chat(chat, updated_at=now_iso)
        sender = message.get("from")
        if isinstance(sender, dict):
            self.store.upsert_user(sender, updated_at=now_iso)

        command = _command_name(_message_text(message), self.bot_username)
        if command in THANK_COMMANDS:
            self._handle_thanks(message, now)
        elif command in QUESTION_COMMANDS:
            self._handle_question(message, now)
        elif command in RESOLVED_COMMANDS:
            self._handle_resolved_question(message, now)
        elif command in PENDING_QUESTIONS_COMMANDS:
            self._handle_pending_questions(message, now)
        elif command == "ranking":
            self._send_all_time_ranking(chat_id, message=message)
        elif command in {"start", "help"}:
            self._reply(message, help_text(self.daily_star_limit), parse_mode="HTML")
        elif _looks_like_thanks(_message_text(message)):
            self._send_rotating_reminder(message, now)

    def _handle_thanks(self, message: dict[str, Any], now: datetime) -> None:
        chat_id = _chat_id(message)
        giver = message.get("from")
        reply = _explicit_reply(message)
        receiver = self._latex_footer_receiver(reply, message=message)
        if (
            receiver is None
            and isinstance(reply, dict)
            and self._from_trusted_footer_bot(reply)
            and _latex_footer_label(reply)
        ):
            self._reply(
                message,
                "Não consegui identificar quem enviou esse código: "
                "o rodapé não tem link para o perfil nem @usuário.",
            )
            return
        if reply is None:
            self._delete_empty_command(
                message, "Use /obrigado respondendo à mensagem de quem ajudou."
            )
            return
        from_footer = receiver is not None
        if receiver is None:
            if _posted_by_other_chat(reply, chat_id=chat_id):
                # A channel post (such as the linked channel's automatic
                # forward, whose sender is Telegram's service account 777000).
                self._reply(message, "Estrelas contam apenas para pessoas.")
                return
            receiver = _thanks_receiver(reply, chat_id=chat_id)

        if chat_id is None or not isinstance(giver, dict) or not isinstance(receiver, dict):
            self._reply(message, "Use /obrigado respondendo à mensagem de quem ajudou.")
            return

        giver_id = giver.get("id")
        receiver_id = receiver.get("id")
        if not isinstance(giver_id, int) or not isinstance(receiver_id, int):
            return
        if receiver_id == giver_id:
            self._reply(message, "Você não pode dar estrela para si mesmo.")
            return
        if receiver.get("is_bot"):
            self._reply(message, "Estrelas contam apenas para pessoas.")
            return

        local_date = now.date().isoformat()
        given_today = self.store.daily_given_count(giver_id=giver_id, local_date=local_date)
        if given_today >= self.daily_star_limit:
            self._reply(message, f"Você já deu suas {self.daily_star_limit} estrelas de hoje.")
            return

        now_iso = now.isoformat(timespec="seconds")
        if from_footer:
            # The footer has only a link label, not the full profile: keep the
            # stored name and @username when the person is already known.
            self.store.ensure_user(receiver, updated_at=now_iso)
        else:
            self.store.upsert_user(receiver, updated_at=now_iso)
        self.store.add_star(
            chat_id=chat_id,
            giver_id=giver_id,
            receiver_id=receiver_id,
            created_at=now_iso,
            local_date=local_date,
            week_key=_week_key(now),
        )
        total = self.store.receiver_total(chat_id=chat_id, receiver_id=receiver_id)
        remaining = self.daily_star_limit - given_today - 1
        remaining_label = "<b>uma</b> estrela" if remaining == 1 else f"<b>{remaining}</b> estrelas"
        remaining_text = (
            "Sua cota diária de estrelas acabou; será renovada amanhã."
            if remaining == 0
            else f"Você ainda pode dar {remaining_label} hoje."
        )
        self._reply(
            message,
            (
                f"⭐ {_user_html(receiver)} ganhou uma estrela de {_user_html(giver)}.\n"
                f"Total geral: <b>{total}</b>. {remaining_text}"
            ),
            parse_mode="HTML",
        )

    def _latex_footer_receiver(
        self,
        reply: Any,
        *,
        message: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not isinstance(reply, dict):
            return None

        chat = message.get("chat")
        chat = chat if isinstance(chat, dict) else {}
        chat_id = _chat_id(message)
        # Only the LaTeX bot writes a trustworthy author footer; a person could
        # type one to redirect the star or rename another user.
        if not self._from_trusted_footer_bot(reply):
            return None
        author = _latex_footer_author(reply)
        label = _latex_footer_label(reply)

        if isinstance(author, dict):
            if _is_group_anonymous_user(author):
                return _chat_identity(chat)

            user_id = author.get("id")
            if isinstance(user_id, int):
                if author.get("is_bot"):
                    return None
                return _footer_user(author, fallback_label=label)

            username = _normalize_username(author.get("username"))
        else:
            username = _footer_username(label or "")

        # A bare display name is not an identity: anyone can rename themselves
        # to match another member, so only a profile link or @username counts.
        if not username:
            return None
        if username == _normalize_username(chat.get("username")):
            return _chat_identity(chat)
        known_user = self.store.user_by_username(username)
        if known_user is not None:
            return known_user
        return _synthetic_footer_user(username, chat_id=chat_id)

    def _handle_question(self, message: dict[str, Any], now: datetime) -> None:
        chat = message.get("chat") or {}
        chat_id = _chat_id(message)
        target = _explicit_reply(message)
        if target is None:
            self._delete_empty_command(
                message,
                "Use /duvida respondendo à mensagem com o problema ainda não resolvido.",
            )
            return
        if (
            _message_from_this_bot(
                target,
                bot_username=self.bot_username,
                bot_user_id=self.bot_user_id,
            )
            or (
                _message_from_bot(target)
                and _anonymous_chat_sender(target, chat_id=chat_id) is None
            )
            or _looks_like_archimedes_question_message(target)
        ):
            self._reply(
                message,
                "Use /duvida na mensagem original do problema ainda não resolvido, não em mensagens de bots.",
            )
            return
        command_description = _payload_after_command(_message_text(message))
        preview = _text_preview(command_description) or _message_preview(target)
        if not preview:
            self._reply(
                message,
                "Use /duvida respondendo a uma mensagem com texto, imagem ou outro conteúdo.",
            )
            return
        target_message_id = target.get("message_id")
        if chat_id is None or not isinstance(target_message_id, int):
            return

        author = _message_sender(target)
        if isinstance(author, dict):
            self.store.upsert_user(author, updated_at=now.isoformat(timespec="seconds"))

        question = self.store.add_or_reopen_question(
            chat_id=chat_id,
            thread_key=_thread_key(target) or _thread_key(message),
            message_id=target_message_id,
            author=author,
            preview=preview,
            link=_message_link(chat, target_message_id),
            created_at=now.isoformat(timespec="seconds"),
        )
        number = _question_number(
            self.store.open_questions(chat_id=chat_id), question_id=question.id
        )
        self._reply(
            message,
            f"✅ Dúvida {_question_id_html(question, number=number)} registrada como pendente.",
            parse_mode="HTML",
        )

    def _handle_resolved_question(self, message: dict[str, Any], now: datetime) -> None:
        chat_id = _chat_id(message)
        if chat_id is None:
            return

        closed_at = now.isoformat(timespec="seconds")
        reply = _explicit_reply(message)
        question = None
        question_number = None
        if reply is not None and isinstance(reply.get("message_id"), int):
            question_number = _question_number(
                self.store.open_questions(chat_id=chat_id),
                message_id=reply["message_id"],
            )
            question = self.store.close_question_by_message(
                chat_id=chat_id,
                message_id=reply["message_id"],
                closed_at=closed_at,
            )
        else:
            question_number = _question_number_from_command(_message_text(message))
            if question_number is None:
                self._delete_empty_command(
                    message,
                    "Use /resolvida respondendo à dúvida, ou /resolvida #2 pelo número.",
                )
                return
            if question_number is not None:
                question = self.store.close_question_by_position(
                    chat_id=chat_id,
                    position=question_number,
                    closed_at=closed_at,
                )

        if question is None:
            self._reply(message, "Não encontrei essa dúvida pendente.")
            return

        self._reply(
            message,
            f"✅ Dúvida {_question_id_html(question, number=question_number)} marcada como resolvida.",
            parse_mode="HTML",
        )

    def _handle_pending_questions(self, message: dict[str, Any], now: datetime) -> None:
        chat_id = _chat_id(message)
        if chat_id is None:
            return
        updated_at = now.isoformat(timespec="seconds")
        self._close_missing_questions(chat_id=chat_id, closed_at=updated_at)
        sent = self.api.send_message(
            chat_id=chat_id,
            text=self._question_digest_text(chat_id),
            parse_mode="HTML",
            message_thread_id=_message_thread_id(message),
        )
        if isinstance(sent, dict) and isinstance(sent.get("message_id"), int):
            self.store.set_manual_question_report_state(
                chat_id=chat_id,
                thread_key=_thread_key(message),
                message_id=sent["message_id"],
                updated_at=updated_at,
            )
            self._cleanup_question_reports(chat_id=chat_id)

    def post_weekly_rankings_if_due(self) -> None:
        now = self._now()
        if not _weekly_time_reached(
            now,
            weekday=self.weekly_ranking_weekday,
            hour=self.weekly_ranking_hour,
            minute=self.weekly_ranking_minute,
        ):
            return

        week_key = _week_key(now)
        for chat_id in self._scheduled_chat_ids():
            if self.store.weekly_posted(chat_id=chat_id, week_key=week_key):
                continue
            if self._waiting_to_retry("ranking", chat_id, now):
                continue
            try:
                self.api.send_message(
                    chat_id=chat_id,
                    text=self._all_time_ranking_text(chat_id),
                    parse_mode="HTML",
                )
            except TelegramApiError:
                logger.warning("Could not send global ranking to %s", chat_id, exc_info=True)
                self._retry_later("ranking", chat_id, now)
                continue
            self.store.mark_weekly_posted(
                chat_id=chat_id,
                week_key=week_key,
                posted_at=now.isoformat(timespec="seconds"),
            )

    def post_question_digests_if_due(self) -> None:
        now = self._now()
        slot_key = _question_digest_slot_key(now, self.question_digest_hours)
        if slot_key is None:
            return

        thread_key = self.question_digest_thread_id or 0
        message_thread_id = self.question_digest_thread_id or None
        for chat_id in self._scheduled_chat_ids():
            self._cleanup_question_reports(chat_id=chat_id)
            state = self.store.question_digest_state(chat_id=chat_id, thread_key=thread_key)
            if state is not None and state[0] == slot_key:
                continue
            if self._waiting_to_retry("digest", chat_id, now):
                continue

            self._close_missing_questions(
                chat_id=chat_id, closed_at=now.isoformat(timespec="seconds")
            )
            try:
                sent = self.api.send_message(
                    chat_id=chat_id,
                    text=self._question_digest_text(chat_id),
                    parse_mode="HTML",
                    message_thread_id=message_thread_id,
                )
            except TelegramApiError:
                logger.warning("Could not send question digest to %s", chat_id, exc_info=True)
                self._retry_later("digest", chat_id, now)
                continue
            if isinstance(sent, dict) and isinstance(sent.get("message_id"), int):
                self.store.set_question_digest_state(
                    chat_id=chat_id,
                    thread_key=thread_key,
                    slot_key=slot_key,
                    message_id=sent["message_id"],
                    updated_at=now.isoformat(timespec="seconds"),
                )
                self._cleanup_question_reports(chat_id=chat_id)

    def _waiting_to_retry(self, kind: str, chat_id: int, now: datetime) -> bool:
        retry_at = self._retry_after.get((kind, chat_id))
        return retry_at is not None and now < retry_at

    def _retry_later(self, kind: str, chat_id: int, now: datetime) -> None:
        self._retry_after[(kind, chat_id)] = now + SCHEDULED_POST_RETRY_DELAY

    def _send_all_time_ranking(
        self,
        chat_id: int,
        *,
        message: dict[str, Any],
    ) -> None:
        self.api.send_message(
            chat_id=chat_id,
            text=self._all_time_ranking_text(chat_id),
            parse_mode="HTML",
            message_thread_id=_message_thread_id(message),
            reply_to_message_id=message.get("message_id"),
        )

    def _all_time_ranking_text(self, chat_id: int) -> str:
        ranking = self.store.all_time_ranking(chat_id=chat_id, limit=10)
        return format_ranking(ranking, title="Ranking global")

    def _question_digest_text(self, chat_id: int) -> str:
        questions = self.store.open_questions(chat_id=chat_id)
        return format_questions_digest(questions)

    def _close_missing_questions(self, *, chat_id: int, closed_at: str) -> None:
        for question in self.store.open_questions(chat_id=chat_id):
            if not question.link:
                continue
            try:
                exists = self._message_link_exists(question.link)
            except Exception:
                logger.warning(
                    "Could not check pending question link %s", question.link, exc_info=True
                )
                continue
            if not exists:
                self.store.close_question_by_id(
                    chat_id=chat_id,
                    question_id=question.id,
                    closed_at=closed_at,
                )

    def _cleanup_question_reports(self, *, chat_id: int) -> None:
        for message_id in self.store.question_report_cleanup_message_ids(chat_id=chat_id):
            try:
                self.api.delete_message(chat_id=chat_id, message_id=message_id)
            except TelegramApiError as error:
                if _message_delete_already_missing(error):
                    pass
                elif _message_delete_refused(error):
                    # Too old or no right to delete: retrying cannot succeed,
                    # so stop trying instead of calling Telegram on every poll.
                    logger.info(
                        "Giving up on deleting old question report %s: %s", message_id, error
                    )
                else:
                    logger.info(
                        "Could not delete previous question report %s",
                        message_id,
                        exc_info=True,
                    )
                    continue
            self.store.mark_question_report_cleaned(
                chat_id=chat_id,
                message_id=message_id,
            )

    def _delete_empty_command(self, message: dict[str, Any], hint: str) -> None:
        """Delete a command with nothing to act on, sent without the reply it
        needs (owner, 2026-10-16: members tap the menu commands and flood the
        group). The bot needs the right to delete messages; without it, the
        person gets the usual hint instead."""
        chat_id = _chat_id(message)
        message_id = message.get("message_id")
        if chat_id is None or not isinstance(message_id, int):
            return
        if not _is_bare_command(message):
            # Text after the command, or a photo or file with the command as
            # caption, is the person's content: never delete it.
            self._reply(message, hint)
            return
        try:
            self.api.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramApiError as error:
            if _message_delete_already_missing(error):
                return
            logger.info("Could not delete an empty command %s", message_id, exc_info=True)
            self._reply(message, hint)

    def _reply(
        self,
        message: dict[str, Any],
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> None:
        chat_id = _chat_id(message)
        if chat_id is None:
            return
        self.api.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            message_thread_id=_message_thread_id(message),
            reply_to_message_id=message.get("message_id"),
        )

    def _send_rotating_reminder(self, message: dict[str, Any], now: datetime) -> None:
        chat_id = _chat_id(message)
        if chat_id is None:
            return

        thread_key = _thread_key(message)
        previous_id = self.store.reminder_message_id(chat_id=chat_id, thread_key=thread_key)
        if previous_id is not None:
            try:
                self.api.delete_message(chat_id=chat_id, message_id=previous_id)
            except TelegramApiError:
                logger.info("Could not delete previous reminder %s", previous_id, exc_info=True)

        sent = self.api.send_message(
            chat_id=chat_id,
            text=THANKS_REMINDER_TEXT,
            message_thread_id=_message_thread_id(message),
            reply_to_message_id=message.get("message_id"),
        )
        if isinstance(sent, dict) and isinstance(sent.get("message_id"), int):
            self.store.set_reminder_message(
                chat_id=chat_id,
                thread_key=thread_key,
                message_id=sent["message_id"],
                updated_at=now.isoformat(timespec="seconds"),
            )

    def _from_trusted_footer_bot(self, message: dict[str, Any]) -> bool:
        return _message_from_trusted_footer_bot(message, self.trusted_footer_bot_usernames)

    def _chat_allowed(self, chat: dict[str, Any]) -> bool:
        if chat.get("type") not in GROUP_TYPES:
            return False
        return self._chat_id_or_username_allowed(chat.get("id"), chat.get("username"))

    def _chat_id_or_username_allowed(self, chat_id: Any, username: Any) -> bool:
        if self.allow_all_groups:
            return True
        return (isinstance(chat_id, int) and chat_id in self.allowed_chat_ids) or (
            _normalize_username(username) in self.allowed_chat_usernames
        )

    def _scheduled_chat_ids(self) -> list[int]:
        """Groups that get the automatic ranking and question digest."""
        chat_ids = set(self.allowed_chat_ids)
        for chat_id, username in self.store.known_chats():
            if self._chat_id_or_username_allowed(chat_id, username):
                chat_ids.add(chat_id)
        return sorted(chat_ids)


def help_text(daily_star_limit: int = 3) -> str:
    return (
        "<b>Arquimedes</b>\n\n"
        "<b>Estrelas</b>\n"
        "• Responda à mensagem de quem ajudou com /obrigado para dar 1 estrela.\n"
        f"• Cada pessoa pode dar até {daily_star_limit} estrelas por dia.\n"
        "• Se a resposta veio pelo bot de LaTeX, a estrela vai para a identidade do rodapé, "
        "mesmo sem @.\n\n"
        "<b>Dúvidas</b>\n"
        "• Responda à pergunta com /duvida para marcar como pendente. Fotos e arquivos "
        "sem legenda também são aceitos; a descrição depois do comando é opcional.\n"
        "• Use /pendentes para ver as dúvidas abertas. O # da dúvida abre o link da mensagem.\n"
        "• Responda à dúvida com /resolvida, ou use /resolvida #2 para remover pelo número mostrado.\n\n"
        "<b>Ranking</b>\n"
        "• Use /ranking para ver o ranking global acumulado."
    )


def format_ranking(ranking: list[RankingEntry], *, title: str) -> str:
    escaped_title = html.escape(title, quote=False)
    if not ranking:
        return f"🏆 <b>{escaped_title}</b>\n\nAinda ninguém recebeu estrelas."

    lines = [f"🏆 <b>{escaped_title}</b>", ""]
    medals = {1: "🥇 Ouro", 2: "🥈 Prata", 3: "🥉 Bronze"}
    for position, entry in enumerate(ranking, start=1):
        medal = medals.get(position, f"{position}.")
        lines.append(f"{medal} {_entry_html(entry)} — <b>{entry.stars}</b> ⭐")
    return "\n".join(lines)


def format_questions_digest(questions: list[PendingQuestion]) -> str:
    if not questions:
        return "✅ <b>Dúvidas pendentes</b>\n\nNão há dúvidas pendentes no momento."

    lines = ["📌 <b>Dúvidas pendentes</b>", ""]
    for number, question in enumerate(questions, start=1):
        lines.append(
            f"{_question_digest_link_html(question, number=number)} — "
            f"{_question_preview_html(question)}"
        )
    return "\n".join(lines)


def _question_id_html(question: PendingQuestion, *, number: int | None = None) -> str:
    label = f"#{number or question.id}"
    return _question_link_html(question, label)


def _question_digest_link_html(question: PendingQuestion, *, number: int) -> str:
    return _question_link_html(question, f"# dúvida {number}")


def _question_link_html(question: PendingQuestion, label: str) -> str:
    if question.link:
        return f'<a href="{html.escape(question.link, quote=True)}">{label}</a>'
    return label


def _question_preview_html(question: PendingQuestion) -> str:
    preview = re.sub(r"\s+", " ", question.preview).strip()
    if len(preview) > QUESTION_PREVIEW_LENGTH:
        preview = preview[:QUESTION_PREVIEW_LENGTH] + "..."
    return html.escape(preview or "Mensagem sem texto", quote=False)


def _entry_html(entry: RankingEntry) -> str:
    label = html.escape(entry.display_name, quote=False)
    username_label = f"@{entry.username}" if entry.username else ""
    if entry.username and entry.display_name != username_label:
        label = f"{label} (@{html.escape(entry.username, quote=False)})"
    if entry.user_id <= 0:
        return label
    return f'<a href="tg://user?id={entry.user_id}">{label}</a>'


def _user_html(user: dict[str, Any]) -> str:
    user_id = user.get("id")
    first_name = (user.get("first_name") or "").strip()
    last_name = (user.get("last_name") or "").strip()
    username = user.get("username")
    label = " ".join(part for part in (first_name, last_name) if part)
    if isinstance(username, str) and username.strip():
        username_label = "@" + username.strip().removeprefix("@")
        label = f"{label} ({username_label})" if label else username_label
    label = html.escape(label or "Usuário", quote=False)
    if isinstance(user_id, int) and user_id > 0:
        return f'<a href="tg://user?id={user_id}">{label}</a>'
    return label


def _command_name(text: str, bot_username: str | None = None) -> str | None:
    if not text.startswith("/"):
        return None
    first = text.split(maxsplit=1)[0]
    command_text = first.removeprefix("/")
    command, separator, target = command_text.partition("@")
    if separator and _normalize_username(target) != _normalize_username(bot_username):
        return None
    return command.lower() or None


def _question_number_from_command(text: str) -> int | None:
    payload = _payload_after_command(text)
    match = re.search(r"#?(\d+)", payload)
    return int(match.group(1)) if match else None


def _question_number(
    questions: list[PendingQuestion],
    *,
    question_id: int | None = None,
    message_id: int | None = None,
) -> int | None:
    for number, question in enumerate(questions, start=1):
        if question_id is not None and question.id == question_id:
            return number
        if message_id is not None and question.message_id == message_id:
            return number
    return None


def _payload_after_command(text: str) -> str:
    stripped = text.strip()
    first_line, separator, rest = stripped.partition("\n")
    parts = first_line.split(maxsplit=1)
    if len(parts) == 2:
        payload = parts[1]
        return f"{payload}\n{rest}".strip() if separator else payload.strip()
    return rest.strip() if separator else ""


def _message_text(message: dict[str, Any]) -> str:
    text = message.get("text")
    if isinstance(text, str):
        return text
    caption = message.get("caption")
    return caption if isinstance(caption, str) else ""


def _message_preview(message: dict[str, Any], *, limit: int = 80) -> str:
    text = _message_text(message) or _html_text(_message_rich_html(message))
    preview = _text_preview(text, limit=limit)
    if preview:
        return preview

    photo = message.get("photo")
    if isinstance(photo, list) and photo:
        return "Imagem"

    document = message.get("document")
    if isinstance(document, dict):
        mime_type = document.get("mime_type")
        label = (
            "Imagem"
            if isinstance(mime_type, str) and mime_type.casefold().startswith("image/")
            else "Arquivo"
        )
        file_name = document.get("file_name")
        if isinstance(file_name, str) and file_name.strip():
            return _text_preview(f"{label}: {file_name.strip()}", limit=limit)
        return label

    media_labels = (
        ("animation", "Animação"),
        ("video", "Vídeo"),
        ("video_note", "Vídeo"),
        ("audio", "Áudio"),
        ("voice", "Áudio"),
        ("sticker", "Figurinha"),
        ("poll", "Enquete"),
        ("location", "Localização"),
        ("venue", "Localização"),
        ("contact", "Contato"),
    )
    for field, label in media_labels:
        if message.get(field) is not None:
            return label
    return ""


def _text_preview(text: str, *, limit: int = 80) -> str:
    preview = re.sub(r"\s+", " ", text).strip()
    if len(preview) <= limit:
        return preview
    return preview[: limit - 1].rstrip() + "…"


def _message_entities(message: dict[str, Any]) -> list[dict[str, Any]]:
    entities = message.get("entities")
    if isinstance(entities, list):
        return entities
    caption_entities = message.get("caption_entities")
    return caption_entities if isinstance(caption_entities, list) else []


def _thanks_receiver(reply: Any, *, chat_id: int | None = None) -> dict[str, Any] | None:
    if not isinstance(reply, dict):
        return None
    return _anonymous_chat_sender(reply, chat_id=chat_id) or _message_sender(reply)


def _anonymous_chat_sender(
    message: dict[str, Any], *, chat_id: int | None
) -> dict[str, Any] | None:
    sender_chat = message.get("sender_chat")
    if not isinstance(sender_chat, dict) or sender_chat.get("id") != chat_id:
        return None
    title = sender_chat.get("title") or sender_chat.get("username") or "Grupo"
    return {"id": chat_id, "first_name": str(title), "username": None}


def _posted_by_other_chat(message: dict[str, Any], *, chat_id: int | None) -> bool:
    """Whether a channel (or another chat), not a person, posted the message."""
    sender_chat = message.get("sender_chat")
    return isinstance(sender_chat, dict) and sender_chat.get("id") != chat_id


def _is_bare_command(message: dict[str, Any]) -> bool:
    """A command alone, as a menu tap sends it: no text after it and no media."""
    text = message.get("text")
    return isinstance(text, str) and not _payload_after_command(text)


def _message_sender(message: dict[str, Any]) -> dict[str, Any] | None:
    sender = message.get("from")
    return sender if isinstance(sender, dict) else None


def _message_from_bot(message: dict[str, Any]) -> bool:
    sender = _message_sender(message)
    return bool(isinstance(sender, dict) and sender.get("is_bot"))


def _message_from_trusted_footer_bot(message: dict[str, Any], trusted: set[str]) -> bool:
    sender = _message_sender(message)
    return bool(
        isinstance(sender, dict)
        and sender.get("is_bot")
        and "sender_chat" not in message
        and _normalize_username(sender.get("username")) in trusted
    )


def _message_from_this_bot(
    message: dict[str, Any],
    *,
    bot_username: str | None,
    bot_user_id: int | None,
) -> bool:
    sender = _message_sender(message)
    if not isinstance(sender, dict):
        return False
    sender_id = sender.get("id")
    if isinstance(sender_id, int) and bot_user_id is not None and sender_id == bot_user_id:
        return True
    return _normalize_username(sender.get("username")) == _normalize_username(bot_username)


def _looks_like_archimedes_question_message(message: dict[str, Any]) -> bool:
    text = (_message_text(message) or _html_text(_message_rich_html(message))).strip()
    return text.startswith(("✅ Dúvida ", "📌 Dúvidas pendentes"))


def _latex_footer_author(message: dict[str, Any]) -> dict[str, Any] | None:
    for rich_value in (
        message.get("rich_message"),
        message.get("rich_text"),
        message.get("blocks"),
    ):
        rich_author = _rich_footer_author(rich_value)
        if rich_author is not None:
            return rich_author

    for html_text in (_message_rich_html(message), *_message_content_strings(message)):
        html_author = _latex_footer_author_from_html(html_text)
        if html_author is not None:
            return html_author

    text = _message_text(message)
    marker_index = _footer_marker_index(text)
    if marker_index is not None:
        for entity in _message_entities(message):
            entity_text = _entity_text(text, entity)
            if not entity_text:
                continue

            start = _entity_start(text, entity)
            if start is None or start < marker_index:
                continue

            if entity.get("type") == "text_mention" and isinstance(entity.get("user"), dict):
                return _footer_user(entity["user"], fallback_label=entity_text)

            if entity.get("type") == "text_link":
                user_id = _tg_user_id(entity.get("url"))
                if user_id is not None:
                    return _user_from_link_label(user_id, entity_text)

            if entity.get("type") == "mention":
                username = _footer_username(entity_text)
                if username:
                    return {
                        "first_name": entity_text,
                        "username": username,
                    }

    return _latex_footer_author_from_text(text)


def _rich_footer_author(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        # The bot appends its footer last, so the last marker wins over any
        # marker text the user may have placed in the rendered content.
        text_seen = ""
        marker_count = 0
        marker_item: int | None = None
        for index, item in enumerate(value):
            text_seen += _rich_text_plain(item)
            count = _footer_marker_count(text_seen)
            if count > marker_count:
                marker_count = count
                marker_item = index
        if marker_item is not None:
            for candidate in value[marker_item:]:
                identity = _identity_from_rich_node(candidate)
                if identity is not None:
                    return identity

        for item in reversed(value):
            identity = _rich_footer_author(item)
            if identity is not None:
                return identity
        return None

    if isinstance(value, dict):
        for key in (
            "text",
            "caption",
            "summary",
            "credit",
            "blocks",
            "items",
            "rich_message",
        ):
            if key not in value:
                continue
            identity = _rich_footer_author(value[key])
            if identity is not None:
                return identity
    return None


def _identity_from_rich_node(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        for item in value:
            identity = _identity_from_rich_node(item)
            if identity is not None:
                return identity
        return None
    if not isinstance(value, dict):
        return None

    identity_type = value.get("type")
    label = _rich_text_plain(value)
    if identity_type == "text_mention" and isinstance(value.get("user"), dict):
        return _footer_user(value["user"], fallback_label=label)

    if identity_type == "mention":
        username = _normalize_username(value.get("username"))
        if username:
            return {
                "first_name": label or f"@{username}",
                "username": username,
            }

    if identity_type in {"link", "text_link"}:
        user_id = _tg_user_id(value.get("url") or value.get("href"))
        if user_id is not None:
            return _user_from_link_label(user_id, label)

    for item in value.values():
        identity = _identity_from_rich_node(item)
        if identity is not None:
            return identity
    return None


def _latex_footer_author_from_html(html_text: str) -> dict[str, Any] | None:
    marker_index = _footer_marker_index(_html_text(html_text))
    if marker_index is None:
        return None

    marker_html_index = max(
        (
            index
            for marker in LATEX_FOOTER_MARKERS
            if (index := html_text.casefold().rfind(marker.casefold())) >= 0
        ),
        default=0,
    )
    footer_html = html_text[marker_html_index:]
    user_match = re.search(
        r"""<a\s+[^>]*href=["']tg://user\?id=(\d+)["'][^>]*>(.*?)</a>""",
        footer_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if user_match:
        label = _html_text(user_match.group(2))
        return _user_from_link_label(int(user_match.group(1)), label)

    username_match = re.search(
        r"""<a\s+[^>]*href=["']https://t\.me/([A-Za-z0-9_]{5,32})["'][^>]*>(.*?)</a>""",
        footer_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if username_match:
        return {
            "first_name": _html_text(username_match.group(2)) or f"@{username_match.group(1)}",
            "username": username_match.group(1),
        }
    return None


def _latex_footer_author_from_text(text: str) -> dict[str, Any] | None:
    footer = _text_after_footer_marker(text)
    if not footer:
        return None

    markdown_link = re.search(
        r"\[([^\]]+)\]\(tg://user\?id=(\d+)\)",
        footer,
        flags=re.IGNORECASE,
    )
    if markdown_link:
        return _user_from_link_label(int(markdown_link.group(2)), markdown_link.group(1))

    user_link = re.search(r"tg://user\?id=(\d+)", footer, flags=re.IGNORECASE)
    if user_link:
        label = _clean_footer_label(footer[: user_link.start()])
        return _user_from_link_label(int(user_link.group(1)), label)

    username = _footer_username(footer)
    if username:
        return {
            "first_name": f"@{username}",
            "username": username,
        }
    return None


def _latex_footer_label(message: dict[str, Any]) -> str | None:
    for text in (
        _rich_text_plain(message.get("rich_message")),
        _rich_text_plain(message.get("rich_text")),
        _rich_text_plain(message.get("blocks")),
        _message_text(message),
        _html_text(_message_rich_html(message)),
        *_message_content_strings(message),
    ):
        footer = _text_after_footer_marker(text)
        label = _clean_footer_label(footer)
        if label:
            return label
    return None


def _latex_footer_username(message: dict[str, Any]) -> str | None:
    return _footer_username(_latex_footer_label(message) or "")


def _rich_text_plain(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_rich_text_plain(item) for item in value)
    if not isinstance(value, dict):
        return ""

    if "text" in value:
        return _rich_text_plain(value.get("text"))
    expression = value.get("expression")
    if isinstance(expression, str):
        return expression
    caption = value.get("caption")
    if caption is not None:
        return _rich_text_plain(caption)
    for key in ("blocks", "items", "summary", "credit", "rich_message"):
        if key in value:
            return _rich_text_plain(value[key])
    return ""


def _footer_marker_index(text: str) -> int | None:
    if not isinstance(text, str):
        return None
    folded = text.casefold()
    indexes = [
        index for marker in LATEX_FOOTER_MARKERS if (index := folded.rfind(marker.casefold())) >= 0
    ]
    return max(indexes) if indexes else None


def _footer_marker_count(text: str) -> int:
    folded = text.casefold()
    return sum(folded.count(marker.casefold()) for marker in LATEX_FOOTER_MARKERS)


def _text_after_footer_marker(text: str) -> str:
    if not isinstance(text, str):
        return ""
    folded = text.casefold()
    matches = [
        (index, marker)
        for marker in LATEX_FOOTER_MARKERS
        if (index := folded.rfind(marker.casefold())) >= 0
    ]
    if not matches:
        return ""
    index, marker = max(matches, key=lambda item: item[0])
    return text[index + len(marker) :]


def _clean_footer_label(value: str) -> str:
    if not isinstance(value, str):
        return ""
    label = _html_text(value)
    label = label.strip().lstrip(":：–—-").strip()
    if not label:
        return ""

    markdown_link = re.search(r"\[([^\]]+)\]\([^)]+\)", label)
    if markdown_link:
        label = markdown_link.group(1)
    else:
        label = label.splitlines()[0].strip()
        label = re.sub(r"\s*\(?tg://user\?id=\d+\)?\s*$", "", label, flags=re.IGNORECASE)

    label = label.strip().strip("_*`").strip()
    return label[:160]


def _footer_username(label: str) -> str | None:
    match = re.search(r"@([A-Za-z0-9_]{5,32})\b", label)
    return match.group(1) if match else None


def _footer_user(user: dict[str, Any], *, fallback_label: str | None = None) -> dict[str, Any]:
    username = _normalize_username(user.get("username"))
    first_name = user.get("first_name")
    first_name = first_name.strip() if isinstance(first_name, str) and first_name.strip() else ""
    last_name = user.get("last_name")
    last_name = last_name.strip() if isinstance(last_name, str) and last_name.strip() else ""
    fallback = _clean_footer_label(fallback_label or "")
    if not first_name:
        first_name = fallback or (f"@{username}" if username else "Usuário")

    normalized: dict[str, Any] = {
        "first_name": first_name,
        "is_bot": bool(user.get("is_bot", False)),
    }
    user_id = user.get("id")
    if isinstance(user_id, int):
        normalized["id"] = user_id
    if last_name:
        normalized["last_name"] = last_name
    if username:
        normalized["username"] = username
    phone_number = user.get("phone_number")
    if isinstance(phone_number, str) and phone_number.strip():
        normalized["phone_number"] = phone_number.strip()
    return normalized


def _is_group_anonymous_user(user: dict[str, Any]) -> bool:
    return bool(
        user.get("is_bot")
        and (
            user.get("id") == GROUP_ANONYMOUS_BOT_ID
            or _normalize_username(user.get("username")) == GROUP_ANONYMOUS_BOT_USERNAME
        )
    )


def _chat_identity(chat: dict[str, Any]) -> dict[str, Any] | None:
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None
    title = chat.get("title")
    username = _normalize_username(chat.get("username"))
    label = (
        title.strip()
        if isinstance(title, str) and title.strip()
        else f"@{username}"
        if username
        else "Grupo"
    )
    return {
        "id": chat_id,
        "first_name": label,
        "is_bot": False,
    }


def _synthetic_footer_user(username: str, *, chat_id: int | None) -> dict[str, Any]:
    """Stand-in account for an @username the bot has not seen yet."""
    identity_key = "username:" + username.casefold()
    digest = int.from_bytes(
        blake2s(f"{chat_id or 0}:{identity_key}".encode("utf-8"), digest_size=7).digest(),
        "big",
    )
    return {
        "id": -(1_000_000_000_000_000 + digest),
        "first_name": f"@{username}",
        "username": username,
        "is_bot": False,
    }


def _message_rich_html(message: dict[str, Any]) -> str:
    rich_message = message.get("rich_message")
    if isinstance(rich_message, dict):
        html_text = rich_message.get("html")
        if isinstance(html_text, str):
            return html_text
    html_text = message.get("html")
    return html_text if isinstance(html_text, str) else ""


# Fields the sending bot writes itself. Others, such as reply_to_message,
# quote or external_reply, embed text written by other people.
MESSAGE_CONTENT_KEYS = ("text", "caption", "html", "rich_message", "rich_text", "blocks")


def _message_content_strings(message: dict[str, Any]) -> list[str]:
    strings: list[str] = []
    for key in MESSAGE_CONTENT_KEYS:
        strings.extend(_message_strings(message.get(key)))
    return strings


def _message_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_message_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_message_strings(item))
        return strings
    return []


def _html_text(html_text: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", html_text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _entity_start(text: str, entity: dict[str, Any]) -> int | None:
    offset = entity.get("offset")
    if not isinstance(offset, int):
        return None
    return _utf16_offset_index(text).get(offset)


def _entity_text(text: str, entity: dict[str, Any]) -> str:
    offset = entity.get("offset")
    length = entity.get("length")
    if not isinstance(offset, int) or not isinstance(length, int) or length <= 0:
        return ""

    indexes = _utf16_offset_index(text)
    start = indexes.get(offset)
    end = indexes.get(offset + length)
    if start is None or end is None:
        return ""
    return text[start:end]


def _utf16_offset_index(text: str) -> dict[int, int]:
    indexes = {0: 0}
    offset = 0
    for index, char in enumerate(text):
        offset += len(char.encode("utf-16-le")) // 2
        indexes[offset] = index + 1
    return indexes


def _tg_user_id(url: Any) -> int | None:
    if not isinstance(url, str):
        return None
    match = re.fullmatch(r"tg://user\?id=(\d+)", url)
    return int(match.group(1)) if match else None


def _user_from_link_label(user_id: int, label: str) -> dict[str, Any]:
    clean_label = label.strip()
    if clean_label.startswith("@"):
        return {"id": user_id, "first_name": "", "username": clean_label.removeprefix("@")}
    return {"id": user_id, "first_name": clean_label or "Usuário"}


def _looks_like_thanks(text: str) -> bool:
    if text.startswith("/"):
        return False
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return THANKS_REMINDER_RE.search(normalized) is not None


def _chat_id(message: dict[str, Any]) -> int | None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    return chat_id if isinstance(chat_id, int) else None


def _message_thread_id(message: dict[str, Any]) -> int | None:
    thread_id = message.get("message_thread_id")
    return thread_id if isinstance(thread_id, int) else None


def _thread_key(message: dict[str, Any]) -> int:
    return _message_thread_id(message) or 0


def _explicit_reply(message: dict[str, Any]) -> dict[str, Any] | None:
    """Return the replied message, ignoring a forum topic's implicit reply.

    In forum topics, Telegram fills reply_to_message with the topic's creation
    service message when the user did not reply to anything.
    """
    reply = message.get("reply_to_message")
    if not isinstance(reply, dict):
        return None
    if "forum_topic_created" in reply:
        return None
    thread_id = _message_thread_id(message)
    if (
        message.get("is_topic_message")
        and thread_id is not None
        and reply.get("message_id") == thread_id
    ):
        return None
    return reply


def _message_link(chat: dict[str, Any], message_id: int) -> str:
    username = chat.get("username")
    if isinstance(username, str) and username.strip():
        return f"https://t.me/{username.strip().removeprefix('@')}/{message_id}"

    chat_id = chat.get("id")
    if isinstance(chat_id, int) and str(chat_id).startswith("-100"):
        return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{message_id}"

    return ""


def _message_link_exists(link: str) -> bool:
    if not link.startswith("https://t.me/"):
        return True

    check_url = f"{link}{'&' if '?' in link else '?'}embed=1"
    request = urllib.request.Request(check_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            body = response.read(20000).decode("utf-8", "ignore").casefold()
    except urllib.error.HTTPError as error:
        body = error.read(20000).decode("utf-8", "ignore").casefold()
        if error.code in {404, 410}:
            return False
        return not _message_missing_marker(body) and _message_link_body_has_text(body)
    except (OSError, TimeoutError):
        return True
    return not _message_missing_marker(body) and _message_link_body_has_text(body)


def _message_missing_marker(body: str) -> bool:
    return any(
        marker in body
        for marker in (
            "message not found",
            "message doesn't exist",
            "message does not exist",
            "post not found",
            "message was deleted",
            "requested page could not be found",
        )
    )


# Classes of the t.me embed page that show a message's media; a question sent
# as a photo or file without a caption is still there.
EMBED_MEDIA_CLASSES = (
    "tgme_widget_message_photo",
    "tgme_widget_message_video",
    "tgme_widget_message_roundvideo",
    "tgme_widget_message_document",
    "tgme_widget_message_voice",
    "tgme_widget_message_sticker",
    "tgme_widget_message_poll",
    "tgme_widget_message_location",
    "tgme_widget_message_grouped",
    "message_media_not_supported",
)


def _message_link_body_has_text(body: str) -> bool:
    if "tgme_widget_message_wrap" not in body:
        return True
    if any(media_class in body for media_class in EMBED_MEDIA_CLASSES):
        return True
    match = re.search(
        r"""<div[^>]+class=["'][^"']*\btgme_widget_message_text\b[^"']*["'][^>]*>(.*?)</div>""",
        body,
        flags=re.DOTALL,
    )
    return bool(match and _html_text(match.group(1)))


def _message_delete_already_missing(error: TelegramApiError) -> bool:
    return "message to delete not found" in str(error).casefold()


def _message_delete_refused(error: TelegramApiError) -> bool:
    return "message can't be deleted" in str(error).casefold()


def _week_key(now: datetime) -> str:
    year, week, _weekday = now.date().isocalendar()
    return f"{year}-W{week:02d}"


def _weekly_time_reached(now: datetime, *, weekday: int, hour: int, minute: int) -> bool:
    return now.weekday() == weekday and (now.hour, now.minute) >= (hour, minute)


def _question_digest_slot_key(now: datetime, hours: tuple[int, ...]) -> str | None:
    due_hours = [hour for hour in hours if now.hour >= hour]
    if not due_hours:
        return None
    hour = max(due_hours)
    return f"{now.date().isoformat()}T{hour:02d}"


def _normalize_username(username: Any) -> str | None:
    if not isinstance(username, str) or not username.strip():
        return None
    return username.strip().removeprefix("@").lower()


def create_bot(runtime_config: config.RuntimeConfig | None = None) -> ArchimedesBot | None:
    runtime_config = runtime_config or config.load_runtime_config()
    if not runtime_config.bot_token:
        return None
    return ArchimedesBot(
        TelegramApi(runtime_config.bot_token, base_url=runtime_config.bot_api_base_url),
        StarStore(runtime_config.data_path),
        allowed_chat_ids=runtime_config.allowed_chat_ids,
        allowed_chat_usernames=runtime_config.allowed_chat_usernames,
        allow_all_groups=runtime_config.allow_all_groups,
        owner_user_ids=runtime_config.owner_user_ids,
        private_chat_notice=runtime_config.private_chat_notice,
        trusted_footer_bot_usernames=runtime_config.trusted_footer_bot_usernames,
        daily_star_limit=runtime_config.daily_star_limit,
        weekly_ranking_weekday=runtime_config.weekly_ranking_weekday,
        weekly_ranking_hour=runtime_config.weekly_ranking_hour,
        weekly_ranking_minute=runtime_config.weekly_ranking_minute,
        question_digest_thread_id=runtime_config.question_digest_thread_id,
        question_digest_hours=runtime_config.question_digest_hours,
        now=lambda: datetime.now(runtime_config.timezone),
    )
