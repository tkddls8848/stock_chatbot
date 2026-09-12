# Stock Chatbot

중국·홍콩·한국·글로벌 시장의 뉴스와 종목 정보를 수집하고, 3시간 시장상황 보고서·감성 분석·관심 종목 관리·브리핑을 제공하는 주식 시장 정보 텔레그램 봇입니다.

## 시작하기

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m telegram_bot.main
```

`.env`에는 아래 값이 필요합니다.

```env
TELEGRAM_BOT_TOKEN=<BotFather 토큰>
TELEGRAM_CHAT_ID=<알림을 받을 채팅 또는 채널 ID>
CLOUDFLARE_ACCOUNT_ID=<Cloudflare 계정 ID>
CLOUDFLARE_API_TOKEN=<Workers AI 실행 권한 토큰>
```

환경 변수로 조정하는 설정과 기본값은 [`.env.example`](.env.example)에서 확인할 수 있습니다.

### LLM은 Cloudflare Workers AI를 사용합니다

시장상황·감성·리서치·브리핑 분석이 모두 Cloudflare Workers AI(`@cf/qwen/qwen3-30b-a3b-fp8`)로 동작합니다. 로컬 GPU나 별도 추론 서버가 필요 없어서 **1GB 메모리 무료 VM에서도 돌아갑니다.**

- 무료 한도는 **하루 10,000 Neurons**이며 UTC 00시(UTC +9 오전 9시)에 리셋됩니다. 리서치 분석은 입력 깊이를 늘린 뒤(뉴스 16건 × 본문 600자, 후보 24개) 1회에 약 400~600 Neurons로 추정되며, 이전의 얕은 입력(6건 × 240자) 기준 실측치는 약 110 Neurons였습니다.
- 예약 뉴스는 매시간 원문을 모으고 UTC +9 기준 3시간마다 시장별로 한 번씩 분석합니다. 따라서 LLM 호출 수는 기사 수가 아니라 보고서에 포함된 시장 수에 비례합니다.
- 한도가 소진되면 다음 리셋까지 호출을 멈춥니다. 그날 시장상황 보고서와 `/research`는 실패하지만, 브리핑은 지수·헤드라인만 담은 데이터 전용 브리핑으로 자동 전환됩니다.
- 실사용량은 로그에 그대로 남으며 일일 합계는 UTC 00시(한국시간 오전 9시)를 경계로 셉니다. 로그는 파일이 아니라 표준 오류로 나가므로, 서버에서는 `journalctl -u stock-chatbot | grep neurons=`로, 로컬에서는 `python -m telegram_bot.main 2> bot.log`처럼 받아 두고 확인합니다.
- 한도를 넘겨 쓰려면 Workers Paid 플랜에서 초과분이 1,000 Neurons당 $0.011입니다.
- API 토큰은 `.env`에만 두고 커밋하지 않습니다. 로그와 예외 메시지에는 토큰이 남지 않습니다.

## 배포

AWS Lightsail에 배포하며, 인스턴스·고정 IP·방화벽과 부트스트랩(스왑·systemd·백업 cron)이 Terraform으로 코드화되어 있습니다.

```powershell
cd iac\terraform
Copy-Item terraform.tfvars.example terraform.tfvars   # ssh_public_key_path 등 편집
terraform init; terraform apply
terraform output cutover_commands                     # 전환 절차
```

- 실행 절차와 주의점은 [`infra/terraform/README.md`](infra/terraform/README.md)에 있습니다.
- 부트스트랩은 봇을 **기동하지 않습니다.** 같은 토큰으로 두 프로세스가 텔레그램을 폴링하면 양쪽이 번갈아 죽으므로, 전환은 "로컬 정지 → 서버 기동" 순서로 사람이 진행합니다.

## 테스트

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

실제 Cloudflare 계정을 호출하는 스모크 테스트는 기본 실행에서 제외되며, 자격증명과 무료 할당량을 소비합니다.

```powershell
$env:RUN_CLOUDFLARE_SMOKE='1'
python -m pytest -q -m cloudflare_smoke
```

Polymarket 읽기 스모크도 같은 방식으로 제외되어 있습니다. Gamma
`/events/keyset`이 이 출구 IP에서 읽히는지, 커서가 실제로 전진하는지를 봅니다.
인증과 LLM을 쓰지 않아 Neurons를 소비하지 않지만, 운영 서버는 출구 IP가 달라
로컬에서 열린다고 서버에서 열리는 것이 아닙니다. 새 인스턴스에서 한 번 돌립니다.

```bash
RUN_POLYMARKET_SMOKE=1 python -m pytest -q -m polymarket_smoke
```

폴리마켓 현재 대시보드는 봇과 별개인 systemd one-shot이 2시간마다 굽고, 공개
웹이 그 산출물을 내보냅니다. 설치·상태·장애 절차는 `infra/server-ops.md` 8절에
있습니다. compact manifest 크기는 다음으로 잽니다.

```powershell
.\venv\Scripts\python.exe tests\polymarket_manifest_size_probe.py
```

## 주요 기능

- 중국·홍콩·미국·한국·글로벌 시장 뉴스 수집과 3시간 시장상황 보고서
- 시장별 뉴스 감성 차트 (`/market`)
- 관심 종목 뉴스·감성 요약
- 뉴스 기반 시장 리서치 후보 관리 (중화권·미국·한국 균형 수집과 추천)
- 개장 전·마감 브리핑
- 중국·홍콩·한국·미국 종목 DB

## 텔레그램 명령

| 명령 | 설명 |
|---|---|
| `/start`, `/help` | 사용 안내와 메뉴 표시 |
| `/market [일수]` | 시장별 뉴스 감성 차트 |
| `/menu`, `/list` | 관심 종목 목록 |
| `/add 종목코드` | 관심 종목 추가 |
| `/view [종목코드]` | 종목별 뉴스 감성 |
| `/research show\|set\|run\|clear` | 리서치 후보 관리 |
| `/briefing morning\|evening` | 브리핑 생성 |
| `/stockdb build` | 종목 DB 갱신 |
| `/system [features\|prefilter\|anomaly]` | 시스템 상태, 기능 카탈로그, 뉴스 사전선별 섀도 비교, 시장 아노말리 |

## 관리 웹 (선택)

봇 프로세스에 내장되는 관리용 웹 대시보드로, 관심 종목·뉴스·리서치·시스템 상태를 브라우저에서 확인·관리합니다. 다른 기능과 같이 `shared/core/config.py`의 `FEATURES_ENABLED`에 `web_admin` 키가 들어 있으면 켜지며, 봇을 제어하므로 비밀번호를 지정해야만 기동합니다. 호스트·포트는 `127.0.0.1:8787` 고정 리터럴이고, 사용자·비밀번호만 `.env`에 둡니다.

```env
WEB_ADMIN_USER=admin
WEB_ADMIN_PASSWORD=<반드시 지정>
```

- `WEB_ADMIN_PASSWORD`를 지정하지 않으면 기능이 켜져 있어도 자동으로 건너뜁니다.
- 모든 요청은 HTTP Basic 인증 뒤에 있으며, 봇과 같은 이벤트 루프에서 동작해 텔레그램과 상태를 공유합니다.
- 외부에 노출할 때는 HTTPS 역방향 프록시(예: Nginx/Caddy/Cloudflare) 뒤에 두는 것을 권장합니다.

접속 후 `http://<호스트>:<포트>/`에서 시스템 상태, 관심 종목 추가·삭제, 최근 뉴스, 리서치 후보를 확인할 수 있습니다.

