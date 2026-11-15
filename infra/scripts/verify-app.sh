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
check "뉴스레터 timer active" systemctl is-active --quiet stock-chatbot-newsletter.timer
check "뉴스레터 마지막 발송 성공" bash -c \
    "[ \"\$(systemctl show stock-chatbot-newsletter.service -p Result --value)\" = success ]"

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
# 같은 시간대를 "Etc/UTC"로 보고하는 호스트가 있다(이 서버가 그렇다). 이름만 달라 매 배포가 실패로 찍혔다
# (2026-11-11) — 늘 뜨는 실패는 진짜 실패를 가린다.
check "호스트 타임존 $HOST_TIMEZONE" bash -c \
    "case \"\$(timedatectl show -p Timezone --value)\" in '$HOST_TIMEZONE'|'Etc/$HOST_TIMEZONE') ;; *) exit 1 ;; esac"

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
if sudo journalctl -u stock-chatbot.service -u 'stock-chatbot-polymarket-*' \
    --since '24 hours ago' --no-pager -o cat 2>/dev/null | grep -F 'quota_exhausted' >/dev/null; then
    warn "최근 24시간 LLM 할당량 소진 — Cloudflare 유료 전환 또는 같은 계정의 다른 사용처 확인"
fi
check "봇 재시작 5회 미만" bash -c \
    "[ \"\$(systemctl show stock-chatbot -p NRestarts --value)\" -lt 5 ]"
check "Telegram 롱폴링 연결 있음" bash -c \
    "sudo ss -tnp state established 2>/dev/null | grep -q \"\$(getent hosts api.telegram.org | awk '{print \$1}' | head -1)\""

say "디스크"
sudo du -sh "$APP_DIR/storage" 2>/dev/null

