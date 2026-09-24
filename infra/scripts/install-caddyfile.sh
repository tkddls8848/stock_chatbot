#!/usr/bin/env bash
# 견본(infra/Caddyfile.example)으로 호스트의 /etc/caddy/Caddyfile 을 만든다.
#
# 손으로 쓰지 않는 이유는 빠뜨리기 때문이다. 첫 줄에 `http://` 를 붙이면 자동
# HTTPS 가 꺼지고, bind 를 빼면 Tailscale 과 443 을 다투다 기동하지 못한다. 견본은
# 그 둘을 이미 맞춰 두었으므로 바꿀 것은 도메인·사설 IP 둘뿐이다. 인증은 없다.
#
# 봇 차단 블록도 견본에 들어 있어 따로 적용할 필요가 없다. 나중에 목록만 고칠
# 때는 apply-caddy-bots.sh 를 쓴다.
#
# 절차는 infra/server-ops.md 11-2.
#
#   sudo infra/scripts/install-caddyfile.sh --domain nunchi.live --bind 172.26.x.x
#   sudo infra/scripts/install-caddyfile.sh --domain ... --bind ... --dry-run

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"
CADDY_BIN="${CADDY_BIN:-caddy}"
EXAMPLE="$INFRA_DIR/Caddyfile.example"

domain=""
bind_ip=""
dry_run=0
while [ $# -gt 0 ]; do
    case "$1" in
        --domain) domain="${2:-}"; shift 2 ;;
        --bind)   bind_ip="${2:-}"; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        *) die "모르는 인자: $1 (쓸 수 있는 것: --domain, --bind, --dry-run)" ;;
    esac
done

[ -n "$domain" ] || die "--domain 이 필요하다 (예: --domain nunchi.live)"
[ -n "$bind_ip" ] || die "--bind 가 필요하다. Lightsail 사설 NIC 주소다:
  ip -4 -o addr show | awk '{print \$2, \$4}'"
[ -f "$EXAMPLE" ] || die "견본을 찾지 못했다: $EXAMPLE"

case "$domain" in
    # 붙이면 자동 HTTPS 가 꺼진다. 견본 주석이 경고하는 바로 그 실수다.
    http://*|https://*) die "--domain 에는 스킴을 붙이지 않는다: $domain" ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
candidate="$work/Caddyfile"

# 견본의 자리표시자만 바꾼다. **주석 줄은 건드리지 않는다** — 주석에 적힌 예시까지
# 바뀌면 견본을 설명하는 문장이 틀어진다.
DOMAIN="$domain" BIND_IP="$bind_ip" awk '
    /^[[:space:]]*#/ { print; next }
    { gsub(/example\.com/, ENVIRON["DOMAIN"])
      gsub(/PRIVATE_IP/,   ENVIRON["BIND_IP"])
      print }
' "$EXAMPLE" > "$candidate"

# 주석을 뺀 본문에 자리표시자가 남았는지 본다.
grep -v '^[[:space:]]*#' "$candidate" | grep -qE "PRIVATE_IP|example\.com" && \
    die "자리표시자가 남았다 — 견본 형식이 바뀌었다: $EXAMPLE"

if [ -f "$CADDYFILE" ] && cmp -s "$candidate" "$CADDYFILE"; then
    ok "이미 같다 — 바꿀 것이 없다: $CADDYFILE"
    exit 0
fi

say "적용될 내용"
cat "$candidate"

if [ "$dry_run" -eq 1 ]; then
    warn "--dry-run 이라 파일을 쓰지 않았다."
    exit 0
fi

require_root

if [ -f "$CADDYFILE" ]; then
    backup="$CADDYFILE.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    while [ -e "$backup" ]; do backup="$backup.1"; done
    cp -p "$CADDYFILE" "$backup"
    ok "백업 $backup"
    cat "$candidate" > "$CADDYFILE"
else
    install -m 0644 "$candidate" "$CADDYFILE"
    backup=""
fi

if ! "$CADDY_BIN" validate --adapter caddyfile --config "$CADDYFILE"; then
    if [ -n "$backup" ]; then cp -p "$backup" "$CADDYFILE"; else rm -f "$CADDYFILE"; fi
    die "caddy validate 실패 — 되돌렸고 reload 하지 않았다."
fi
ok "caddy validate 통과"

# 설치 직후에는 아직 안 떠 있을 수 있다. reload 가 실패하면 restart 로 올린다.
systemctl reload caddy || systemctl restart caddy
ok "반영 완료. 인증서 확인: journalctl -u caddy -n 30 --no-pager | grep -i certificate"
