#!/usr/bin/env bash
# 리서치면 Basic 인증 비밀번호를 바꾼다.
#
# 손으로 고치면 두 가지가 조용히 어긋난다. 평문을 그대로 넣어 아무도 로그인하지
# 못하게 되거나, 들여쓰기·중괄호를 건드려 Caddy 가 기동하지 못한다. 여기서는
# `basicauth` 블록 안의 그 사용자 줄 하나만 바꾸고 validate 를 거친다.
#
# 절차는 infra/server-ops.md 11-3.
#
#   sudo infra/scripts/set-caddy-password.sh --hash "$(caddy hash-password --plaintext '<새 비밀번호>')"
#   sudo infra/scripts/set-caddy-password.sh --password-stdin   # 평문을 ps·history 에 남기지 않는다
#
# --password-stdin 은 터미널에서 조용히 입력받아 caddy hash-password 로 넘긴다.
# --hash 는 이미 해시를 들고 있을 때 쓴다. 평문을 인자로 받는 방법은 두지 않는다 —
# 셸 히스토리와 `ps` 에 그대로 남는다.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"
CADDY_BIN="${CADDY_BIN:-caddy}"

user="friend"
hash=""
from_stdin=0
dry_run=0
while [ $# -gt 0 ]; do
    case "$1" in
        --hash) hash="${2:-}"; shift 2 ;;
        --user) user="${2:-}"; shift 2 ;;
        --password-stdin) from_stdin=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        *) die "모르는 인자: $1 (쓸 수 있는 것: --hash, --password-stdin, --user, --dry-run)" ;;
    esac
done

[ -f "$CADDYFILE" ] || die "Caddyfile 이 없다: $CADDYFILE"

if [ "$from_stdin" -eq 1 ]; then
    [ -z "$hash" ] || die "--hash 와 --password-stdin 을 같이 쓰지 않는다"
    if [ -t 0 ]; then
        printf '새 비밀번호: ' >&2
        read -rs plaintext; printf '\n' >&2
        printf '한 번 더: ' >&2
        read -rs confirm; printf '\n' >&2
        [ "$plaintext" = "$confirm" ] || die "두 입력이 다르다"
    else
        read -r plaintext
    fi
    [ -n "$plaintext" ] || die "빈 비밀번호는 쓰지 않는다"
    # 평문을 인자로 넘기지 않는다. `ps` 에 보인다.
    hash="$(printf '%s' "$plaintext" | "$CADDY_BIN" hash-password)"
    unset plaintext confirm
fi

[ -n "$hash" ] || die "--hash 또는 --password-stdin 중 하나가 필요하다"
case "$hash" in
    '$2a$'*|'$2b$'*|'$2y$'*) : ;;
    *) die "bcrypt 해시로 보이지 않는다. 평문을 넣으면 아무도 로그인하지 못한다." ;;
esac

grep -qE "^[[:space:]]*$user[[:space:]]+\\\$2[aby]\\\$" "$CADDYFILE" || die \
"'$user' 사용자 줄을 찾지 못했다: $CADDYFILE
  basicauth 블록 안의 '<사용자> <bcrypt해시>' 줄을 찾는다. 사용자명이 다르면 --user 로 준다."

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
candidate="$work/Caddyfile"

USERNAME="$user" HASH="$hash" awk '
    $1 == ENVIRON["USERNAME"] && $2 ~ /^\$2[aby]\$/ && !done {
        # 들여쓰기를 그대로 둔다. 블록 안이라 줄 모양이 바뀌면 읽기 어려워진다.
        match($0, /^[[:space:]]*/)
        printf "%s%s %s\n", substr($0, 1, RLENGTH), ENVIRON["USERNAME"], ENVIRON["HASH"]
        done = 1
        next
    }
    { print }
' "$CADDYFILE" > "$candidate"

if cmp -s "$candidate" "$CADDYFILE"; then
    ok "해시가 이미 같다 — 바꿀 것이 없다"
    exit 0
fi

say "바뀔 줄 (해시는 가린다)"
diff -u "$CADDYFILE" "$candidate" | sed -E 's/\$2[aby]\$[^"[:space:]]+/<BCRYPT>/g' || true

if [ "$dry_run" -eq 1 ]; then
    warn "--dry-run 이라 파일을 바꾸지 않았다."
    exit 0
fi

require_root

backup="$CADDYFILE.bak.$(date -u +%Y%m%dT%H%M%SZ)"
while [ -e "$backup" ]; do backup="$backup.1"; done
cp -p "$CADDYFILE" "$backup"
ok "백업 $backup"

cat "$candidate" > "$CADDYFILE"

if ! "$CADDY_BIN" validate --adapter caddyfile --config "$CADDYFILE"; then
    cp -p "$backup" "$CADDYFILE"
    die "caddy validate 실패 — 백업을 되돌렸고 reload 하지 않았다."
fi
ok "caddy validate 통과"

if ! systemctl reload caddy; then
    cp -p "$backup" "$CADDYFILE"
    systemctl reload caddy || true
    die "systemctl reload caddy 실패 — 백업을 되돌리고 다시 reload 했다."
fi
ok "반영 완료. 백업 파일에 옛 해시가 남아 있으니 확인 뒤 지운다: $backup"