say "공개 산출물 신선도·건수 (경고 전용)"
# 실제 웹 설정을 읽어 STORAGE_DIR 재정의도 따른다. 산출물 부재·지연은 서비스 점검의 실패 코드와 구분한다.
# 파일 나이만 보면 "작업이 멈춤"·"돌았지만 실패/예산 초과로 건너뜀"·"돌았는데 건수가 비정상적으로 적음"이 모두
# "N시간 전"으로만 보인다(2026-11-15, worldmonitor의 seed-meta·minRecordCount 방식을 따름). 그래서 각 파일 안의
# 생성 시각·상태·건수를 읽는다. 하한은 정상 실측의 절반 안팎이다 — 늘 뜨는 경고는 진짜 경고를 가린다.
if freshness=$(cd "$APP_DIR" && "$APP_DIR/venv/bin/python" - <<'PY'
from datetime import datetime
import json

from services.web.core.clock import now
from services.web.core.config import POLYMARKET_WEB_DIR, PUBLIC_DIR

current_time = now()


def load(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def hours_since(value):
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return None if stamp.tzinfo is None else (current_time - stamp).total_seconds() / 3600


def report(ok, text):
    print(f"{'ok' if ok else 'warn'} {text}")


def fresh(label, payload, key, limit_hours):
    """생성 시각이 limit_hours 안인지. 파일 mtime이 아니라 쓰는 쪽이 적은 시각을 본다."""
    if payload is None:
        report(False, f"{label} — 산출물 없음/읽기 실패")
        return False
    age = hours_since(payload.get(key))
    if age is None:
        report(False, f"{label} — 생성 시각({key}) 없음")
        return False
    report(0 <= age <= limit_hours, f"{label} — 생성 {age:.1f}시간 전(기준 {limit_hours}시간)")
    return True


# 예측 컨센서스 순회(4시간마다). 시도 시각과 성공 시각을 나눠 "작업 멈춤"과 "작업은 도는데 실패"를 가른다.
status = load(POLYMARKET_WEB_DIR / "status.json")
if status is None:
    report(False, "컨센서스 순회 상태 — status.json 없음/읽기 실패")
else:
    attempt = hours_since(status.get("last_attempt_at"))
    report(attempt is not None and attempt <= 4.5,
           f"컨센서스 순회 작업 — 마지막 시도 {'?' if attempt is None else f'{attempt:.1f}'}시간 전(기준 4.5시간)")
    result = status.get("last_result")
    if result == "success":
        report(True, "컨센서스 마지막 순회 성공")
    elif result == "skipped_budget":
        report(False, "컨센서스 마지막 순회 — 하루 예산 초과로 건너뜀")
    else:
        report(False, f"컨센서스 마지막 순회 실패 — {str(status.get('error') or result)[:80]}")

current = load(POLYMARKET_WEB_DIR / "current.json")
if fresh("current.json", current, "generated_at", 9):
    events = current.get("events") if isinstance(current.get("events"), list) else []
    # 정상 실측 18,900~21,900건(2026-09~11). 절반 아래면 순회가 일부만 담고 승격된 것이다.
    report(len(events) >= 10000 and current.get("coverage_status") == "complete",
           f"current.json — 열린 질문 {len(events):,}건, 수집 {current.get('coverage_status')}")

trending = load(POLYMARKET_WEB_DIR / "trending.json")
if fresh("trending.json", trending, "generated_at", 9):
    same = current is not None and trending.get("generation_id") == current.get("generation_id")
    report(same and trending.get("state") in {"ok", "warming_up"},
           f"trending.json — 상태 {trending.get('state')}, 후보 {trending.get('candidate_count')}건"
           + ("" if same else ", 현재 수집분과 세대가 다름"))

brief = load(POLYMARKET_WEB_DIR / "sector_brief.json")
if fresh("sector_brief.json", brief, "generated_at", 9):
    # partial은 해설이 실패한 분야만 직전 단락을 두는 설계된 대체 동작이라 건수만 보인다. 전부 실패해야 경고다.
    report(brief.get("state") in {"ok", "partial"},
           f"sector_brief.json — 상태 {brief.get('state')} {brief.get('group_counts')}")

# 검색 주석은 하루 Neurons 예산 안에서만 달린다. 예산 소진으로 건너뛴 주기는 정상 동작이라 경고하지 않고
# 주석 비율만 보인다. 작업이 멈췄거나 실패했을 때만 경고한다.
annotate = load(POLYMARKET_WEB_DIR / "annotate_status.json")
if annotate is None:
    report(False, "검색 주석 — annotate_status.json 없음/읽기 실패")
else:
    attempt = hours_since(annotate.get("last_attempt_at"))
    result = annotate.get("last_result")
    report(attempt is not None and attempt <= 9
           and result in {"ok", "up_to_date", "skipped_budget", "stopped_budget"},
           f"검색 주석 — {annotate.get('annotated')}/{annotate.get('total')}건, 마지막 {result}"
           f"({'?' if attempt is None else f'{attempt:.1f}'}시간 전)")

# 봇이 굽는 공개 산출물. 뉴스·보고서는 03·08·15·20시라 최대 간격 7시간, 지켜볼 뉴스·시장은 매시간이다.
news = load(PUBLIC_DIR / "news.json")
if fresh("news.json", news, "generated_at", 9):
    recent = [doc for doc in news.get("documents") or []
              if isinstance(doc, dict) and doc.get("kind") == "news"
              and (hours_since(doc.get("published_at")) or 99) <= 24]
    # 정상 실측 하루 60~115건(2026-10-17~11-15).
    report(len(recent) >= 20, f"news.json — 최근 24시간 뉴스 {len(recent)}건(기준 20건)")

market = load(PUBLIC_DIR / "market.json")
if fresh("market.json", market, "generated_at", 3):
    report(len(market.get("markets") or []) >= 5, f"market.json — 시장 {len(market.get('markets') or [])}개")

watch_news = load(PUBLIC_DIR / "watch_news.json")
if fresh("watch_news.json", watch_news, "generated_at", 3):
    report(len(watch_news.get("items") or []) >= 300, f"watch_news.json — {len(watch_news.get('items') or [])}건(72시간)")

fresh("research_inputs.json", load(PUBLIC_DIR / "research_inputs.json"), "generated_at", 9)

# 쇼츠는 하루 한 편(20시 제작 → 검수 → 게시)이라 이틀이 넘으면 하루를 건너뛴 것이다.
fresh("오늘의 영상(shorts/ko.json)", load(PUBLIC_DIR / "shorts" / "ko.json"), "uploaded_at", 48)
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
