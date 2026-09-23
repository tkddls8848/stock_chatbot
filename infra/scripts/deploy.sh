#!/usr/bin/env bash
# 떠 있는 서버의 코드를 GitHub main 으로 갱신한다: pull → 설치 → 재시작 → 점검.
#
#   sudo /srv/stock-chatbot/infra/scripts/deploy.sh
#
# 체크아웃은 fast-forward 로만 받는다. 서버에서 추적 파일을 고쳐 두었으면 멈춘다 —
# 그 수정이 무엇인지 먼저 확인하고 저장소로 옮기거나 버린다(서버 핫픽스를 조용히
# 덮으면 무엇을 잃었는지 모른다). data/·.env·requirements.lock.txt 는 추적하지 않아
# 건드리지 않는다.
#
# 설치 스크립트가 유닛·의존성까지 다시 깔므로 디렉터리 구조나 실행 모듈이 바뀐 커밋도
# 이것 하나로 된다. 재시작은 이미 켜져 있던 서비스만 한다. 로컬에서 같은 텔레그램
# 토큰으로 봇을 켜 둔 채 돌리지 않는다(getUpdates Conflict).

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_root

as_app() { sudo -u "$APP_USER" -H "$@"; }

[ -z "$(as_app git -C "$APP_DIR" status --porcelain --untracked-files=no)" ] \
  || die "추적 파일이 수정돼 있다. git -C $APP_DIR diff 로 확인하고 정리한 뒤 다시 돌린다."
[ "$(as_app git -C "$APP_DIR" branch --show-current)" = "main" ] \
  || die "$APP_DIR 가 main 브랜치가 아니다."

before="$(as_app git -C "$APP_DIR" rev-parse --short HEAD)"
as_app env GIT_TERMINAL_PROMPT=0 git -C "$APP_DIR" pull --ff-only -q origin main
after="$(as_app git -C "$APP_DIR" rev-parse --short HEAD)"
say "코드 $before → $after"

"$SCRIPT_DIR/install-shared-host.sh"

for unit in stock-chatbot.service stock-chatbot-web.service; do
  if systemctl is-active --quiet "$unit"; then
    systemctl restart "$unit"
    ok "$unit 재시작"
  fi
done
sleep 8

"$SCRIPT_DIR/verify-app.sh"
