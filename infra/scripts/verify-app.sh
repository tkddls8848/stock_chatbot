#!/usr/bin/env bash
# stock_chatbot 앱 점검. 공유 호스트에서 우리 몫만 본다.
#
# 호스트 자체(Orca, Tailscale, OS 계정, 방화벽)는 이 스크립트가 보지 않는다 —
# 그쪽은 호스트 저장소 remote_coding 의 scripts/util/verify-host.sh 가 소유한다.
# 앱 서비스(봇 포함)의 실행 계정·자동 시작은 여기서 본다.
# 인스턴스를 공유할 뿐 별개 프로젝트이므로 서로의 내부를 검사하지 않는다.
# 다만 우리가 기대고 있는 계약 값(호스트 타임존)은 여기서 확인한다.
#
# 실패해도 끝까지 확인한 뒤 비정상 종료한다.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
set +e

fail=0
check() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then ok "$label"; else warn "$label — 실패"; fail=1; fi
}

say "서비스"
check "봇 active" systemctl is-active --quiet stock-chatbot.service
check "봇 부팅 시 자동 시작" systemctl is-enabled --quiet stock-chatbot.service
check "읽기 웹 active" systemctl is-active --quiet stock-chatbot-web.service
check "읽기 웹 부팅 시 자동 시작" systemctl is-enabled --quiet stock-chatbot-web.service
check "Polymarket timer active" systemctl is-active --quiet stock-chatbot-polymarket-refresh.timer
check "Polymarket timer 부팅 시 자동 시작" systemctl is-enabled --quiet stock-chatbot-polymarket-refresh.timer

say "계정과 비밀"
# 호스트는 ubuntu 단일 계정이고 그 계정에 sudo 가 있다(remote_coding 의 결정).
# 앱 전용 계정·sudo 분리는 더 이상 검사하지 않고, 실제 실행 계정이 유닛과 같은지만 본다.
for unit in stock-chatbot.service stock-chatbot-web.service stock-chatbot-polymarket-refresh.service; do
    check "$unit 실행 계정 $APP_USER" bash -c \
        "[ \"\$(systemctl show '$unit' -p User --value)\" = '$APP_USER' ]"
done
check "운영 .env 소유권·권한" bash -c \
    "[ \"\$(sudo stat -c '%U:%G %a' '$APP_DIR/.env')\" = '$APP_USER:$APP_GROUP 600' ]"

say "웹 (루프백 전용)"
check "읽기 웹 localhost 200" curl -fsS "http://127.0.0.1:$WEB_PORT/"

# 헤더는 HEAD로 읽어 본문을 전송하지 않는다. 루프백에는 TLS 전용 HSTS가 없다.
web_headers=$(curl -fsSI "http://127.0.0.1:$WEB_PORT/")
has_web_header() { printf '%s\n' "$web_headers" | tr -d '\r' | grep -Eiq "$1"; }
check "웹 nosniff" has_web_header '^X-Content-Type-Options: nosniff$'
check "웹 Referrer-Policy" has_web_header '^Referrer-Policy: strict-origin-when-cross-origin$'
check "웹 Permissions-Policy" has_web_header '^Permissions-Policy: .*camera=\(\).*microphone=\(\).*geolocation=\(\)'
check "웹 CSP 해시" has_web_header "^Content-Security-Policy: .*script-src 'sha256-.*style-src 'sha256-"
check "웹 CSP 프레임 차단" has_web_header "^Content-Security-Policy: .*frame-ancestors 'none'"
check "Caddy HSTS 설정" sudo grep -Eq 'header Strict-Transport-Security "max-age=31536000; includeSubDomains"' /etc/caddy/Caddyfile
check "Caddy Server 헤더 제거" sudo grep -Eq 'header -Server' /etc/caddy/Caddyfile

# 앞단 Caddy 의 봇 차단은 우리 견본이 출처다(infra/Caddyfile.example). 실제 설정이
# 갈라지면 robots.txt 만 남고 강제는 사라지는데, 화면은 멀쩡해 보여 알아챌 수 없다.
# 어긋나면 infra/scripts/apply-caddy-bots.sh 로 다시 맞춘다.
check "Caddy 봇 차단 블록" bash -c \
    "sudo grep -qF '@aibots' /etc/caddy/Caddyfile"

say "호스트 계약"
# cron.d 는 타임존을 선언할 수 없어 호스트 설정을 그대로 따른다. 호스트가 UTC 가
# 아니면 아래 백업 시각의 의미가 통째로 바뀌므로 여기서 먼저 본다.
check "호스트 타임존 $HOST_TIMEZONE" bash -c \
    "[ \"\$(timedatectl show -p Timezone --value)\" = '$HOST_TIMEZONE' ]"

say "예약 작업"
check "일일 앱 백업 cron" test -f /etc/cron.d/stock-chatbot-backup
check "최근 24시간 내 백업 산출물" bash -c \
    "sudo find '$BACKUP_DIR' -maxdepth 1 -name 'backup-*.tgz' -mtime -1 | grep -q ."

# 자동 스냅샷(19:00 UTC)보다 한 시간 앞이어야 그날 백업이 스냅샷에 담긴다.
# 타임존을 JST 로 착각해 '0 3' 로 적으면 정오에 돌아 그 순서가 깨진다.
check "백업 크론 시각이 ${BACKUP_HOUR_UTC}:00 UTC" bash -c \
    "sudo grep -Eq '^0 $BACKUP_HOUR_UTC \* \* \* ' /etc/cron.d/stock-chatbot-backup"
check "백업 보관 ${BACKUP_RETENTION_DAYS}일" bash -c \
    "sudo grep -Fq -- '-mtime +$BACKUP_RETENTION_DAYS' /etc/cron.d/stock-chatbot-backup"

say "봇 안정성"
check "봇 재시작 5회 미만" bash -c \
    "[ \"\$(systemctl show stock-chatbot -p NRestarts --value)\" -lt 5 ]"
check "Telegram 롱폴링 연결 있음" bash -c \
    "sudo ss -tnp state established 2>/dev/null | grep -q \"\$(getent hosts api.telegram.org | awk '{print \$1}' | head -1)\""

say "디스크"
sudo du -sh "$APP_DIR/storage" 2>/dev/null

say "공개 산출물 신선도 (9시간 이내, 경고 전용)"
# 실제 웹 설정을 읽어 STORAGE_DIR 재정의도 따른다. 산출물 부재·지연은
# 서비스 점검의 실패 코드와 구분한다. 주석은 예산 때문에 지연될 수 있다.
if freshness=$(cd "$APP_DIR" && "$APP_DIR/venv/bin/python" - <<'PY'
from services.web.core.clock import now
from services.web.core.config import POLYMARKET_WEB_DIR

stamp = now().timestamp()
for name in ("current.json", "trending.json", "sector_brief.json", "search_index.json"):
    try:
        age = stamp - (POLYMARKET_WEB_DIR / name).stat().st_mtime
    except OSError:
        print(f"warn {name} — 산출물 없음/읽기 실패")
        continue
    state = "ok" if 0 <= age <= 9 * 3600 else "warn"
    print(f"{state} {name} — 갱신 {age / 3600:.1f}시간 전")
PY
); then
    while IFS=' ' read -r state label; do
        if [ "$state" = ok ]; then ok "$label"; else warn "$label"; fi
    done <<< "$freshness"
else
    warn "공개 산출물 신선도 — 설정/시각을 읽지 못함"
fi

if [ "$fail" -eq 0 ]; then
    ok "앱 점검 통과"
else
    warn "앱 점검 실패 항목이 있다"
fi
exit "$fail"
