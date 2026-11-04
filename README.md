# TeleArchimedesBot

A Telegram group bot that rewards help with stars and keeps track of open questions. Its messages are in Brazilian Portuguese.

## What it does

**Stars**
- Reply to someone's message with `/obrigado` (or `/obrigada`) to give them one star. Each person can give up to `DAILY_STAR_LIMIT` stars a day (3 by default).
- Messages such as `valeu`, `obg` or `obrigado` get a reminder to use `/obrigado` as a reply. The bot keeps only one reminder per group or topic, deleting the previous one.
- `/ranking` shows the group's all-time top 10, with gold, silver and bronze for the first three. Every week (Sunday at 20:00 by default) the bot posts the same ranking in each group it serves.
- Integration with a LaTeX rendering bot: when `/obrigado` replies to a message from a bot listed in `TRUSTED_FOOTER_BOT_USERNAMES`, the star goes to the person named in that message's footer ("Código enviado por …"), identified by profile link or `@username`. A footer with only a display name is refused, because anyone can rename themselves. If the footer names an anonymous admin, the star goes to the group.

**Questions**
- Reply to a question with `/duvida` to add it to the open list. Photos, images sent as files and other media without a caption are accepted; a description after the command is optional.
- Reply to it with `/resolvida`, or send `/resolvida #2`, to close it.
- Once a day (15:00 by default) the bot posts the open list in the General topic, replacing the previous list. `/pendentes` posts the list on demand in the current topic.
- Each item links to the original message (`# dúvida N`) and shows its first 20 characters. In public groups, questions whose message was deleted are closed automatically.

**Quiet by design**
- `/obrigado`, `/duvida` and `/resolvida` sent alone without a reply (typically a tap on the command menu) are deleted. With text or media they are kept, and the bot answers with how to use them.
- The bot serves only the groups listed in `ALLOWED_CHAT_IDS` or `ALLOWED_CHAT_USERNAMES` (or every group with `ALLOW_ALL_GROUPS=1`); elsewhere it does not answer. In private it answers only the IDs in `OWNER_USER_IDS`, with `PRIVATE_CHAT_NOTICE`.

## Running it locally

```bash
uv sync
cp .env.example .env    # then set BOT_TOKEN
uv run telearchimedesbot run
```

`uv run telearchimedesbot config-check` validates the configuration without network access, and `preflight` also checks the database.

## Configuration

Settings come from environment variables (or `.env` when running locally):

| Variable | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` / `BOT_TOKEN_FILE` | | Bot token, or a file holding it (required in production). |
| `DATA_PATH` | `stars.sqlite3` | SQLite database. |
| `TIMEZONE` | `America/Sao_Paulo` | Time zone for days, weeks and schedules. |
| `DAILY_STAR_LIMIT` | `3` | Stars each person can give per day. |
| `WEEKLY_RANKING_WEEKDAY` / `_HOUR` / `_MINUTE` | `6` / `20` / `0` | When the ranking is posted (Monday is `0`, Sunday `6`). |
| `QUESTION_DIGEST_HOURS` | `15` | Hours (comma separated) of the open-questions list. |
| `QUESTION_DIGEST_THREAD_ID` | `0` | Topic for that list; `0` is the General topic. |
| `ALLOWED_CHAT_IDS` | | Group IDs the bot serves, comma separated. |
| `ALLOWED_CHAT_USERNAMES` | | Public group `@usernames` the bot serves, comma separated. |
| `ALLOW_ALL_GROUPS` | `0` | `1` serves every group. Otherwise only the listed groups are served, and with no list the bot answers nowhere. |
| `OWNER_USER_IDS` | | Users who get `PRIVATE_CHAT_NOTICE` in private; everyone else gets no answer. |
| `PRIVATE_CHAT_NOTICE` | `Este bot só funciona nos grupos autorizados.` | Text for those users. |
| `TRUSTED_FOOTER_BOT_USERNAMES` | `telelatexbot` | Bots whose author footer is trusted for stars. |
| `TELEARCHIMEDES_STRICT_CONFIG` | `0` | `1` in production: requires `BOT_TOKEN_FILE` and an absolute `DATA_PATH`. |

## Development

```bash
uv sync --extra dev
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

## Production

See [docs/OPERATIONS.md](docs/OPERATIONS.md): an immutable release under `/opt`, state under `/var/lib`, the token as an encrypted systemd credential, and verified encrypted backups. The scripts are in `ops/`.

## License

[MIT](LICENSE)
