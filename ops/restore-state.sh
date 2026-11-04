#!/usr/bin/env bash
set -euo pipefail
umask 077

slug='telearchimedesbot'
service_user="$slug"
key_file="/etc/$slug/backup.agekey"
default_db="/var/lib/$slug/stars.sqlite3"
replace=0
work_dir=''

cleanup() {
  if [[ -n "$work_dir" && -d "$work_dir" ]]; then
    rm -rf -- "$work_dir"
  fi
}
trap cleanup EXIT HUP INT TERM

fail() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}

if [[ "$(id -u)" -ne 0 ]]; then
  fail 'Execute como root.'
fi
if [[ "${1:-}" == '--replace' ]]; then
  replace=1
  shift
fi
backup_path="${1:-}"
destination="${2:-}"
if [[ "$backup_path" != /* || ! -f "$backup_path" || "$destination" != /* ]]; then
  fail 'Uso: restore-state [--replace] /caminho/backup.sqlite3.age /destino/absoluto.sqlite3' 2
fi
[[ -f "$key_file" ]] || fail 'Chave de backup ausente.' 3

if [[ "$replace" -eq 1 ]]; then
  [[ "$destination" == "$default_db" ]] || fail "Destino de substituição deve ser $default_db" 4
  if systemctl is-active --quiet "$slug.service"; then
    fail "Pare $slug.service antes de substituir o banco." 5
  fi
fi

work_dir="$(mktemp -d "/run/$slug-restore.XXXXXX")"
chmod 0700 "$work_dir"
plain="$work_dir/restore.sqlite3"
age -d -i "$key_file" -o "$plain" "$backup_path"
integrity="$(sqlite3 "$plain" 'PRAGMA integrity_check;')"
[[ "$integrity" == 'ok' ]] || fail 'Integridade do backup restaurado falhou.' 6

if [[ "$replace" -eq 1 ]]; then
  # The service user owns the data directory, so it writes there itself: root
  # only streams the verified copy, and links planted in that directory cannot
  # redirect a root write to another file.
  sudo -n -u "$service_user" sh -c 'umask 077 && cat >"$1"' sh "$destination.tmp" <"$plain"
  sudo -n -u "$service_user" mv -Tf -- "$destination.tmp" "$destination"
  sudo -n -u "$service_user" rm -f -- "$destination-wal" "$destination-shm"
else
  install -m 0600 -o root -g root "$plain" "$destination"
fi

printf 'restauração verificada: %s\n' "$destination"
