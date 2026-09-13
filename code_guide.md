# stock_chatbot 코드 작성 기준

**이 저장소의 유일한 기준 문서다.** 코드·구조·검증·리팩터링 판단이 부딪히면
이 문서가 이긴다. 다른 문서(`infra/server-ops.md`, `infra/terraform/README.md`,
도메인별 `docs/` 계획서)는 절차와 계획만 담고 기준을 새로 정하지 않는다.

> **에이전트는 포인터를 통해 이 문서에 닿는다.** 도구마다 자동으로 읽는 파일이
> 달라서(`CLAUDE.md`·`AGENTS.md`)
> 각각 "`code_guide.md`를 읽어라"만 적힌 포인터를 두었다. **규칙을 포인터에
> 옮겨 적지 않는다** — 두 벌이 되면 갈라지고, 그게 이 문서를 하나로 합친
> 이유다. 포인터를 고칠 일은 실행 명령이 바뀔 때뿐이다.

개인 운영용 Telegram 주식 봇이다. 중국·홍콩·미국·한국 뉴스, 종목 DB, 관심종목,
시장 감성, 리서치, 브리핑을 제공하고 공개 웹(`https://nunchi.live`)과 쇼츠 영상
자동 생성을 곁들인다. 현재 데이터 형식과 현재 설정만 지원하며 과거 형식
마이그레이션이나 비활성 대체 경로를 추가하지 않는다.

## 실행과 검증

```powershell
.\venv\Scripts\python.exe -m telegram_bot.main
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m pytest -q shorts/tests   # 루트 pytest에 안 잡힌다
.\venv\Scripts\python.exe -m ruff check telegram_bot web conftest.py tests
```

- Python 3.11+, `requirements.txt`, 개발 도구는 `requirements-dev.txt`.
- **저장소 루트가 import root다.** 모든 명령을 루트에서 돌린다. 내부 import는
  `from telegram_bot.core...`, `from telegram_bot.news...`, `from web.polymarket...`
  형식이다.
- 진입점은 넷이고 전부 루트에서 `-m`으로 부른다. 스크립트 경로로 부르면
  `sys.path[0]`이 하위 폴더가 되어 `ModuleNotFoundError: telegram_bot`이 난다.
  | 프로세스 | 명령 |
  |---|---|
  | 텔레그램 봇(+8787 관리 웹) | `python -m telegram_bot.main` |
  | 공개 웹 8788 | `python -m web.server` |
  | 폴리마켓 순회 one-shot | `python -m web.polymarket.refresh` |
  | 폴리마켓 줄글 one-shot | `python -m web.polymarket.sector_brief` |
- **텔레그램 봇 폴더를 `telegram`으로 이름 붙이지 않는다.** `python-telegram-bot`이
  제공하는 최상위 모듈이 정확히 `telegram`이라, import root에 같은 이름을 두면
  19개 파일의 `from telegram import Update`가 전부 이 폴더를 집는다.
- **쇼츠 테스트는 루트 `pytest`에 잡히지 않는다.** `shorts/`는 자기
  `pyproject.toml`과 venv를 쓰는 별개 패키지라 `pytest shorts/tests`로 따로
  돌려야 한다. 쇼츠를 고쳤으면 이 줄을 함께 실행한다 — 빼먹으면 조용히 썩는다
  (실제로 `scenario.py`가 제목에 `SIGNAL n · `를 붙인 변경이 테스트를 두 개
  깨뜨린 채 방치돼 있었다).
- 테스트는 외부 API를 mock하며 Cloudflare smoke test는 기본 제외다.
- 파일 수정 뒤 관련 테스트와 전체 pytest를 실행한다.

## 구조

최상위는 **도메인 넷과 인프라 하나**다. **도메인끼리는 아무것도 공유하지 않는다** —
`telegram_bot`·`web`은 서로를 import하지 않고, 둘 사이에 놓인 공용 패키지도 없다.
각자 자기 `core/`(설정·시각·저장)와 `llm/`(Cloudflare 백엔드·분석기)을 갖는다.
유일한 예외가 `web/export.py`로, 봇이 산출물을 굽는 쪽이라 봇에서 지연 import한다 —
방향은 봇 → 웹 한쪽뿐이고 웹은 봇을 부르지 않는다. `shorts`는 파이썬 import 자체를
하지 않고 공개 웹의 API만 HTTP로 읽는다.

**도메인은 자기 코드·테스트·계획서를 자기 폴더 안에 둔다. 인프라 코드는 전부
`infra/`에 있다.** 도메인 안에 `deploy/`를 두지 않는다 — systemd 유닛·Caddy·
terraform·호스트 스크립트는 넷이 한 인스턴스에 얹히는 문제라 한곳에서 봐야 순서와
의존을 읽을 수 있다. 최상위 `tests/`에는 **어느 도메인의 것도 아닌 저장소 자체의
검사**만 둔다(현재는 에이전트 포인터 동기화 하나뿐이다). 도메인 코드를 검사하는
테스트를 여기 두지 않는다.

