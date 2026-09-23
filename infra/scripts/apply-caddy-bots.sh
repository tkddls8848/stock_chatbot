#!/usr/bin/env bash
# 크롤·AI 봇 차단 블록을 호스트의 /etc/caddy/Caddyfile 에 반영한다.
#
# 견본(infra/Caddyfile.example)의 `# BEGIN aibots` ~ `# END aibots` 사이를 그대로
# 옮긴다. **손으로 옮기지 않는 이유는 갈라지기 때문이다.** UA 목록은 앱의
# robots.txt(services/web/pages/robots.py)와도 같아야 해서 출처가 하나여야 하는데, 편집기로
# 옮기면 목록을 고칠 때마다 세 곳이 어긋날 자리가 생기고 어긋난 것을 알아챌 방법이
# 없다. 여기서는 견본이 유일한 출처이고 이 스크립트가 그것을 그대로 복사한다.
#
# 여러 번 돌려도 결과가 같다. 표식이 이미 있으면 그 사이만 새 내용으로 바꾸고,
# 없으면 `header X-Robots-Tag` 줄 뒤에 넣는다. 바뀔 것이 없으면 파일을 건드리지
# 않는다 — 건드리지 않으면 reload 도 하지 않는다.
#
# 절차와 확인 방법은 infra/server-ops.md 11-6.
#
#   sudo infra/scripts/apply-caddy-bots.sh              # 반영하고 reload
#   infra/scripts/apply-caddy-bots.sh --dry-run         # 바뀔 내용만 보여준다(root 불필요)
#   sudo infra/scripts/apply-caddy-bots.sh --no-reload  # 파일만 바꾸고 reload 는 나중에

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"
CADDY_BIN="${CADDY_BIN:-caddy}"
EXAMPLE="$INFRA_DIR/Caddyfile.example"
BEGIN_MARK="# BEGIN aibots"
END_MARK="# END aibots"
ANCHOR="header X-Robots-Tag"

dry_run=0
reload=1
for arg in "$@"; do
    case "$arg" in
        --dry-run)   dry_run=1 ;;
        --no-reload) reload=0 ;;
        *) die "모르는 인자: $arg (쓸 수 있는 것: --dry-run, --no-reload)" ;;
    esac
done

[ -f "$EXAMPLE" ] || die "견본을 찾지 못했다: $EXAMPLE"
[ -f "$CADDYFILE" ] || die "Caddyfile 이 없다: $CADDYFILE
  (다른 경로면 CADDYFILE=/경로 로 지정한다)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
block="$work/block"

awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
    index($0, b) { inside = 1 }
    inside       { print }
    index($0, e) { inside = 0 }
' "$EXAMPLE" > "$block"
# 표식이 지워졌거나 이름이 바뀌면 여기서 멈춘다. 빈 블록을 조용히 넣으면
# 차단이 사라진 채로 reload 까지 성공해 버린다.
grep -qF "$END_MARK" "$block" || die "견본에서 '$BEGIN_MARK' ~ '$END_MARK' 블록을 찾지 못했다: $EXAMPLE"

candidate="$work/Caddyfile"
if grep -qF "$BEGIN_MARK" "$CADDYFILE"; then
    grep -qF "$END_MARK" "$CADDYFILE" || die "'$BEGIN_MARK' 는 있는데 '$END_MARK' 가 없다: $CADDYFILE
  (표식 한 쪽만 남으면 어디까지 바꿔야 할지 알 수 없다. 손으로 맞춘 뒤 다시 돌린다)"
    awk -v b="$BEGIN_MARK" -v e="$END_MARK" -v blockfile="$block" '
        index($0, b) && !done { while ((getline line < blockfile) > 0) print line
                                close(blockfile); done = 1; skip = 1; next }
        skip && index($0, e)  { skip = 0; next }
        skip                  { next }
                              { print }
    ' "$CADDYFILE" > "$candidate"
elif grep -qF "$ANCHOR" "$CADDYFILE"; then
    awk -v a="$ANCHOR" -v blockfile="$block" '
        { print }
        index($0, a) && !done { print ""
                                while ((getline line < blockfile) > 0) print line
                                close(blockfile); done = 1 }
    ' "$CADDYFILE" > "$candidate"
else
    die "붙일 자리를 찾지 못했다: $CADDYFILE
  '$ANCHOR' 줄도 '$BEGIN_MARK' 표식도 없다. 견본과 구조가 다르다는 뜻이므로
  자동으로 넣지 않는다. 견본(infra/Caddyfile.example)을 보고 표식 두 줄을 먼저
  넣은 뒤 다시 돌린다."
fi

if cmp -s "$candidate" "$CADDYFILE"; then
    ok "이미 견본과 같다 — 바꿀 것이 없다: $CADDYFILE"
    exit 0
fi

say "바뀔 내용"
diff -u "$CADDYFILE" "$candidate" || true

if [ "$dry_run" -eq 1 ]; then
    warn "--dry-run 이라 파일을 바꾸지 않았다."
    exit 0
fi

require_root

backup="$CADDYFILE.bak.$(date -u +%Y%m%dT%H%M%SZ)"
# 같은 초에 두 번 돌면 이름이 겹친다. 덮어쓰면 직전 백업을 잃는다.
while [ -e "$backup" ]; do backup="$backup.1"; done
cp -p "$CADDYFILE" "$backup"
ok "백업 $backup"

# 리다이렉트가 아니라 cat 으로 덮어쓴다. 소유권·권한·심볼릭 링크를 그대로 둔다.
cat "$candidate" > "$CADDYFILE"

if ! "$CADDY_BIN" validate --adapter caddyfile --config "$CADDYFILE"; then
    cp -p "$backup" "$CADDYFILE"
    die "caddy validate 실패 — 백업을 되돌렸고 reload 하지 않았다."
fi
ok "caddy validate 통과"

if [ "$reload" -eq 0 ]; then
    warn "--no-reload 라 반영하지 않았다. 나중에: sudo systemctl reload caddy"
    exit 0
fi

if ! systemctl reload caddy; then
    cp -p "$backup" "$CADDYFILE"
    systemctl reload caddy || true
    die "systemctl reload caddy 실패 — 백업을 되돌리고 다시 reload 했다."
fi
ok "reload 완료. 확인: curl -sS -A GPTBot -o /dev/null -w '%{http_code}\\n' https://nunchi.live/"
