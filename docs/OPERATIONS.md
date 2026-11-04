# Running TeleArchimedesBot on a server

The production setup runs the bot by long polling, with no public port and no reverse proxy, as a hardened systemd service. The scripts in `ops/` assume a Debian or Ubuntu host with `python3`, `uv`, `age`, `systemd-creds` and `sqlite3`.

## Layout

| What | Path |
|---|---|
| Active release | `/opt/telearchimedesbot/current` (link to `releases/<version>`) |
| Previous release | `/opt/telearchimedesbot/previous` |
| SQLite state | `/var/lib/telearchimedesbot/stars.sqlite3` |
| Non-secret configuration | `/etc/telearchimedesbot/telearchimedesbot.conf` |
| Encrypted bot token | `/etc/credstore.encrypted/telearchimedesbot-bot-token.cred` |
| Encrypted backups | `/var/backups/telearchimedesbot` |
| Operation scripts | `/usr/local/libexec/telearchimedesbot` |
| Service unit | `/etc/systemd/system/telearchimedesbot.service` |

## First installation

1. Copy `ops/` to the server and run `sudo ops/provision-host.sh /absolute/path/to/ops`. It creates the `telearchimedesbot` system user, the directories, the backup key and the units, and installs `ops/telearchimedesbot.conf.example` as the configuration.
2. Edit `/etc/telearchimedesbot/telearchimedesbot.conf` (see the README for every setting).
3. Store the token as an encrypted credential, without leaving it in the shell history:
   `read -rs TOKEN && printf '%s' "$TOKEN" | sudo systemd-creds encrypt --name=bot-token - /etc/credstore.encrypted/telearchimedesbot-bot-token.cred; unset TOKEN`
4. Install a release (below), then `sudo systemctl enable --now telearchimedesbot.service`.

## Installing a release

```bash
uv build --sdist                         # on your machine: dist/telearchimedesbot-<version>.tar.gz
scp dist/telearchimedesbot-<version>.tar.gz server:/tmp/
sudo systemctl start telearchimedesbot-backup.service
sudo /usr/local/libexec/telearchimedesbot/install-release /tmp/telearchimedesbot-<version>.tar.gz <sha256> <version>
sudo systemctl restart telearchimedesbot.service
```

`install-release` checks the SHA-256, refuses unsafe archive members and links, installs the locked dependencies with `uv sync --frozen`, runs `preflight` as the service user and only then switches `current`. It does not restart the service.

## Backups and rollback

- `telearchimedesbot-backup.service` takes a SQLite snapshot as the service user, checks its integrity, encrypts it with `age` and verifies that it decrypts; it keeps the newest 30.
- Code rollback, with the service stopped: `sudo /usr/local/libexec/telearchimedesbot/rollback-release`.
- Data restore, with the service stopped and from a verified backup: `sudo /usr/local/libexec/telearchimedesbot/restore-state --replace <backup.sqlite3.age> /var/lib/telearchimedesbot/stars.sqlite3`.

## Logs

```bash
sudo journalctl -u telearchimedesbot.service -n 100 --no-pager
```

The bot never logs its token; Telegram API errors carry only the method name and Telegram's description.
