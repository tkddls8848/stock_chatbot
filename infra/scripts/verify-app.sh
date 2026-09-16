#!/usr/bin/env bash
# stock_chatbot 앱 점검. 공유 호스트에서 우리 몫만 본다.
#
# 호스트 자체(Orca, Tailscale, OS 계정, 방화벽)는 이 스크립트가 보지 않는다 —
# 그쪽은 호스트 저장소의 scripts/util/verify-host.sh 가 소유한다.
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
check "$APP_USER 전용 계정" id "$APP_USER"
check "운영 .env 소유권·권한" bash -c \
    "[ \"\$(sudo stat -c '%U:%G %a' '$APP_DIR/.env')\" = '$APP_USER:$APP_GROUP 600' ]"
check "$APP_USER sudo 그룹 아님" bash -c \
    "! id -nG '$APP_USER' | tr ' ' '\n' | grep -qx -e sudo -e admin"

say "웹 (루프백 전용)"
check "읽기 웹 localhost 200" curl -fsS "http://127.0.0.1:$WEB_PORT/"
check "관리 웹 localhost 인증 요구" bash -c \
    "[ \"\$(curl -sS -o /dev/null -w '%{http_code}' 'http://127.0.0.1:$ADMIN_PORT/')\" = 401 ]"

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
sudo du -sh "$APP_DIR/data" 2>/dev/null

if [ "$fail" -eq 0 ]; then
    ok "앱 점검 통과"
else
    warn "앱 점검 실패 항목이 있다"
fi
exit "$fail"
