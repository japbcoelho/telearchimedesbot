from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RankingEntry:
    user_id: int
    display_name: str
    username: str | None
    stars: int


@dataclass(frozen=True)
class PendingQuestion:
    id: int
    chat_id: int
    thread_key: int
    message_id: int
    author_id: int | None
    author_display_name: str
    author_username: str | None
    preview: str
    link: str
    created_at: str


class StarStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.db.close()

    def _init_schema(self) -> None:
        self.db.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL,
                username TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                username TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                giver_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                local_date TEXT NOT NULL,
                week_key TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_stars_giver_day
                ON stars(giver_id, local_date);
            CREATE INDEX IF NOT EXISTS idx_stars_chat_week
                ON stars(chat_id, week_key, receiver_id);
            CREATE TABLE IF NOT EXISTS weekly_posts (
                chat_id INTEGER NOT NULL,
                week_key TEXT NOT NULL,
                posted_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, week_key)
            );
            CREATE TABLE IF NOT EXISTS reminder_messages (
                chat_id INTEGER NOT NULL,
                thread_key INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, thread_key)
            );
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                thread_key INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                author_id INTEGER,
                author_display_name TEXT NOT NULL,
                author_username TEXT,
                preview TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL,
                created_at TEXT NOT NULL,
                closed_at TEXT,
                UNIQUE(chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_questions_open
                ON questions(chat_id, closed_at, id);
            CREATE TABLE IF NOT EXISTS question_digest_state (
                chat_id INTEGER NOT NULL,
                thread_key INTEGER NOT NULL,
                slot_key TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, thread_key)
            );
            CREATE TABLE IF NOT EXISTS question_manual_report_state (
                chat_id INTEGER NOT NULL,
                thread_key INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, thread_key)
            );
            CREATE TABLE IF NOT EXISTS question_report_cleanup (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                queued_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );
            """
        )
        question_columns = {
            str(row["name"]) for row in self.db.execute("PRAGMA table_info(questions)").fetchall()
        }
        if "preview" not in question_columns:
            self.db.execute("ALTER TABLE questions ADD COLUMN preview TEXT NOT NULL DEFAULT ''")
        chat_columns = {
            str(row["name"]) for row in self.db.execute("PRAGMA table_info(chats)").fetchall()
        }
        if "username" not in chat_columns:
            self.db.execute("ALTER TABLE chats ADD COLUMN username TEXT")
        self.db.commit()

    def upsert_user(self, user: dict[str, Any], *, updated_at: str) -> None:
        user_id = user.get("id")
        if not isinstance(user_id, int):
            return
        self.db.execute(
            """
            INSERT INTO users(user_id, display_name, username, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name = excluded.display_name,
                username = excluded.username,
                updated_at = excluded.updated_at
            """,
            (user_id, _display_name(user), _username(user), updated_at),
        )
        self.db.commit()

    def ensure_user(self, user: dict[str, Any], *, updated_at: str) -> None:
        """Record a user only if unknown, keeping the profile Telegram gave us."""
        user_id = user.get("id")
        if not isinstance(user_id, int):
            return
        self.db.execute(
            """
            INSERT OR IGNORE INTO users(user_id, display_name, username, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, _display_name(user), _username(user), updated_at),
        )
        self.db.commit()

    def user_by_username(self, username: str) -> dict[str, Any] | None:
        """The account behind @username: a real account first, then the newest."""
        username = username.strip().removeprefix("@")
        if not username:
            return None
        row = self.db.execute(
            """
            SELECT user_id, display_name, username
            FROM users
            WHERE LOWER(username) = LOWER(?)
            ORDER BY user_id > 0 DESC, updated_at DESC
            LIMIT 1
            """,
            (username,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["user_id"]),
            "first_name": str(row["display_name"]),
            "username": row["username"],
        }

    def upsert_chat(self, chat: dict[str, Any], *, updated_at: str) -> None:
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return
        self.db.execute(
            """
            INSERT INTO chats(chat_id, title, username, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                username = excluded.username,
                updated_at = excluded.updated_at
            """,
            (chat_id, _chat_title(chat), _username(chat), updated_at),
        )
        self.db.commit()

    def daily_given_count(self, *, giver_id: int, local_date: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS total FROM stars WHERE giver_id = ? AND local_date = ?",
            (giver_id, local_date),
        ).fetchone()
        return int(row["total"])

    def add_star(
        self,
        *,
        chat_id: int,
        giver_id: int,
        receiver_id: int,
        created_at: str,
        local_date: str,
        week_key: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO stars(chat_id, giver_id, receiver_id, created_at, local_date, week_key)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, giver_id, receiver_id, created_at, local_date, week_key),
        )
        self.db.commit()

    def receiver_total(self, *, chat_id: int, receiver_id: int) -> int:
        """All the stars a person has received in a group, since the start."""
        row = self.db.execute(
            "SELECT COUNT(*) AS total FROM stars WHERE chat_id = ? AND receiver_id = ?",
            (chat_id, receiver_id),
        ).fetchone()
        return int(row["total"])

    def all_time_ranking(self, *, chat_id: int, limit: int = 10) -> list[RankingEntry]:
        rows = self.db.execute(
            """
            SELECT
                s.receiver_id AS user_id,
                COUNT(*) AS stars,
                COALESCE(u.display_name, 'Usuário') AS display_name,
                u.username AS username
            FROM stars s
            LEFT JOIN users u ON u.user_id = s.receiver_id
            WHERE s.chat_id = ?
            GROUP BY s.receiver_id
            ORDER BY stars DESC, LOWER(display_name) ASC
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()
        return [
            RankingEntry(
                user_id=int(row["user_id"]),
                display_name=str(row["display_name"]),
                username=row["username"],
                stars=int(row["stars"]),
            )
            for row in rows
        ]

    def known_chat_ids(self) -> list[int]:
        return [chat_id for chat_id, _username in self.known_chats()]

    def known_chats(self) -> list[tuple[int, str | None]]:
        """Groups the bot has seen, with their @username when public."""
        rows = self.db.execute("SELECT chat_id, username FROM chats ORDER BY chat_id").fetchall()
        return [(int(row["chat_id"]), row["username"]) for row in rows]

    def weekly_posted(self, *, chat_id: int, week_key: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM weekly_posts WHERE chat_id = ? AND week_key = ?",
            (chat_id, week_key),
        ).fetchone()
        return row is not None

    def mark_weekly_posted(self, *, chat_id: int, week_key: str, posted_at: str) -> None:
        self.db.execute(
            """
            INSERT OR IGNORE INTO weekly_posts(chat_id, week_key, posted_at)
            VALUES (?, ?, ?)
            """,
            (chat_id, week_key, posted_at),
        )
        self.db.commit()

    def reminder_message_id(self, *, chat_id: int, thread_key: int) -> int | None:
        row = self.db.execute(
            """
            SELECT message_id
            FROM reminder_messages
            WHERE chat_id = ? AND thread_key = ?
            """,
            (chat_id, thread_key),
        ).fetchone()
        return int(row["message_id"]) if row else None

    def set_reminder_message(
        self,
        *,
        chat_id: int,
        thread_key: int,
        message_id: int,
        updated_at: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO reminder_messages(chat_id, thread_key, message_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, thread_key) DO UPDATE SET
                message_id = excluded.message_id,
                updated_at = excluded.updated_at
            """,
            (chat_id, thread_key, message_id, updated_at),
        )
        self.db.commit()

    def add_or_reopen_question(
        self,
        *,
        chat_id: int,
        thread_key: int,
        message_id: int,
        author: dict[str, Any] | None,
        preview: str,
        link: str,
        created_at: str,
    ) -> PendingQuestion:
        author_id = author.get("id") if isinstance(author, dict) else None
        if not isinstance(author_id, int):
            author_id = None
        display_name = _display_name(author or {})
        username = _username(author or {})
        self.db.execute(
            """
            INSERT INTO questions(
                chat_id,
                thread_key,
                message_id,
                author_id,
                author_display_name,
                author_username,
                preview,
                link,
                created_at,
                closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                thread_key = excluded.thread_key,
                author_id = excluded.author_id,
                author_display_name = excluded.author_display_name,
                author_username = excluded.author_username,
                preview = excluded.preview,
                link = excluded.link,
                closed_at = NULL
            """,
            (
                chat_id,
                thread_key,
                message_id,
                author_id,
                display_name,
                username,
                preview,
                link,
                created_at,
            ),
        )
        self.db.commit()
        row = self.db.execute(
            """
            SELECT *
            FROM questions
            WHERE chat_id = ? AND message_id = ?
            """,
            (chat_id, message_id),
        ).fetchone()
        return _pending_question_from_row(row)

    def close_question_by_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        closed_at: str,
    ) -> PendingQuestion | None:
        row = self.db.execute(
            """
            SELECT *
            FROM questions
            WHERE chat_id = ? AND message_id = ? AND closed_at IS NULL
            """,
            (chat_id, message_id),
        ).fetchone()
        if row is None:
            return None
        self.db.execute(
            "UPDATE questions SET closed_at = ? WHERE id = ?",
            (closed_at, row["id"]),
        )
        self.db.commit()
        return _pending_question_from_row(row)

    def close_question_by_id(
        self,
        *,
        chat_id: int,
        question_id: int,
        closed_at: str,
    ) -> PendingQuestion | None:
        row = self.db.execute(
            """
            SELECT *
            FROM questions
            WHERE chat_id = ? AND id = ? AND closed_at IS NULL
            """,
            (chat_id, question_id),
        ).fetchone()
        if row is None:
            return None
        self.db.execute(
            "UPDATE questions SET closed_at = ? WHERE id = ?",
            (closed_at, question_id),
        )
        self.db.commit()
        return _pending_question_from_row(row)

    def close_question_by_position(
        self,
        *,
        chat_id: int,
        position: int,
        closed_at: str,
    ) -> PendingQuestion | None:
        if position < 1:
            return None
        row = self.db.execute(
            """
            SELECT *
            FROM questions
            WHERE chat_id = ? AND closed_at IS NULL
            ORDER BY id ASC
            LIMIT 1 OFFSET ?
            """,
            (chat_id, position - 1),
        ).fetchone()
        if row is None:
            return None
        self.db.execute(
            "UPDATE questions SET closed_at = ? WHERE id = ?",
            (closed_at, row["id"]),
        )
        self.db.commit()
        return _pending_question_from_row(row)

    def open_questions(self, *, chat_id: int) -> list[PendingQuestion]:
        rows = self.db.execute(
            """
            SELECT *
            FROM questions
            WHERE chat_id = ? AND closed_at IS NULL
            ORDER BY id ASC
            """,
            (chat_id,),
        ).fetchall()
        return [_pending_question_from_row(row) for row in rows]

    def question_digest_state(
        self,
        *,
        chat_id: int,
        thread_key: int,
    ) -> tuple[str, int] | None:
        row = self.db.execute(
            """
            SELECT slot_key, message_id
            FROM question_digest_state
            WHERE chat_id = ? AND thread_key = ?
            """,
            (chat_id, thread_key),
        ).fetchone()
        return (str(row["slot_key"]), int(row["message_id"])) if row else None

    def set_question_digest_state(
        self,
        *,
        chat_id: int,
        thread_key: int,
        slot_key: str,
        message_id: int,
        updated_at: str,
    ) -> None:
        previous = self.question_digest_state(chat_id=chat_id, thread_key=thread_key)
        self.db.execute(
            """
            INSERT INTO question_digest_state(chat_id, thread_key, slot_key, message_id, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, thread_key) DO UPDATE SET
                slot_key = excluded.slot_key,
                message_id = excluded.message_id,
                updated_at = excluded.updated_at
            """,
            (chat_id, thread_key, slot_key, message_id, updated_at),
        )
        if previous is not None and previous[1] != message_id:
            self.db.execute(
                """
                INSERT OR IGNORE INTO question_report_cleanup(chat_id, message_id, queued_at)
                VALUES (?, ?, ?)
                """,
                (chat_id, previous[1], updated_at),
            )
        self.db.execute(
            """
            INSERT OR IGNORE INTO question_report_cleanup(chat_id, message_id, queued_at)
            SELECT chat_id, message_id, ?
            FROM question_manual_report_state
            WHERE chat_id = ?
            """,
            (updated_at, chat_id),
        )
        self.db.execute(
            "DELETE FROM question_manual_report_state WHERE chat_id = ?",
            (chat_id,),
        )
        self.db.commit()

    def manual_question_report_state(
        self,
        *,
        chat_id: int,
        thread_key: int,
    ) -> int | None:
        row = self.db.execute(
            """
            SELECT message_id
            FROM question_manual_report_state
            WHERE chat_id = ? AND thread_key = ?
            """,
            (chat_id, thread_key),
        ).fetchone()
        return int(row["message_id"]) if row else None

    def set_manual_question_report_state(
        self,
        *,
        chat_id: int,
        thread_key: int,
        message_id: int,
        updated_at: str,
    ) -> None:
        previous_id = self.manual_question_report_state(
            chat_id=chat_id,
            thread_key=thread_key,
        )
        self.db.execute(
            """
            INSERT INTO question_manual_report_state(chat_id, thread_key, message_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, thread_key) DO UPDATE SET
                message_id = excluded.message_id,
                updated_at = excluded.updated_at
            """,
            (chat_id, thread_key, message_id, updated_at),
        )
        if previous_id is not None and previous_id != message_id:
            self.db.execute(
                """
                INSERT OR IGNORE INTO question_report_cleanup(chat_id, message_id, queued_at)
                VALUES (?, ?, ?)
                """,
                (chat_id, previous_id, updated_at),
            )
        self.db.commit()

    def question_report_cleanup_message_ids(self, *, chat_id: int) -> list[int]:
        rows = self.db.execute(
            """
            SELECT message_id
            FROM question_report_cleanup
            WHERE chat_id = ?
            ORDER BY message_id
            """,
            (chat_id,),
        ).fetchall()
        return [int(row["message_id"]) for row in rows]

    def mark_question_report_cleaned(self, *, chat_id: int, message_id: int) -> None:
        self.db.execute(
            """
            DELETE FROM question_report_cleanup
            WHERE chat_id = ? AND message_id = ?
            """,
            (chat_id, message_id),
        )
        self.db.commit()


def _display_name(user: dict[str, Any]) -> str:
    first_name = (user.get("first_name") or "").strip()
    last_name = (user.get("last_name") or "").strip()
    name = " ".join(part for part in (first_name, last_name) if part)
    username = _username(user)
    return name or (f"@{username}" if username else "Usuário")


def _username(user: dict[str, Any]) -> str | None:
    username = user.get("username")
    if not isinstance(username, str) or not username.strip():
        return None
    return username.strip().removeprefix("@")


def _chat_title(chat: dict[str, Any]) -> str:
    title = chat.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    username = chat.get("username")
    if isinstance(username, str) and username.strip():
        return "@" + username.strip().removeprefix("@")
    return str(chat.get("id") or "chat")


def _pending_question_from_row(row: sqlite3.Row) -> PendingQuestion:
    return PendingQuestion(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        thread_key=int(row["thread_key"]),
        message_id=int(row["message_id"]),
        author_id=int(row["author_id"]) if row["author_id"] is not None else None,
        author_display_name=str(row["author_display_name"]),
        author_username=row["author_username"],
        preview=str(row["preview"]),
        link=str(row["link"]),
        created_at=str(row["created_at"]),
    )
