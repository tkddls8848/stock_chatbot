#!/usr/bin/env bash
# 공유 호스트 orca-host-tokyo-v2 에 stock_chatbot 런타임을 설치한다.
#
# 두 저장소의 경계: 호스트 저장소 remote_coding 은 AWS 환경(인스턴스·고정 IP·공인
# 방화벽·스냅샷·Orca·Tailscale·OS 계정)만 만든다. 그 위에 서비스를 심는 일 —
# 체크아웃·venv·유닛·cron·점검 — 은 전부 이 저장소 infra/ 가 한다(infra/host-contract.md).
#
# 체크아웃은 먼저 $APP_DIR 에 있어야 한다(최초 clone 은 host-contract.md, 갱신은 deploy.sh).
# 이 스크립트는 venv·의존성, systemd 유닛 전부, 백업 cron 을 깐다. 계정은 만들지
# 않는다 — 호스트가 만든 ubuntu 를 쓴다. 처음 설치할 때는 서비스를 시작하지 않는다.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_root
assert_backup_before_snapshot

id "$APP_USER" >/dev/null 2>&1 || die "$APP_USER 계정이 없다. 호스트 환경 구축이 먼저다."
test -d "$APP_DIR/.git" || die "$APP_DIR 체크아웃이 없다. host-contract.md 대로 clone 한다."
test -d "$APP_DIR/services/telegram_bot" || die "체크아웃에 services/telegram_bot 이 없다."
test -d "$APP_DIR/services/web" || die "체크아웃에 services/web 이 없다."
test -f "$APP_DIR/requirements.txt" || die "requirements.txt 가 없다."
test -f "$APP_DIR/.env" || die "$APP_DIR/.env 가 없다."

chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
chmod 0750 "$APP_DIR"
chmod 0600 "$APP_DIR/.env"

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip

if [ ! -x "$APP_DIR/venv/bin/python" ]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
fi
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q --upgrade pip
# requirements.lock.txt 는 옛 서버의 실제 패키지 버전을 담아 온 미추적 파일이다.
# 있으면 그것을 따른다 — requirements.txt 에 패키지를 추가하면 여기에도 반영해야 한다.
if [ -f "$APP_DIR/requirements.lock.txt" ]; then
  sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.lock.txt"
else
  sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
fi

# 최초 설치와 재설치를 구분한다. 재설치는 이미 켜 둔 서비스를 끄지 않는다.
if [ -f /etc/systemd/system/stock-chatbot.service ]; then
  FIRST_INSTALL=0
else
  FIRST_INSTALL=1
fi

for unit in \
  stock-chatbot.service \
  stock-chatbot-web.service \
  stock-chatbot-polymarket-refresh.service \
  stock-chatbot-polymarket-refresh.timer \
  stock-chatbot-polymarket-brief.service \
  stock-chatbot-polymarket-trending.service \
  stock-chatbot-polymarket-annotate.service; do
  install -o root -g root -m 0644 "$INFRA_DIR/systemd/$unit" "/etc/systemd/system/$unit"
done

# 백업 cron 은 시각·보관 기간·경로가 lib.sh 한 곳에서만 나오도록 렌더링한다.
# 손으로 적으면 자동 스냅샷 시각과 갈라져도 아무도 알아채지 못한다.
install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "$BACKUP_DIR"
cron_rendered="$(mktemp)"
trap 'rm -f "$cron_rendered"' EXIT
sed \
  -e "s|__BACKUP_HOUR__|$BACKUP_HOUR_UTC|g" \
  -e "s|__SNAPSHOT_TIME__|$AUTO_SNAPSHOT_TIME_UTC|g" \
  -e "s|__APP_USER__|$APP_USER|g" \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  -e "s|__BACKUP_DIR__|$BACKUP_DIR|g" \
  -e "s|__RETENTION_DAYS__|$BACKUP_RETENTION_DAYS|g" \
  "$INFRA_DIR/systemd/stock-chatbot-backup.cron.tmpl" > "$cron_rendered"
install -o root -g root -m 0644 "$cron_rendered" /etc/cron.d/stock-chatbot-backup

systemctl daemon-reload
if [ "$FIRST_INSTALL" = 1 ]; then
  systemctl disable stock-chatbot.service stock-chatbot-web.service \
    stock-chatbot-polymarket-refresh.timer >/dev/null 2>&1 || true
fi

# 프로세스마다 자기 설정을 따로 읽는다. 한쪽이 깨져도 다른 쪽은 뜨지만,
# 설치 직후에는 둘 다 import되는지 확인한다.
sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" -c \
  "import sys; sys.path.insert(0, '$APP_DIR'); import services.telegram_bot.main, services.web.server"

if [ "$FIRST_INSTALL" = 1 ]; then
  ok "런타임 설치 완료. 전환 시에만 서비스를 enable --now 한다."
else
  ok "재설치 완료. 실행 중인 서비스는 restart 해야 새 유닛·코드가 적용된다(deploy.sh 가 한다)."
fi