## 종목 DB

`/stockdb build`는 AkShare에서 중국·홍콩 종목을, FinanceDataReader와 Nasdaq Trader에서 각각 한국·미국 전체 상장종목을 수집합니다.

종목 DB는 `data/instruments/stock_db.json`에 캐시됩니다. 외부 식별자 매핑 API는 사용하지 않습니다.

## 데이터와 접근 제어

- `data/`에는 관심 종목, 발송 이력, 뉴스·신호 로그, 종목 DB가 소유 기능별 하위 디렉토리(`news/`, `watchlist/`, `instruments/`, `signal_scoring/`, `research/`, `runtime/`)에 저장됩니다.
- `ALLOWED_CHAT_IDS`에 쉼표로 구분한 채팅 ID를 설정해야 하며, 여기 적힌 채팅에서만 명령을 처리합니다. 비워 두거나 유효한 ID가 하나도 없으면 봇이 기동하지 않습니다 — 개인 운영용이라 빈 값을 전체 허용으로 해석하지 않습니다.
- 뉴스·시세 제공처가 일시적으로 실패해도 다른 기능은 계속 동작하며, 다음 주기에 다시 수집합니다.

## 프로젝트 구조

최상위를 프로세스 경계로 나눕니다. `telegram_bot`·`web`은 서로를 import하지 않고 `shared`만 공유합니다. `shorts`는 아예 import하지 않고 공개 웹 API만 HTTP로 읽습니다.

각 도메인이 자기 코드·테스트·계획서를 자기 폴더 안에 담고, 인프라 코드는 전부 `infra/`에 모읍니다. 최상위에 `tests/`·`docs/`·`deploy/`를 따로 두지 않습니다.

```text
shared/           공유 — core(설정·시각·저장·워커), llm(Cloudflare), prompts/, tests/
telegram_bot/     텔레그램 봇 — 명령 처리, 뉴스, 종목 DB, 관심 종목, 8787 관리 웹
                  + docs/ tests/
web/              읽기 전용 공개 웹 (8788) + docs/ tests/
  polymarket/     폴리마켓 화면을 먹이는 순회·줄글 one-shot (봇과 무관)
shorts/           쇼츠 영상 자동 생성. 자기 pyproject·venv를 가진 별개 패키지
                  + src/ docs/ tests/
infra/            인프라 코드 — terraform/, systemd/, scripts/, Caddyfile, server-ops.md
data/             실행 중 생성되는 상태·캐시 데이터, 소유 기능별 하위 디렉토리 (Git 제외)
```

저장소 루트가 import root입니다. 진입점은 모두 루트에서 `-m`으로 부릅니다.

> 텔레그램 봇 폴더는 `telegram`으로 지을 수 없습니다. `python-telegram-bot`이 제공하는 최상위 모듈 이름이 `telegram`이라, import root에 같은 이름을 두면 `from telegram import Update`가 전부 이 폴더를 집습니다.

| 프로세스 | 명령 |
|---|---|
| 텔레그램 봇(+8787 관리 웹) | `python -m telegram_bot.main` |
| 공개 웹 8788 | `python -m web.server` |
| 폴리마켓 순회 one-shot | `python -m web.polymarket.refresh` |
| 폴리마켓 줄글 one-shot | `python -m web.polymarket.sector_brief` |
| 쇼츠(별개 venv, `shorts/`에서) | `python -m polymarket_shorts.cli` |
