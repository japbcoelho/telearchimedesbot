#!/usr/bin/env bash
set -euo pipefail
umask 077

slug='telearchimedesbot'
service_user="$slug"
service_group="$slug"
state_dir="/var/lib/$slug"
ops_src="${1:-}"

if [[ "$(id -u)" -ne 0 ]]; then
  printf 'Execute como root.\n' >&2
  exit 1
fi
if [[ "$ops_src" != /* || ! -d "$ops_src" ]]; then
  printf 'Uso: provision-host.sh /caminho/absoluto/ops\n' >&2
  exit 2
fi

for command_name in python3 uv age systemd-creds sqlite3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Comando obrigatório ausente: %s\n' "$command_name" >&2
    exit 3
  fi
done

if getent group "$service_group" >/dev/null; then
  :
else
  groupadd --system "$service_group"
fi

if id "$service_user" >/dev/null 2>&1; then
  passwd_entry="$(getent passwd "$service_user")"
  user_home="$(printf '%s' "$passwd_entry" | cut -d: -f6)"
  user_shell="$(printf '%s' "$passwd_entry" | cut -d: -f7)"
  if [[ "$user_home" != "$state_dir" || "$user_shell" != '/usr/sbin/nologin' ]]; then
    printf 'Usuário existente %s não corresponde ao contrato esperado.\n' "$service_user" >&2
    exit 4
  fi
else
  useradd --system --gid "$service_group" --home-dir "$state_dir" --shell /usr/sbin/nologin "$service_user"
fi

install -d -m 0755 -o root -g root "/opt/$slug" "/opt/$slug/releases"
install -d -m 0700 -o "$service_user" -g "$service_group" "$state_dir" "/var/cache/$slug"
install -d -m 0750 -o root -g "$service_group" "/etc/$slug"
install -d -m 0700 -o root -g root "/var/backups/$slug" "/usr/local/libexec/$slug" /etc/credstore.encrypted

install -m 0755 -o root -g root "$ops_src/install-release.sh" "/usr/local/libexec/$slug/install-release"
install -m 0755 -o root -g root "$ops_src/rollback-release.sh" "/usr/local/libexec/$slug/rollback-release"
install -m 0755 -o root -g root "$ops_src/backup-state.sh" "/usr/local/libexec/$slug/backup-state"
install -m 0755 -o root -g root "$ops_src/restore-state.sh" "/usr/local/libexec/$slug/restore-state"
install -m 0644 -o root -g root "$ops_src/$slug.service" "/etc/systemd/system/$slug.service"
install -m 0644 -o root -g root "$ops_src/$slug-backup.service" "/etc/systemd/system/$slug-backup.service"
install -m 0644 -o root -g root "$ops_src/$slug-backup.timer" "/etc/systemd/system/$slug-backup.timer"

if [[ ! -e "/etc/$slug/$slug.conf" ]]; then
  install -m 0640 -o root -g "$service_group" "$ops_src/$slug.conf.example" "/etc/$slug/$slug.conf"
else
  chown root:"$service_group" "/etc/$slug/$slug.conf"
  chmod 0640 "/etc/$slug/$slug.conf"
fi

if [[ ! -e "/etc/$slug/backup.agekey" ]]; then
  age-keygen -o "/etc/$slug/backup.agekey" >/dev/null 2>&1
  chown root:root "/etc/$slug/backup.agekey"
  chmod 0600 "/etc/$slug/backup.agekey"
fi
age-keygen -y "/etc/$slug/backup.agekey" >"/etc/$slug/backup-recipient.tmp"
install -m 0600 -o root -g root "/etc/$slug/backup-recipient.tmp" "/etc/$slug/backup-recipient"
unlink "/etc/$slug/backup-recipient.tmp"

systemctl daemon-reload
if [[ -e "/opt/$slug/current/.venv/bin/telearchimedesbot" ]]; then
  systemd-analyze verify "/etc/systemd/system/$slug.service"
fi
systemd-analyze verify "/etc/systemd/system/$slug-backup.service" "/etc/systemd/system/$slug-backup.timer"

printf 'provisionamento concluído; serviço não iniciado\n'
