#!/usr/bin/env bash
# 공유 호스트 orca-host-tokyo 에 stock_chatbot 런타임을 설치한다.
#
# 호스트 자체(인스턴스·고정 IP·공인 방화벽·스냅샷·타임존·Orca·Tailscale)는 건드리지
# 않는다 — 호스트 저장소가 소유한다(infra/host-contract.md).
# 앱과 .env/data 는 먼저 $APP_DIR 에 배치한다. 서비스는 의도적으로 시작하지 않는다.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_root
assert_backup_before_snapshot

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

# 배치가 끝난 체크아웃인지 확인한다. 봇과 웹이 각자 자기 패키지를 갖는다.
test -d "$APP_DIR/telegram_bot"
test -d "$APP_DIR/web"
test -f "$APP_DIR/requirements.txt"
test -f "$APP_DIR/.env"

chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
chmod 0750 "$APP_DIR"
chmod 0600 "$APP_DIR/.env"

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip

if [ ! -x "$APP_DIR/venv/bin/python" ]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
fi
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
if [ -f "$APP_DIR/requirements.lock.txt" ]; then
  sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.lock.txt"
else
  sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
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
  stock-chatbot-polymarket-brief.service; do
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
  "import sys; sys.path.insert(0, '$APP_DIR'); import telegram_bot.core.config, web.core.config"

if [ "$FIRST_INSTALL" = 1 ]; then
  ok "공유 호스트 런타임 설치 완료. 전환 시에만 서비스를 enable --now 한다."
else
  ok "유닛 재설치 완료. 실행 중인 서비스는 직접 restart 해야 새 유닛이 적용된다."
fi
