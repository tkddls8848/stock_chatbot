#!/usr/bin/env bash
# 앱 설치·점검 스크립트가 공통으로 쓰는 설정과 출력 헬퍼. 직접 실행하지 않고 source 한다.
#
# 이 앱은 공유 호스트 orca-host-tokyo-v2 의 입주 앱이다. AWS 자원(인스턴스·고정 IP·공인
# 방화벽·스냅샷)과 OS 기본 설정은 여기서 다루지 않는다 — 호스트 저장소 remote_coding 이
# 소유한다. 계약 값과 경계는 infra/host-contract.md 에 있다.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 로컬에서만 값을 바꿔 시험할 때 쓴다. 커밋하지 않는다.
# shellcheck disable=SC1091
[ -f "$SCRIPT_DIR/config.env" ] && . "$SCRIPT_DIR/config.env"

# --- 앱 배치 ----------------------------------------------------------------
# 호스트가 서버를 ubuntu 단일 계정으로 통합했다(2026-09-23, remote_coding
# docs/server-migration.md). 봇·웹·Orca 가 모두 ubuntu 로 돌고, 앱 전용 계정은
# 두지 않는다. systemd 유닛의 User/Group 도 같은 값이다.
APP_USER="${APP_USER:-ubuntu}"
APP_GROUP="${APP_GROUP:-ubuntu}"
APP_DIR="${APP_DIR:-/srv/stock-chatbot}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/stock-chatbot}"

# 앱 내부 포트. 127.0.0.1 전용이며 공인 방화벽에 열지 않는다.
WEB_PORT="${WEB_PORT:-8788}"    # 읽기 웹 (앞단 Caddy 가 TLS·인증)

# --- 호스트와의 계약 값 (호스트 소유, 여기서는 따라간다) --------------------
# 호스트 타임존은 UTC 고정이고 cron.d 는 타임존을 선언할 수 없다. Lightsail 자동
# 스냅샷도 UTC 정시이므로, 앱 백업은 스냅샷보다 한 시간 앞(18:00 UTC = 03:00 KST/JST)에
# 끝낸다. 호스트가 스냅샷 시각을 옮기면 이 값도 함께 옮긴다.
HOST_TIMEZONE="${HOST_TIMEZONE:-UTC}"
AUTO_SNAPSHOT_TIME_UTC="${AUTO_SNAPSHOT_TIME_UTC:-19:00}"
BACKUP_HOUR_UTC="${BACKUP_HOUR_UTC:-18}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m  xx\033[0m %s\n' "$*" >&2; exit 1; }

require_root() {
    [ "$(id -u)" -eq 0 ] || die "root 로 실행해야 한다: sudo $0"
}

# 백업이 자동 스냅샷 앞에 끝나는지 확인한다. 두 값이 갈라지면 스냅샷에 그날 백업이
# 빠진 채로 담기고, 아무도 실패를 보지 못한다.
assert_backup_before_snapshot() {
    local snapshot_hour="${AUTO_SNAPSHOT_TIME_UTC%%:*}"
    snapshot_hour="$((10#$snapshot_hour))"
    [ "$((10#$BACKUP_HOUR_UTC))" -eq "$((snapshot_hour - 1))" ] || die \
"백업 시각(${BACKUP_HOUR_UTC}:00 UTC)이 자동 스냅샷(${AUTO_SNAPSHOT_TIME_UTC} UTC) 한 시간 전이 아니다.
  호스트 저장소에서 스냅샷 시각을 옮겼다면 lib.sh 의 BACKUP_HOUR_UTC 도 함께 옮긴다
  (infra/host-contract.md 참고)."
}
