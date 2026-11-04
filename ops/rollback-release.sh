#!/usr/bin/env bash
set -euo pipefail
umask 077

slug='telearchimedesbot'
release_parent="/opt/$slug/releases"
current_link="/opt/$slug/current"
previous_link="/opt/$slug/previous"

fail() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}

if [[ "$(id -u)" -ne 0 ]]; then
  fail 'Execute como root.'
fi
if systemctl is-active --quiet "$slug.service"; then
  fail "Pare $slug.service antes do rollback." 3
fi
[[ -L "$current_link" && -L "$previous_link" ]] || fail 'current ou previous ausente.' 4

current_target="$(readlink -f "$current_link")"
previous_target="$(readlink -f "$previous_link")"
case "$current_target" in "$release_parent"/*) ;; *) fail 'current aponta para fora dos releases.' 5 ;; esac
case "$previous_target" in "$release_parent"/*) ;; *) fail 'previous aponta para fora dos releases.' 5 ;; esac
[[ -x "$previous_target/.venv/bin/telearchimedesbot" ]] || fail 'entrypoint anterior inválido.' 6

"$previous_target/.venv/bin/telearchimedesbot" --help >/dev/null
ln -sfn "$previous_target" "$current_link.tmp"
mv -Tf "$current_link.tmp" "$current_link"
ln -sfn "$current_target" "$previous_link.tmp"
mv -Tf "$previous_link.tmp" "$previous_link"

printf 'rollback de release concluído; serviço não iniciado\n'
