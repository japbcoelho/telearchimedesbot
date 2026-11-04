#!/usr/bin/env bash
set -euo pipefail
umask 077

slug='telearchimedesbot'
service_user="$slug"
service_group="$slug"
artifact="${1:-}"
expected_sha256="${2:-}"
version="${3:-}"
release_parent="/opt/$slug/releases"
release_dir="$release_parent/$version"
current_link="/opt/$slug/current"
previous_link="/opt/$slug/previous"
tmp_dir=''
token_file=''
release_created=0
installed=0

cleanup() {
  if [[ -n "$token_file" && -e "$token_file" ]]; then
    unlink "$token_file"
  fi
  if [[ "$installed" -eq 0 && -n "$tmp_dir" && -d "$tmp_dir" ]]; then
    rm -rf -- "$tmp_dir"
  fi
  if [[ "$installed" -eq 0 && "$release_created" -eq 1 && -d "$release_dir" ]]; then
    rm -rf -- "$release_dir"
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
if [[ "$artifact" != /* || ! -f "$artifact" || "$expected_sha256" == '' || "$version" == '' ]]; then
  fail 'Uso: install-release /caminho/absoluto/artefato.tar.gz SHA256 VERSAO' 2
fi
if [[ ! "$expected_sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
  fail 'SHA-256 esperado inválido.' 2
fi
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([._+-][A-Za-z0-9._-]+)?$ ]]; then
  fail 'Versão inválida.' 2
fi
if [[ -e "$release_dir" ]]; then
  fail "Release já existe: $release_dir" 5
fi
if ! id "$service_user" >/dev/null 2>&1; then
  fail "Usuário de serviço ausente: $service_user" 6
fi

actual_sha256="$(sha256sum "$artifact" | awk '{print $1}')"
if [[ "${actual_sha256,,}" != "${expected_sha256,,}" ]]; then
  fail 'SHA-256 do artefato não confere.' 7
fi

while IFS= read -r member; do
  case "$member" in
    ''|/*|../*|*/../*|*/.git|*/.git/*|*/.venv|*/.venv/*|*/node_modules|*/node_modules/*|*/__pycache__|*/__pycache__/*|*/.pytest_cache|*/.pytest_cache/*|*/.ruff_cache|*/.ruff_cache/*|*/build|*/build/*|*/dist|*/dist/*|*/.env|*/stars.sqlite3|*.db|*.sqlite|*.sqlite3|*.log|*.pid|*.pem|*.key)
      fail "Membro inseguro no artefato: $member" 8
      ;;
  esac
done < <(tar -tzf "$artifact")

if ! tar -tvzf "$artifact" | awk 'substr($1,1,1) == "l" || substr($1,1,1) == "h" { bad=1 } END { exit bad }'; then
  fail 'Artefato contém link simbólico ou hard link.' 8
fi

tmp_dir="$(mktemp -d "$release_parent/.install-$version.XXXXXX")"
tar -xzf "$artifact" -C "$tmp_dir" --strip-components=1
[[ -f "$tmp_dir/pyproject.toml" ]] || fail 'pyproject.toml ausente no release.' 9
[[ -f "$tmp_dir/uv.lock" ]] || fail 'uv.lock ausente no release.' 9
[[ -d "$tmp_dir/telearchimedes_bot" ]] || fail 'pacote Python ausente no release.' 9
chmod 0755 "$tmp_dir"

(
  mv "$tmp_dir" "$release_dir"
  tmp_dir=''
  release_created=1
  cd "$release_dir"
  uv sync --frozen --no-dev --no-editable --compile-bytecode
)
chmod -R a+rX "$release_dir"
chmod -R go-w "$release_dir"

token_file="$(mktemp "/run/$slug-token.XXXXXX")"
printf 'test-token' >"$token_file"
chown "$service_user:$service_group" "$token_file"
chmod 0600 "$token_file"

sudo -n -u "$service_user" env -i \
  PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
  HOME="/var/lib/$slug" \
  PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  BOT_TOKEN_FILE="$token_file" \
  BOT_API_BASE_URL='https://api.telegram.org' \
  DATA_PATH="/var/lib/$slug/stars.sqlite3" \
  TIMEZONE='America/Sao_Paulo' \
  DAILY_STAR_LIMIT=3 \
  WEEKLY_RANKING_WEEKDAY=6 \
  WEEKLY_RANKING_HOUR=20 \
  WEEKLY_RANKING_MINUTE=0 \
  QUESTION_DIGEST_THREAD_ID=0 \
  QUESTION_DIGEST_HOURS=15 \
  ALLOWED_CHAT_IDS='' \
  TELEARCHIMEDES_STRICT_CONFIG=1 \
  "$release_dir/.venv/bin/telearchimedesbot" preflight >/dev/null

sudo -n -u "$service_user" "$release_dir/.venv/bin/telearchimedesbot" --help >/dev/null
chown -R root:root "$release_dir"
chmod -R go-w "$release_dir"
installed=1

if [[ -L "$current_link" ]]; then
  old_current="$(readlink -f "$current_link")"
  case "$old_current" in
    "$release_parent"/*) ;;
    *) fail 'current aponta para fora do diretório de releases.' 10 ;;
  esac
  ln -sfn "$old_current" "$previous_link.tmp"
  mv -Tf "$previous_link.tmp" "$previous_link"
fi

ln -sfn "$release_dir" "$current_link.tmp"
mv -Tf "$current_link.tmp" "$current_link"

printf 'release instalado; serviço não reiniciado\n'
