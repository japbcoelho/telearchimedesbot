#!/usr/bin/env bash
set -euo pipefail
umask 077

slug='telearchimedesbot'
service_user="$slug"
service_group="$slug"
db_path="/var/lib/$slug/stars.sqlite3"
backup_dir="/var/backups/$slug"
key_file="/etc/$slug/backup.agekey"
recipient_file="/etc/$slug/backup-recipient"
keep_count="${TELEARCHIMEDES_BACKUP_KEEP:-30}"
work_dir=''
snapshot_dir=''

cleanup() {
  if [[ -n "$snapshot_dir" && -d "$snapshot_dir" ]]; then
    # The service user owns this directory, so it also deletes its contents.
    sudo -n -u "$service_user" rm -rf -- "$snapshot_dir" 2>/dev/null || true
    rmdir -- "$snapshot_dir" 2>/dev/null || true
  fi
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
[[ -f "$db_path" ]] || fail "Banco ausente: $db_path" 2
[[ -f "$key_file" && -f "$recipient_file" ]] || fail 'Chave de backup ausente.' 3

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
# Root works only in its own private directory. The service user writes the
# snapshot to a separate directory, and root reads it back as the service user
# through a pipe, so links planted there cannot redirect root's writes or reads.
work_dir="$(mktemp -d "/run/$slug-backup.XXXXXX")"
chmod 0700 "$work_dir"
snapshot_dir="$(mktemp -d "/run/$slug-snapshot.XXXXXX")"
chown "$service_user:$service_group" "$snapshot_dir"
chmod 0700 "$snapshot_dir"
snapshot="$snapshot_dir/snapshot.sqlite3"
plain="$work_dir/$slug-$timestamp.sqlite3"
restore_check="$work_dir/$slug-$timestamp.restore.sqlite3"
encrypted_tmp="$work_dir/$slug-$timestamp.sqlite3.age"
final_name="$slug-$timestamp.sqlite3.age"

sudo -n -u "$service_user" sqlite3 "$db_path" ".backup '$snapshot'"
sudo -n -u "$service_user" cat -- "$snapshot" >"$plain"
integrity="$(sqlite3 "$plain" 'PRAGMA integrity_check;')"
[[ "$integrity" == 'ok' ]] || fail 'Integridade do snapshot falhou.' 4

recipient="$(cat "$recipient_file")"
age -r "$recipient" -o "$encrypted_tmp" "$plain"
age -d -i "$key_file" -o "$restore_check" "$encrypted_tmp"
restore_integrity="$(sqlite3 "$restore_check" 'PRAGMA integrity_check;')"
[[ "$restore_integrity" == 'ok' ]] || fail 'Teste de restauração falhou.' 5

install -m 0600 -o root -g root "$encrypted_tmp" "$backup_dir/$final_name"
(
  cd "$backup_dir"
  sha256sum "$final_name" >"$final_name.sha256.tmp"
)
install -m 0600 -o root -g root "$backup_dir/$final_name.sha256.tmp" "$backup_dir/$final_name.sha256"
unlink "$backup_dir/$final_name.sha256.tmp"

mapfile -t old_backups < <(find "$backup_dir" -maxdepth 1 -type f -name "$slug-*.sqlite3.age" | sort | head -n "-$keep_count")
for backup_path in "${old_backups[@]}"; do
  unlink "$backup_path"
  if [[ -e "$backup_path.sha256" ]]; then
    unlink "$backup_path.sha256"
  fi
done

printf 'backup verificado: %s\n' "$backup_dir/$final_name"
