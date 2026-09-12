#!/usr/bin/env bash
# 기존 Orca/Lightsail 공유 호스트에 stock_chatbot 런타임을 설치한다.
# 앱과 .env/data는 먼저 /srv/stock-chatbot에 배치한다. 서비스는 의도적으로 시작하지 않는다.

set -euo pipefail

APP_USER=stockbot
APP_GROUP=stockbot
APP_DIR=/srv/stock-chatbot
BACKUP_DIR=/var/backups/stock-chatbot

if [ "$(id -u)" -ne 0 ]; then
  echo "root로 실행해야 한다: sudo $0" >&2
  exit 1
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

test -d "$APP_DIR/telegram_bot"
test -d "$APP_DIR/shared"
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

install -o root -g root -m 0644 \
  "$APP_DIR/infra/systemd/stock-chatbot.service" \
  /etc/systemd/system/stock-chatbot.service

for unit in \
  stock-chatbot-web.service \
  stock-chatbot-polymarket-refresh.service \
  stock-chatbot-polymarket-refresh.timer \
  stock-chatbot-polymarket-brief.service; do
  install -o root -g root -m 0644 "$APP_DIR/infra/systemd/$unit" "/etc/systemd/system/$unit"
done

install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "$BACKUP_DIR"
install -o root -g root -m 0644 "$APP_DIR/infra/systemd/stock-chatbot-backup.cron" \
  /etc/cron.d/stock-chatbot-backup

systemctl daemon-reload
if [ "$FIRST_INSTALL" = 1 ]; then
  systemctl disable stock-chatbot.service stock-chatbot-web.service \
    stock-chatbot-polymarket-refresh.timer >/dev/null 2>&1 || true
fi

sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" -c \
  "import sys; sys.path.insert(0, '$APP_DIR'); import shared.core.config"

if [ "$FIRST_INSTALL" = 1 ]; then
  echo "공유 호스트 런타임 설치 완료. 전환 시에만 서비스를 enable --now 한다."
else
  echo "유닛 재설치 완료. 실행 중인 서비스는 직접 restart 해야 새 유닛이 적용된다."
fi