```text
tests/                 저장소 자체의 검사. 도메인 테스트는 여기 두지 않는다

telegram_bot/          텔레그램 봇 프로세스
  main.py              조립, Telegram 앱, 스케줄러 — `python -m telegram_bot.main`
  core/                이 프로세스의 설정·시각·원자적 저장·워커
  llm/                 이 프로세스의 Cloudflare 백엔드와 분석기
  prompts/             이 프로세스가 읽는 모델 프롬프트
  features/            기능 등록과 명령 핸들러
  handlers/            콜백 라우팅, 메뉴 구성, 인라인 네비게이션
  news/                소스, 매시간 수집, 3시간 시장상황 보고서, 감성
  research/            뉴스 수집, 후보 발굴, 리서치 실행
  briefing/            브리핑 생성과 A주 거래일 캘린더
  stocks/              종목 DB와 시세
  state/               발송·뉴스·시장 감성 상태
  watchlist/           관심종목 상태
  webadmin/            관리 웹 대시보드(터널 전용, 8787) — 봇과 같은 프로세스다
  docs/  tests/

web/                   읽기 전용 공개 웹(별도 프로세스, 8788)
  server.py            FastAPI 라우트, `GET`만 — `python -m web.server`
  core/                이 프로세스의 설정·시각·원자적 저장. 봇 것과 별개다
  llm/                 줄글 브리프용 Cloudflare 백엔드와 분석기
  prompts/             줄글 브리프 프롬프트
  pages.py             화면(정적 HTML·CSS, 기동 시 1회 조립)
  export.py            봇이 부르는 산출물 굽기 — 유일한 봇 → 웹 방향 의존
  polymarket/          폴리마켓 화면을 먹이는 파이프라인. 봇과 무관한 one-shot이라
                       여기 둔다 — 존재 이유가 이 웹 화면 하나다
    dashboard/         Gamma 순회·정규화·generation 저장
    refresh.py         3시간마다 도는 순회 — `python -m web.polymarket.refresh`
    sector_brief.py    순회 성공 뒤 도는 줄글 — `python -m web.polymarket.sector_brief`
    repository.py      server.py가 현재 generation을 읽는 자리
  docs/  tests/

shorts/                쇼츠 영상 자동 생성. 자기 pyproject·venv를 가진 별개 패키지이고
                       이 저장소 코드를 import하지 않는다 — 공개 웹의
                       `/api/polymarket/*`를 HTTP로만 읽는다
  src/  docs/  tests/

infra/                 인프라 코드 전부. 네 도메인이 한 인스턴스에 얹힌다
  terraform/           Lightsail 생성·초기 전환·삭제
  systemd/             유닛·타이머·cron 전부(봇·웹·폴리마켓·쇼츠·백업)
  scripts/             호스트 런타임 설치, 작업 트리 정리
  Caddyfile.example    TLS·Basic 인증 프록시 견본
  server-ops.md        떠 있는 서버를 상대로 반복하는 절차서

data/                  실행 중 생성되는 상태·캐시. `data/<feature>/`. Git 제외
```

기능 카탈로그 순서는 의존 순서다. `FeatureSpec`을 추가할 때 명령, 메뉴,
callback, persistent label을 한 곳에서 등록하고 `FEATURES_ENABLED` 기본값과
`.env.example`을 함께 갱신한다.

## 현재 동작 가정

- 뉴스 수집은 `NEWS_COLLECTION_INTERVAL_MINUTES=60` 간격으로 모든 활성 소스를
  읽고 원문 제목만 `news_report_queue.json`에 저장한다(`collect_report_articles`).
  예약 실행 경로에서는 기사별 번역을 호출하지 않는다.
- **시장상황 보고서는 UTC +9 기준 00·03·06·09·12·15·18·21시에 전송한다.**
  `send_news_report`가 지난 구간 기사를 시장별로 묶어 시장당 한 번 LLM을 호출한다.
  프롬프트는 기사 번역·나열이 아니라 지배적 국면, 업종·종목 영향, 상충 근거,
  달라진 점, 다음 3시간 관찰 포인트를 추론하도록 요구한다.
- 큐에 담긴 기사는 `SentNewsTracker.reserve` 상태다. 보고서 전송이 전부 실패하면
  큐와 예약을 유지해 다음 실행에서 재시도하고, 전송이 성립한 뒤에만 확정하고
  큐를 비운다. 주요 근거는 `NewsLog`와 `PredictionLog`에 함께 기록한다.
- **기사별 번역 파이프라인은 삭제했다.** `news/{pipeline,preparation,delivery,
  selection}.py`는 `fetch_all`을 부르는 호출자가 없는 닫힌 섬이었다. 남겨 두면
  `send_global_digest`·`prepare_global_source` 같은 이름이 살아 있는 전송처럼
  보여, "뉴스 전송을 고쳐라"는 지시가 죽은 코드로 간다. 되살릴 일이 생기면
  git에서 꺼내는 별도 변경이다.
  예약 뉴스 비용은 기사 수가 아니라 3시간 보고서의 시장 수에 비례한다.
- **읽는 폭은 `NEWS_SOURCE_ARTICLE_LIMIT`가 정한다.** 소스를 이 깊이까지만 읽으므로
  여기서 잘린 기사는 다음 주기에도 보이지 않는다. 주기가 3배로 길어져 한 주기가
  덮어야 할 시간도 3배다 — 이 값을 내리면 그만큼 사각지대가 생긴다.
  `gnews`는 이 값을 시장 수로, `gnews_us`·`gnews_kr`은 질의 수로 다시 나눠 쓴다.
- **같은 사건을 두 번 담지 않는다.** 사전선별이 순위를 매기기 전에 세 가지를
  후보에서 뺀다: 최근 `NEWS_PREFILTER_TRANSLATED_EVENT_COOLDOWN_HOURS` 안에 이미
  집은 사건, 이번 주기에 다른 소스가 이미 집은 사건, 같은 소스 안의 같은 사건이다.
  소스 여섯 곳이 같은 발표를 옮겨 적는 것이 한 주기의 가장 흔한 중복이다.
  이것은 **순서를 바꾸는 일이 아니라 빼는 일이라** shadow/active 어느 쪽에서도
  똑같이 돈다 — 두 정책이 걸러진 뒤의 같은 풀에서 고르므로 섀도 비교의 baseline은
  그대로다. 무엇이 왜 빠졌는지는 주기별 `cycle` 관측 줄의 `gated_*`가 센다.
- **`news_prefilter`는 넓게 읽는 비용을 CPU로 내고 Neurons는 그대로 둔다.**
  원문 후보를 사건 단위로 묶고 점수를 매겨, 큐에 담는 대상을 "최신순 상위 N건"
  에서 "점수 상위 N건"으로 바꾼다. 담는 건수는
  `NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT` 그대로라 **추가 Neurons는 0이다** —
  그래서 `NEWS_SOURCE_ARTICLE_LIMIT`을 250까지 올릴 수 있다. LLM을 부르지 않고
  Aho-Corasick 종목 매칭·simhash 사건 군집·로컬 로지스틱 보정기만 쓴다.
- **사전선별의 거취는 2026-10-15까지 정한다. 기한까지 기준 미달이면 지운다.**
  `shadow`는 관찰만 하므로 **켜져 있어도 이득이 0이다.** 코드 1,625줄(테스트
  963줄)을 짊어지고 아무것도 바꾸지 않는 상태가 가장 나쁘다. 실제로 반년 가까이
  그 상태였고, 라벨이 끊긴 것조차 몰랐다. **기본값은 삭제다** — "좀 더 보자"로
  미루지 않으려고 기한과 기본값을 먼저 적는다.

  판단은 `/system prefilter`가 그대로 보여 주는 값으로만 한다.

  | 값 | 뜻 |
  |---|---|
  | `labeled` | 채점된 기사 수(관측 보존 7일 창 안) |
  | `latest_only`·`prefilter_only` | 두 정책이 서로 다르게 고른 건수 |
  | `auc` | 점수의 판별력. 0.5 = 무작위 |
  | `model_validation_ap` / `model_prevalence` | 검증 정밀도 / 기저 비율 |

  **판단 시점.** `labeled >= 500` **그리고** 관측이 7일(보존 기간) 찬 뒤. 그전
  숫자는 표본이 얇아 읽지 않는다. 하루 100~250건이 붙으므로 대략 일주일이다.

  **지운다** — 둘 중 하나라도 해당하면.
  - 불일치율 `(latest_only + prefilter_only) / (agree + latest_only + prefilter_only) < 0.10`
    — 두 정책이 같은 기사를 고른다. 바꿔도 나가는 뉴스가 같으니 1,625줄이 하는 일이 없다.
  - `auc < 0.55` — 점수가 무작위와 구분되지 않는다.

  **올린다(`active`)** — 셋을 **모두** 만족할 때.
  - 불일치율 `>= 0.20` — 바꾸면 실제로 다른 기사가 나간다
  - `auc >= 0.65`
  - `model_validation_ap >= model_prevalence * 1.5` — 기저보다 뚜렷이 낫다

  **그 사이(어느 쪽도 아님)이면 한 번만 2주 연장한다.** 두 번째 연장은 없다 —
  그때도 결론이 안 나면 이 기능은 이 데이터 규모로는 판단 불가라는 뜻이므로 지운다.

  **`auc`를 액면가로 읽지 않는다.** 라벨은 3시간 보고서가 근거로 고른 기사에만
  붙고(시장당 3~8건), 그 기사들은 최신순이 이미 큐에 넣은 것이다. 그래서
  `shadow`의 `auc`는 "최신순이 고른 것들 안에서의 순위"이지 "더 나은 기사를
  찾아내는 능력"이 아니다. 후자는 `active`의 탐색 슬롯으로만 측정된다 —
  **승격은 그 측정의 시작이지 결론이 아니다.**

- **사전선별은 `shadow`로 시작하고 `/system prefilter`가 승격 근거를 그린다.**
  shadow는 점수와 관측만 쌓고 큐에 담는 순서는 최신순 그대로 둔다. **shadow가
  답하지 못하는 것이 있다**: 라벨(impact)은 최신순이 이미 고른 기사에만 붙어,
  사전선별이 새로 끌어올렸을 기사의 impact는 끝내 관측되지 않는다. 따라서 shadow의 AUC는 "최신순이 이미 고른 기사들
  안에서의 순위"이지 "더 나은 기사를 찾아내는 능력"이 아니다. 후자는 `active`의
  탐색 슬롯(`NEWS_PREFILTER_EXPLORATION_SLOTS`)으로만 측정된다. 이 한계는
  `telegram_bot/features/news_prefilter/service.py`의 `SHADOW_CAVEATS`에 적어
  두었고 지운 채로 승격하지 않는다.
- **사전선별의 라벨 공급원은 3시간 보고서 하나뿐이다.** 보고서 입력에서 시장별
  최대 10건을 무작위 표본으로 지정한다. 주요 기사와 겹치지 않는 표본은 같은
  호출의 `evaluations`에서 중요도만 평가해 학습에 쓴다. 미평가는 음성이 아니다.
  표본은 사용자 표시·NewsLog·PredictionLog·사건 재탕 차단에 넣지 않는다.
  추가 요청은 없지만 출력 토큰 비용은 늘 수 있으며 기존 출력 상한은 유지한다.
  표본도 최신순 보고서 후보 안에 있으므로 shadow의 선택 편향을 해소하지는 않는다.
  보고서가 고른
  `highlights`의 `impact`를 `news/report.py`의 `_log_highlights`가
  `record_outcome`으로 되먹인다. 보고서가 이미 만든 값이라 **추가 LLM 호출이
  없다.** 사전선별은 제목만 보고 추측하므로 기사를 실제로 읽고 판정하는
  누군가가 없으면 자기가 맞았는지 영원히 모른다.
  **이 선은 한 번 끊긴 적이 있다.** 예전 공급원이 기사별 번역 경로였는데
  3시간 보고서로 바꾸면서 그 경로가 죽었고, 오류가 나지 않아 13일 동안
  라벨 0건으로 돌았다(2026-08-30 ~ 09-12). 그래서 둘을 두었다 —
  `test_news_report.py`가 이 호출을 고정하고, `/system prefilter`는 라벨 0건을
  "아직 덜 모임"과 구분해 원인을 짚어 준다.
  **보고서 근거와 입력 안의 평가 표본에만 라벨이 붙는다.** 사전선별이 새로 끌어올렸을 기사의
  impact는 여전히 관측되지 않으므로, 그 능력은 `active`의 탐색 슬롯으로만
  측정된다. 승격 판단에서 이 한계를 빼놓지 않는다.
- **관측 파일은 두 정책이 고르는 기사와 탐색분만 남긴다.** 후보 250건을 전부
  적으면 하루 수만 줄이 쌓이는데 라벨이 붙는 것은 극히 일부라 나머지는 학습에
  쓸 수 없다. 남기지 않는 후보는 주기별 `cycle` 집계 줄로 센다.
  적재는 **offset 뒤만 이어 읽는다** — 파일 전체를 dict에 담으면 보존 기간이
  찬 시점에 1GB 인스턴스가 감당하지 못한다(실측: 2일치 221k줄에서 602MB).
- **예약 뉴스의 Neurons 비용은 3시간 보고서에 등장한 시장 수에 비례한다.**
  기사를 추가로 수집해도 기사별 LLM 호출은 늘지 않지만 입력 토큰은 늘어난다.
  `NEWS_REPORT_MAX_HEADLINES`를 올릴 때 무료 한도(하루 10,000)와 컨텍스트 상한을
  함께 계산한다.
- **종목·시장 감성을 읽는 경로는 네 갈래이고, 서로 캐시를 공유하지 않는다.**
  같은 "감성"이라는 말이 네 군데서 각자 다른 저장소·집계·보존정책으로
  쓰이므로, 새로 만지기 전에 어느 갈래인지부터 정한다.
  | 소비자 | 저장소 | 단위·granularity | 보존 |
  |---|---|---|---|
  | `/view`(signal_scoring) | `PredictionLog`(JSONL append-only) | 종목별, up/down/neutral verdict 포함 | 무기한(읽을 때만 `VIEW_LOOKBACK_DAYS=3`로 필터) |
  | 브리핑(briefing) | `NewsLog` | 종목별, count·평균만(verdict 없음) | `NEWS_LOG_RETENTION_DAYS=30`로 매 append마다 정리 |
  | `/market`(market_sentiment) | `MarketDigestStore` | 시장(국가) 단위, 그날 헤드라인 배치 재요약 | `MARKET_DIGEST_RETENTION_DAYS=30` |
  | `/research`(research) | 없음 — 캐시를 안 쓴다 | 실행마다 원문을 새로 수집해 LLM에 직접 투입 | 해당 없음 |
  `PredictionLog`·`NewsLog`는 보고서 근거 기사를 `telegram_bot/news/report.py`에서 나란히
  기록한다(중복이 아니라 소비자가 달라서다 — 하나를 지우면 다른 소비자가 못 읽는다).
- 예약 뉴스 보고서와 리서치 입력은 모두 원문을 사용한다.
- 리서치 후보는 관심종목, 원문 종목명 매칭, 중화권 섹터, 미국 스크리너,
  한국 등락률에서 만든다. 분석 action은 `add`, `remove`, `watch`만 허용한다.
- 리서치 상태는 `history`, `sight`, `updated_at`, `last_result` 형식만 읽고 쓴다.
  `history`는 다음 분석 프롬프트에 들어가는 **압축본**이고, `last_result`는
  `/research show`가 실행 직후와 같은 화면을 다시 그리기 위한 **마지막 전체
  결과**다. 쓰임이 달라 합치지 않는다 — 합치면 프롬프트가 비대해지거나 show가
  얇아진다. 주제를 바꾸거나 지우면 둘 다 비운다.
- 관심종목과 발송 이력도 현재 JSON 형식만 지원한다.
- `/market`은 기사별 번역 대신 시장·일자별 헤드라인 다이제스트를 분석한다.
  완료된 과거 일자는 저장 결과를 재사용하고 오늘만 다시 계산한다.
- **Polymarket은 텔레그램에서 철수했고 공개 웹의 독립 화면이다.** `/polymarket`
  명령·메뉴, 08:35 스냅숏 job, 거시 위험선호 컨센서스, 90일 이력, 승격 게이트,
  CLOB 백필은 전부 제거했다(2026-09-01). `market_sentiment`는 이제 감성과
  이상 두 화면만 가진다. 되살리려면 git에서 꺼내는 별도 변경이며, 그것은
  "지금 열린 것만 본다"는 제품 결정을 되돌리는 일이다.
- **현재 대시보드는 봇과 완전히 분리된 systemd one-shot이 굽는다.**
  `web/polymarket/refresh.py`가 3시간마다 Gamma `/events/keyset`을
  전수 순회해 `data/webpub/polymarket/`에 generation을 쓰고, `web/server.py`가
  `/polymarket`과 `/api/polymarket/*`로 그 파일만 내보낸다. 봇 프로세스도
  스케줄러도 이 경로를 모른다 — 봇이 죽어도 화면은 마지막 generation을 계속
  보여 준다. 절차는 `infra/server-ops.md` 8절.
- **`current.json`은 열린 event 전부를 한 파일에 담고 상한이 16 MiB다.**
  이것은 임의의 숫자가 아니라 설계 판정 기준이다(`web/docs/polymarket-dashboard.md`
  7-4). 넘으면 `write_generation`이 멈추고 current를 교체하지 않는다 —
  상한을 올려 넘기지 않는다. 공개 웹이 이 파일을 통째로 파이썬 객체로 올리고
  `MemoryMax=192M`이 걸려 있어, 올리면 화면이 비는 대신 웹 프로세스가 OOM으로
  죽는다. **compact에 필드를 추가하면 그 크기에 event 수(실측 21,872)가
  곱해진다** — 20 B짜리 필드 하나가 0.42 MiB다. 목록·순위·필터·정렬이 읽지
  않는 값은 detail로 내린다(detail은 byte-addressed라 한 행만 seek한다).
  추가 전에 `web/tests/polymarket_manifest_size_probe.py`로 여유를 먼저 잰다.
- **두 one-shot은 봇의 9% CPU 회계 밖이다. 대신 자기 예산을 스스로 지킨다.**
  별도 프로세스라 `burst_phase`·`is_burst_active`가 닿지 않는다. 유닛의
  `Nice=10`·`CPUWeight=20`은 **경쟁이 있을 때만** 양보시켜서, 새벽에 봇이
  한가하면 CPU를 100% 쓰고 버스트 크레딧이 탄다. 그래서 refresh가 순회를
  시작하기 전에 `status.json`의 최근 24시간 표본을 더해
  `POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS`(900)·`POLYMARKET_WEB_MAX_DAILY_REQUESTS`
  (3,000)와 비교하고, 넘었으면 그 주기를 건너뛴다(`last_result:
  "skipped_budget"`, 종료 코드 0). **실패한 실행도 센다** — manifest 상한
  초과처럼 219 page를 전부 돌고 죽는 실패가 있어, 세지 않으면 반복 실패가
  예산을 그대로 통과한다.
- **generation은 두 벌만 남긴다.** detail shard가 generation 하나에 116 MiB라
  쌓이면 디스크가 상한보다 먼저 찬다. 직전 하나를 남기는 것은 이력이 아니라,
  승격 순간에 이미 들어와 있던 요청이 자기가 읽던 shard를 계속 seek할 수 있게
  하기 위한 것이다. 과거 조회는 만들지 않는다 — 화면은 "지금"만 본다.
- **공개 웹은 봇과 다른 프로세스이고 `GET`만 가진다.** 봇이 산출물을 갱신할 때
  `web/export.py`가 `data/webpub/`에 구워 두고(`market.json`·`market_chart.png`·
  `research.json`·`meta.json`), `web/server.py`는 그 파일을 그대로 내보낸다. 요청 때
  렌더하지 않는다 — `render_market_chart`는 dpi 160짜리 12×7.5인치 figure라 지인
  몇 명의 새로고침만으로 사전선별 보정이 밀린다. **실행 트리거는 웹에 열지 않는다**:
  `/research run`·`/market` 재계산은 텔레그램에만 둔다. Neurons가 링크를 받은 사람
  수만큼 나가고, 리서치 상태(`sight`·`history`)가 단일 사용자 형식이라 동시 실행이
  서로의 맥락을 덮기 때문이다. 쓰기 API가 있는 관리 웹(8787)은 계속 터널 전용이고,
  8788도 방화벽에 열지 않는다 — TLS와 Basic 인증은 앞단 Caddy가 맡는다
  (`https://nunchi.live`. 절차는 `infra/server-ops.md` 11절). **인증은 면을 나눈다** —
  국가별 감성 집계는 열고, 종목명·`add`/`watch`·confidence가 담기는 `/research`만
  잠근다. 잠금이 지키는 것은 시스템이 아니라 내용이다.
  **회원가입·계정별 상태, DB, SPA 빌드 파이프라인, 실시간 갱신은 만들지 않는다** —
  상태 파일이 단일 사용자 형식이고, 조회가 전부 "마지막 것 한 개"라 인덱스가 필요한
  질의가 없으며, 데이터가 분 단위로 바뀌지 않아 기준 시각을 적는 것으로 충분하다.
  **화면(`web/pages.py`)은 정적 문자열이고 외부 폰트·CDN·프레임워크를 부르지
  않는다.** 페이지는 기동 시 한 번 조립되고 값은 브라우저가 `/api/*`에서 채운다 —
  이 프로세스가 요청을 받아 밖으로 나가는 경로를 만들지 않으려는 것이고, 빌드
  산출물이 없어야 배포가 파일 복사로 끝나기 때문이다. 값을 넣을 때는 `esc()`를
  거친다: 산출물에는 리서치 `reason`처럼 모델이 쓴 문자열이 그대로 들어 있다.
  화면은 **라이트 전용**이고(`color-scheme:light`) 감성의 부호는 **빨강이 긍정,
  파랑이 부정**이다 — 한국 시장 화면의 관례라 읽는 사람이 다른 화면에서 종일
  보는 방향과 맞춘다. 서양식 녹/적으로 되돌리지 않는다.
- 종목 canonical code는 시장마다 형식이 다르다. CN·HK는 **접두사 없는 숫자 코드**
  (`600519`, `00700`)이고, US·KR만 `US:NASDAQ:AAPL`·`KR:KOSPI:005930` 형식이다
  (`telegram_bot/stocks/universe.py`의 `stock_key`). KR 6자리는 A주 코드와 겹치므로 US·KR에만
  거래소를 붙인다 — 코드 문자열만으로 시장을 단정하지 않는다.

## 기능 경계 — 가장 중요한 규칙

**기능은 다른 기능을 import하지 않는다.** `telegram_bot/tests/test_feature_isolation.py`
가 AST로 검사해 위반하면 실패한다. 프레임워크(`features/base.py`·`registry.py`)와
카탈로그 조립 지점(`features/__init__.py`)만 예외다.

두 번 당하고 세운 규칙이다.

- `news_prefilter`가 `research`의 **비공개** 함수를 직접 불렀다. 리서치의 종목명
  처리를 건드리면 사전선별 매칭이 함께 흔들렸다.
- `system_admin`이 `news_prefilter`의 `report()` dict 모양을 알고 화면을 그렸다.
  관측 항목 하나를 추가하려면 남의 기능 파일을 함께 고쳐야 했다.

### 공용 계층은 모듈 안에서만 만든다

**모듈 경계를 넘는 공용 계층을 두지 않는다.** 최상위 모듈(`telegram_bot`, `web`,
`shorts`)은 필요한 것을 **각자 자기 안에 구현한다.** 공유는 그 모듈 안에서만 한다
— `telegram_bot/core`는 봇의 것이고 `web/core`는 웹의 것이며, 이름이 같아도 서로
다른 파일이다.

이유는 장애 전파다. **한 모듈의 사정으로 고친 파일이 다른 모듈을 멈추면 안 된다.**
예전 `shared/core/config.py`가 정확히 그랬다. 최상단에서
`os.environ["TELEGRAM_BOT_TOKEN"]`을 읽었기 때문에, **텔레그램 토큰이 비면 봇과
아무 상관 없는 공개 웹(8788)도 기동하지 못했다.** 같은 파일이 봇의 기능 플래그로
Cloudflare 자격증명을 강제해, 줄글 브리프를 쓰지도 않는 순회 one-shot까지
끌어내렸다. 공용 계층 하나가 프로세스 둘의 기동 조건을 묶어 버린 것이다.

**그래서 중복을 감수한다.** `telegram_bot/llm/backends.py`와 `web/llm/backends.py`는
지금 내용이 같다. 한쪽 버그를 고치면 다른 쪽에 따로 옮겨야 하고 **그 비용은
의도한 것이다** — 옮길지 말지를 그때 판단할 수 있다는 뜻이고, 반대쪽은 판단 없이
끌려간다. 옮길 때는 두 벌 다 고치고 양쪽 테스트를 돌린다.

기존 규칙과 부딪히는 지점을 분명히 해 둔다. **모듈 경계에서는 중복 회피가 아니라
격리가 이긴다.** 아래 기능 간 규칙은 그대로다 — 한 모듈 **안에서는** 복제하지
말고 소유자를 옮긴다.

### 기능 간(한 모듈 안)에는 여전히 소유자를 옮긴다

**기능이 무언가를 공유해야 하면 복제하지 말고 둘 중 하나를 쓴다.**

| 방법 | 쓸 때 | 실제 예 |
|---|---|---|
| 소유자를 **같은 모듈의 공용 계층**으로 옮긴다 | 그 지식이 특정 기능의 것이 아닐 때 | 종목명 토큰화 → `telegram_bot/stocks/naming.py` |
| **`FeatureSpec` 선언**으로 레지스트리가 중개한다 | 기능이 앱에 무언가를 기여할 때 | `/system` 화면 → `status_reports` |

**한 모듈 안에서 결합 제거와 중복 회피가 부딪히면 결합 제거가 우선이다.** 단
복제로 풀지 않고 소유자 이전으로 푼다. 같은 모듈 안에 같은 코드를 두 벌 두면
한쪽 버그 수정이 다른 쪽에 가지 않아, 막으려던 문제가 형태만 바꿔 돌아온다.
모듈 **밖으로** 옮기는 선택지는 없다 — 그게 위에서 지운 공용 계층이다.

**공용 계층은 자기 사정으로만 바뀌어야 한다.** 어떤 기능 때문에 공용 파일을
고치고 있다면 소유자가 잘못된 것이다. 커밋 이력으로 잴 수 있다 — 그 파일이
기능 파일과 같은 커밋에 얼마나 자주 들어갔는지 센다(대량 이동 커밋은 뺀다).

## 그 밖의 확정 규칙

- 환경 변수는 각 모듈의 `core/config.py`에서만 읽고 `.env.example`에 현재 키를 기록한다.
  (`telegram_bot/core/config.py`, `web/core/config.py`. `shorts`는 자기 `config.py`.)
  운영자가 조정하지 않는 설정은 같은 모듈의 리터럴 상수로 둔다.
- **배경 CPU 작업(보정)은 예산 안에서만 돈다. 매 주기 필수인 foreground
  작업은 고정 슬라이스로 재지 않는다.** `run_prefilter_maintenance`는 직전
  1분의 실제 foreground CPU를 측정하고, 2 vCPU 전체 용량의 9%에서 그 값을
  뺀 잔여분만 보정(calibration)에 배정한다. 유휴 주기의 최대치는 10.8
  CPU-second(60초 × 2 × 9%)이고, 하루 보정 상한은 4.32 CPU-hour다. 각 2초
  조각마다 `wait_for_urgent_idle`·load average·남은 예산을 다시 확인한다.
- **버스트 크레딧은 고가치 작업에 우선 배정한다.** 리서치, 3시간 시장상황
  보고서, 시장 컨센서스 분석·수집은 `burst_phase`로 표시한다. 이 구간에는
  프리필터 보정이 시작되지 않으며, 고가치 작업 자체는 평시 9% 제한을 받지
  않는다. Lightsail 크레딧 잔량을 API로 읽지 못해도 평시를 baseline 아래로
  유지해 충전하고 요청 시에는 저장된 크레딧을 쓸 수 있는 구조다.
- 상태 파일은 `data/<feature>/`에 둔다. 설정은 상태 파일에 저장하지 않는다.
- **상태 파일은 그 모듈의 `core/storage.py`가 주는 원자적 쓰기로만 저장한다.** 대상 파일을 직접
  열어 쓰면 그 순간 내용이 비고, 실패하면 잘린 JSON이 남아 다음 기동이 상태를
  통째로 잃는다. 저장 실패를 로그로 삼키지 않는다 — 호출자가 반환값으로 판단하는
  것(스냅숏을 남겼는가, 관심종목이 저장됐는가)이 있어서, 실패를 성공으로 보고하면
  사라진 줄 모르는 상태가 된다. 메모리는 저장에 성공한 뒤에 바꾸거나 되돌린다.
- **`ALLOWED_CHAT_IDS`가 비면 기동하지 않는다.** 빈 목록을 전체 허용으로 읽는
  분기를 되살리지 않는다 — 상태가 채팅별로 나뉘어 있지 않아 설정 누락이 곧바로
  공개 봇이 된다. 오타로 유효한 ID가 남지 않은 경우도 같게 막는다.
- 번역·분석·브리핑은 Cloudflare Workers AI만 사용한다. 비밀값은 `.env`에만 두고
  로그나 예외에 포함하지 않는다.
- LLM JSON은 필수 필드를 엄격히 검사하고 현재 응답 envelope만 처리한다.
- **`finish_reason=length`는 성공이 아니다.** 상한에 걸려 끊긴 응답을 그대로
  돌려주면 호출자는 파싱 오류(`Unterminated string`)만 보고 원인을 못 찾는다.
  `backends.py`가 `truncated`로 실패시키고 `max_tokens`와 실제 output_tokens를
  로그에 남긴다. **재시도하지 않는다** — 같은 입력은 같은 길이에서 다시 끊기고
  Neurons만 두 배로 태운다. 회로 차단의 연속 실패로도 세지 않는다
  (`caller_fault`) — 한 호출자의 출력 예약 문제로 다른 LLM 작업까지 멈추면 안 된다.
- **LLM에게 원문 URL을 받아 적게 하지 않는다.** URL은 파이썬 쪽에서 따라가다
  표시 직전에 붙인다(`telegram_bot/news/utils.py`의 `<a href>`). 리서치 evidence도 모델은
  `news_items`의 id만 가리키고 서버가 되찾는다(`_news_payload`). Google News
  링크는 중앙값 286자의 base64라, 모델이 생성하게 두면 출력 예산의 3분의 1을
  먹고 정작 분석이 끝을 맺지 못한다.
- 외부 소스 하나의 실패가 전체 뉴스 주기를 중단시키지 않도록 소스 단위로 격리한다.
- 새 호환 분기, 사용하지 않는 설정 플래그, 중복 helper를 만들지 않는다.
  기능끼리 같은 코드가 필요하면 복제하지 말고 위 "기능 경계"의 두 방법을 쓴다.
- **문서는 이 기준 문서, 에이전트 포인터, 절차서 하나, 배포서 하나, 그리고
  계획서뿐이다. 그 외 새 목록 파일을 만들지 않는다.** `infra/server-ops.md`는 이미 떠 있는 서버를 상대로 반복하는
  **절차서**다(접속·배포·설정·실측·판정·백업·장애). 절차이므로 항목이 끝나도
  지우지 않는다. 인스턴스 생성·최초 전환·삭제는 `infra/terraform/README.md`에만 있다.
  나머지는 **계획서**이고 항목이 끝나면 지운다 — 완료 기록은 git 이력이 맡는다.
  계획서는 주제를 소유한 도메인의 `docs/` 안에 둔다.
  현재 계획서는 셋이다: `telegram_bot/docs/actor-potus.md`(세력 행동 추정, 미 대통령
  게시물 추적), `web/docs/polymarket-dashboard.md`(폴리마켓 현재 전량을 공개 웹
  대시보드로),
  `web/docs/polymarket-sector-brief.md`(경제·금융·지정학 줄글 컨센서스와 주기 간
  이동 추적). 뒤 둘은 앞이 전제이지만 파일을 나눴다 — 대시보드 계획서가 먼저
  끝나 지워져도 줄글 계획은 남아야 한다.
  **계획서를 새로 파는 것은 주제가 기존 계획서와 독립일 때뿐이고, 다 끝나면 파일째
  지운다.** 종류를 섞지 않는다: 절차서에 할 일을
  적으면 끝난 일이 남고, 계획서에 절차를 적으면 계획을 지울 때 절차까지 사라진다.
- 모듈은 한 책임을 유지하되 한두 함수만 담는 무의미한 파일 분할은 피한다.
- **시각은 그 모듈의 `core/clock.py`가 주는 `now()`·`today()`만 쓴다.** `datetime.now()`·`date.today()`는
  호스트 타임존을 따라가서 서버를 다른 타임존에 올리면 `/market`의 하루 경계와 보존
  기간이 통째로 밀린다. ruff의 `DTZ` 규칙이 이걸 막는다(테스트는 예외).
  저장된 타임스탬프를 `now()`와 비교할 때는 `ensure_jst()`로 감싼다 — aware 전환
  이전에 쓴 `data/` 파일에는 오프셋이 없어 그냥 비교하면 TypeError로 죽는다.
  예외는 셋뿐이다: Cloudflare 할당량 리셋은 UTC 00시 기준이고
  (`telegram_bot/llm/backends.py`·`web/llm/backends.py`), 기사 시각은 소스 타임존을 `telegram_bot/news/utils.py`가 UTC +9로 변환하며,
  사전선별의 하루 CPU 예산도 Neurons와 같은 UTC 00시에 리셋한다
  (`telegram_bot/features/news_prefilter/service.py`) — 두 예산의 경계를 맞춰 두면 한쪽이
  소진된 날을 다른 쪽 로그와 같은 일자로 읽을 수 있다.

## 리팩터링 방법

### 판정 기준

코드 길이를 줄이는 것이 목표가 아니다. 판단 기준은 하나다.

> **이 기능을 고치려고 몇 개 파일을 열어야 하는가.** 대부분의 일반적인 변경이
> 1~3개 파일 안에서 끝나야 한다.

우선순위는 이 순서다.

1. 한 기능을 이해·수정할 때 읽어야 할 범위
2. 코드 이해 가능성
3. 변경 범위의 국소화
4. 모듈 간 결합도
5. 테스트 가능성 · 회귀 위험
6. 성능
7. 코드 축약

### 착수 전 — 후보를 표로 평가한다

한 번에 전부 고치지 않는다. 후보를 나열하고 **High priority + Low/Medium risk**
부터 한다.

| 항목 | 내용 |
|---|---|
| Problem | 현재 구조의 문제 |
| Cost | 이 기능을 고칠 때 드는 읽기·추론 비용 |
| Refactor | 제안하는 변경 |
| Benefit | 기대 효과 |
| Risk | 회귀 위험 |
| Priority | High / Medium / Low |

### 죽은 코드는 추측하지 말고 측정한다

네 진입점(`telegram_bot.main`, `web.server`, `web.polymarket.refresh`,
`web.polymarket.sector_brief`)에서 import 그래프를 따라가 도달하지 못하는 모듈을
찾는다. 함수 안 지연 import는 그래프에 안 잡히므로 따로 확인한다.

`news/{pipeline,preparation,delivery,selection}.py` 523줄이 이 방법으로 나왔다.
`send_global_digest` 같은 이름이 살아 있는 전송처럼 보여, "뉴스 전송을 고쳐라"는
지시가 죽은 코드로 가고 있었다.

### 동작 보존은 증명한다

테스트 통과만으로 갈음하지 않는다. 테스트가 덮지 않는 부분이 있다.

`web/pages.py` 863줄을 여섯 파일로 나눌 때, 분리 전 모듈을 따로 import해 네 화면을
렌더하고 새 패키지의 결과와 SHA-256을 비교했다 — 네 화면 모두 바이트 단위로
동일했다. 이런 증명을 붙일 수 있으면 붙인다.

### 하지 않는 것

- **동작을 바꾸지 않는다.** 요청하지 않은 제품 동작 변경은 리팩터링이 아니다.
  기능 추가가 필요하면 그렇다고 말하고 별도 지시를 받는다.
- **전체 rewrite를 하지 않는다.** 작동하는 코드는 점진적으로 고친다.
- **구조를 위한 구조를 도입하지 않는다.** 새 프레임워크, DI 컨테이너, ORM,
  event bus, CQRS, repository pattern은 실제 문제를 풀 때만 쓴다.
- **파일 수만 늘리지 않는다.** 한두 함수짜리 파일로 쪼개면 읽을 파일만 늘어난다.
- **사용 여부가 불확실하면 지우지 않는다.** 먼저 참조를 조사한다.

### 이름

`utils`·`helper`·`common`·`misc`·`manager`·`processor`는 피하고 도메인 단어를 쓴다.
단 이 저장소의 확정 관례는 그대로 둔다 — `handlers.py`는 기능별 명령 구현 자리이고
(6곳), `telegram_bot/news/utils.py`는 뉴스 표시·시각·자르기 계약이다.

같은 개념에 같은 단어를 쓴다. `customer`/`client`/`user`처럼 섞지 않는다.

## 작업 후 자가 점검

- 기존 동작이 바뀌지 않았는가 (증명했는가)
- 기능이 다른 기능을 참조하지 않는가 (`test_feature_isolation.py` 통과)
- 불필요한 추상화가 늘지 않았는가
- 파일 수만 늘어난 것은 아닌가
- 이 기능을 고칠 때 읽어야 할 범위가 실제로 줄었는가
- 이름만 보고 역할을 알 수 있는가
- 테스트가 이 변경을 지키는가
- 지울 수 있는 죽은 코드가 남아 있지 않은가

## 배포

Lightsail 운영 절차는 `infra/terraform/README.md`가 유일한 배포 문서다. 부트스트랩은
봇을 자동 기동하지 않는다. 동일 Telegram 토큰의 중복 polling을 피하도록 로컬을
정지한 뒤 서버를 기동한다.
