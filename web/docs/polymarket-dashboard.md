# 계획: 현재 Polymarket 컨센서스 웹 대시보드

> 이 문서는 기존 `/polymarket` 텔레그램 기능을 확장하는 계획이 아니다.
> **텔레그램에서 Polymarket을 완전히 분리하고, 현재 열린 Polymarket 전체를
> 카테고리별로 탐색하는 읽기 전용 웹 대시보드로 교체하는 계획**이다.

한 줄로: **사용자가 웹을 열면 지금 Polymarket에서 어떤 분야와 이벤트에 돈과
관심이 모였고, 각 이벤트에서 어느 결과에 몇 %가 걸려 있는지를 과거 차트 없이
한눈에 본다.**

핵심 규약은 네 가지다.

1. **현재만 본다.** 가격 이력, 일별 스냅숏 열, 백필, 전일 대비, 추세선이 없다.
2. **전부 포함한다.** 거래량·유동성·만기 거리로 이벤트를 수집·회계·탐색기에서
   버리지 않는다. 다만 요약 순위의 기본 표본은 데이터 품질과 분리해 정한다.
3. **분야별로 찾게 한다.** 모든 이벤트는 정확히 하나의 대표 카테고리에 들어가고,
   원래 태그는 보조 필터로 보존한다.
4. **텔레그램과 생명주기를 분리한다.** 수집·가공·표시·장애가 봇 명령과 스케줄에
   의존하지 않는다. 프로세스가 달라도 같은 인스턴스의 CPU·메모리는 공유하므로
   자원 예산은 함께 검증한다.

---

## 0. 제품 목적과 범위

### 0-1. 사용자가 이 화면에서 답을 얻어야 하는 질문

- 지금 Polymarket에는 몇 개의 열린 이벤트와 마켓이 있는가?
- 정치·지정학·경제·크립토·기술·스포츠·문화·날씨 등 각 분야에 얼마나 많은
  이벤트와 거래가 몰려 있는가?
- 각 이벤트에서 현재 가장 높은 확률을 받는 결과는 무엇인가?
- 결과 하나로 컨센서스가 굳은 이벤트와 팽팽하게 갈린 이벤트는 무엇인가?
- 최근 24시간 거래가 가장 활발한 분야와 이벤트는 무엇인가?
- 유동성이 얕거나 가격을 정상적으로 읽지 못한 항목은 무엇인가?
- 관심 분야만 필터링하고 제목·태그로 원하는 이벤트를 바로 찾을 수 있는가?

### 0-2. 이 대시보드가 하지 않는 것

- 과거 가격 이력 수집 및 저장
- 과거 시점 복원과 백필
- 전일 대비 확률 변화, 모멘텀, 추세, 7·30·90일 비교
- Matplotlib 이미지나 시계열 그래프 생성
- risk-on/risk-off 변환 또는 주식·상품 가격 방향 추론
- 매매 신호, 종목 추천, 예측 정확도 평가
- 사용자의 주문·포지션·지갑 조회 또는 Polymarket 주문 실행
- LLM을 이용한 이벤트 분류나 방향 판정
- 서로 다른 이벤트 유형을 하나의 합성 확신 점수로 줄여 순위를 매기는 일

“컨센서스”는 **현재 표시된 시장가격이 암시하는 확률과 결과 분포**라는 뜻이다.
높은 확률은 그 결과가 참이라는 보증이 아니며, 이 대시보드는 공개 예측시장 참여자의
현재 베팅 상태를 정리하는 관측 화면이다.

---

## 1. 배포 면과 독립성

기존 저장소에는 다음 읽기 전용 공개 웹 경로가 있다.

```text
브라우저 → Caddy(TLS·인증 정책) → 127.0.0.1:8788 → web/server.py
                                                ↓
                                      data/webpub/*
```

Polymarket 대시보드는 이 웹의 `/polymarket` 페이지로 들어간다. 다만 현재
`market.json`과 `research.json`처럼 봇이 산출물을 굽지 않고 독립 timer가 갱신한다.

```text
systemd timer
    ↓ 기본 15분마다 독립 one-shot
polymarket/refresh.py
    ↓ 0단계에서 검증해 선택한 Gamma events 순회 경로
페이지별 정규화·분류·현재 집계
    ↓ 현재 generation 원자적 승격
data/webpub/polymarket/current.json + detail shards
    ↓ 읽기 전용
webpub FastAPI → /polymarket, /api/polymarket/*
```

분리의 의미는 다음과 같다.

- 봇이 꺼져도 웹 수집과 마지막 정상 화면은 유지된다.
- 웹 수집이 실패해도 텔레그램 뉴스·리서치·시장 감성 작업은 기능적으로 영향받지
  않는다.
- 웹 방문이 Gamma 호출을 만들지 않는다. 방문자 수와 외부 API 요청량이 분리된다.
- Polymarket용 Telegram 명령, 메뉴, callback, bot scheduler, `bot_data`가 사라진다.
- 웹과 갱신 작업은 주문·쓰기 API를 갖지 않는다.
- **프로세스 독립은 자원 독립이 아니다.** 별도 one-shot의 CPU와 RSS는 봇의
  `time.process_time()`에 잡히지 않으므로 7절의 별도 계측·상한을 적용한다.
- one-shot은 `Nice=10`, `CPUWeight=20`, `MemoryMax=256M`,
  `TimeoutStartSec=10min`을 두어 봇의 고가치 작업과 경합할 때 먼저 양보하게 한다.

---

## 2. 착수 전 원천 검증과 현재 데이터 범위

### 2-1. 공식 문서는 구현 전제가 아니라 검증할 가설이다

공식 문서는 Gamma `GET /events/keyset`에 `next_cursor`·`after_cursor` 방식과 최대
`limit=500`을 명시한다.

- 공식 문서: <https://docs.polymarket.com/api-reference/events/list-events-keyset-pagination>
- 데이터 모델: <https://docs.polymarket.com/market-data/overview>

그러나 이 저장소의 `telegram_bot/features/market_sentiment/polymarket.py`에는
`/markets/keyset`이 어떤 cursor 이름을 넘겨도 첫 페이지를 반복했고 실제 limit도
100에서 잘렸다는 운영 실측이 있다. 현재 `web/tests/test_polymarket_smoke.py`도 함수명과
달리 `/events/keyset`을 호출하지 않고 `PolymarketClient`의 레거시 `/markets`만
검사한다.

두 endpoint가 다르게 동작할 수는 있지만, 확인 전에는 `/events/keyset` 전수 순회를
신뢰하지 않는다. **아래 0단계가 통과하기 전에는 수집기 구현에 착수하지 않는다.**

### 2-2. 0단계: Lightsail keyset·offset 프로브

`web/tests/test_polymarket_smoke.py`에 opt-in 프로브를 추가하고 실제 운영 Lightsail에서
다음 명령으로 실행한다.

```bash
RUN_POLYMARKET_SMOKE=1 python -m pytest -q -m polymarket_smoke
```

한국 PC의 성공은 운영 서버 출구 IP의 성공을 대신하지 않는다. 직접 호출이 451이면
기존 `POLYMARKET_PROXY_URL` 경로에서도 같은 프로브를 실행한다.

확인하고 출력으로 남길 값:

1. `/events/keyset?closed=false&limit=500` 첫 응답의 실제 건수와 `next_cursor`
2. `after_cursor=<next_cursor>` 두 번째 페이지의 cursor 변화, ID 집합, 중복 수
3. 같은 cursor나 동일 페이지가 반복되는지
4. 마지막 cursor까지 순회한 page 수, 고유 event 수, 중복 수
5. 전체 응답 바이트, wall seconds, process CPU seconds, peak RSS
6. 열린 이벤트에서 관측한 전체 raw tag 빈도와 nested market 수
7. 레거시 `/events?closed=false&limit&offset&order=id&ascending=true`도 같은 방식으로
   순회했을 때 실제 limit, 중복 페이지, 경계 역전 수, 총량과 비용

`limit=500` 요청이 항상 500건이어야 한다고 단정하지 않는다. 다음 cursor가 있는데도
더 작은 고정 크기로 잘리는지, 요청값과 실제 페이지 크기가 무엇인지 기록한다.

프로브 결과에 따른 경로는 미리 고정한다.

| 결과 | 선택 | 조건 |
|---|---|---|
| A | `/events/keyset` | cursor가 전진하고 동일 페이지를 반복하지 않으며 끝까지 순회 가능 |
| B | 레거시 `/events` offset | keyset 실패, offset은 결정론적 ID 정렬로 끝까지 순회되고 경계 이상을 계측 가능 |
| C | 구현 중단 | 두 경로 모두 첫 페이지 반복·강제 절단·순회 불가로 전체 범위를 만들 수 없음 |

경로 B는 살아 있는 목록을 offset으로 읽으므로 단일 시점의 원자적 스냅숏을 보장하지
못한다. 중복을 제거하고 `shifted_page_count`를 공개하되, 0단계에서 허용할 안정성
기준을 실측값과 함께 확정한다. 경로 B의 `coverage_status`는 이상이 관측되지 않아도
원자성을 증명할 수 없으므로 `degraded`가 기본이다. 기준을 넘은 갱신은 last-good을
교체하지 않는다.

경로 C이면 `/markets`를 이벤트로 재조립하거나 일부 카테고리만 보여 주는 식으로
목적을 몰래 축소하지 않는다. 다른 공식 원천이나 mirror를 정한 뒤 이 계획의 원천·
회계·비용 계약부터 다시 쓴다.

### 2-3. 수집 대상

선택된 events endpoint에는 `closed=false`만 넘기고, 응답 후 클라이언트에서
`active is true and closed is false`를 다시 확인한다. 비활성 항목은
`excluded_non_open_count`와 이유를 남기고 화면에서는 제외한다.

다음 값은 **수집 제외 조건이 아니다.**

- 거래량과 유동성
- spread
- 만기까지 남은 기간
- 0 또는 1에 가까운 현재 가격
- theme·tag·region
- `restricted` 여부

이 대시보드는 “신뢰할 만한 몇 개를 뽑는 지표”가 아니라 “한 번의 성공한 순회에서
관측한 현재 열린 전체를 보여 주는 탐색 화면”이다. 얇거나 이상한 이벤트는 상태로
설명하고 버리지 않는다.

### 2-4. 중복과 누락을 숨기지 않는 회계

이벤트 identity는 `id`, 없으면 `slug`, 둘 다 없으면 scan-local key 순서로 만든다.
identity가 없는 레코드는 `missing_identity_count`에 세고 `unavailable`로 남긴다.
안정된 identity가 다시 나오면 첫 레코드만 사용하고 나머지는
`duplicate_event_count`에 센다.

```text
fetched_record_count
  = unique_event_count
  + duplicate_event_count

unique_event_count
  = open_event_count
  + excluded_non_open_count

open_event_count
  = consensus_ready_event_count
  + unavailable_event_count

sum(category.event_count)
  = open_event_count
```

`source_event_count = displayed_event_count`처럼 같은 값을 다른 이름으로 두지 않는다.
화면은 `open_event_count`를 유일한 전체 이벤트 분모로 사용한다.

함께 기록할 순회 품질 값:

```text
pagination_mode, coverage_status
page_count, actual_page_sizes[]
duplicate_event_count, missing_identity_count
shifted_page_count
source_request_count, retry_count
response_bytes, walk_seconds
```

`coverage_status`는 `complete`, `degraded`, `failed`다. 반복 cursor·동일 페이지 무한
반복·프로브에서 정한 안정성 기준 초과는 `failed`이며 현재 파일을 교체하지 않는다.
고립된 중복이나 순회 중 열림·닫힘처럼 회계 가능한 변동은 `degraded`로 표시할 수
있지만 조용히 `complete`로 부르지 않는다.

---

## 3. 이벤트와 현재 컨센서스 모델

Polymarket에서 market은 Yes/No로 거래되는 단위이고 event는 한 개 이상의 market을
묶는 상위 단위다. 화면과 회계의 기본 행은 event다.

### 3-1. 공통 가격 검증

현행 파서의 `_PRICE_SUM_TOLERANCE = 0.05`를 새 파서의 명시적 계약으로 이어 쓴다.

```text
PRICE_SUM_TOLERANCE = 0.05
valid_binary_leg =
  outcomes가 정확히 Yes/No이고
  각 가격이 0..1이며
  abs(sum(outcome_prices) - 1.0) <= 0.05
```

- tolerance 안이면 표시용 확률만 합 1로 정규화하고 `raw_price_sum`을 보존한다.
- tolerance 밖이면 정상 이진 확률로 만들지 않고 `price_sum_invalid` 경고와
  `unavailable` 상태를 준다.
- event 유형은 가격 합으로 추론하지 않는다. market 수와 명시적인 `negRisk`를 먼저
  사용하고 가격 합은 해당 유형의 현재 값이 유효한지 교차 검증한다.
- `outcomes`·`outcomePrices`가 문자열 JSON 또는 배열인 경우를 모두 읽는다.

### 3-2. 단일 binary 이벤트

market이 하나이고 유효한 Yes/No 가격이면 두 결과를 직접 표시한다.

```text
미국이 2026년에 경기침체에 들어갈까?
Yes 34%  ███████░░░░░░░░░░░░
No  66%  █████████████░░░░░░░
```

`leader_probability`는 `max(p_yes, p_no)`이고 `leader`는 그 결과다. 결측·범위 오류·
가격 합 오류가 있으면 추정하지 않는다.

### 3-3. 배타적 다지선다 이벤트

자식 market이 여러 개이고 `negRisk is true`이면 각 자식의 Yes가 서로 배타적인
결과라고 본다.

- 모든 자식 Yes 가격이 유효하고 `abs(sum(yes_prices)-1) <= 0.05`일 때만 표시용
  구성비를 정규화한다.
- `raw_yes_sum`, outcome 수, 결측 수를 보존한다.
- 하나라도 빠지거나 합이 tolerance 밖이면 정상인 100% 구성비를 만들지 않는다.
  읽힌 raw Yes 가격은 상세에 남기되 event leader와 순위 표본에서는 제외한다.
- `leader_probability`는 정규화된 1위 확률, `runner_up_probability`는 2위 확률,
  `leader_margin`은 둘의 차이다.

### 3-4. 서로 독립적인 다중-market 이벤트

자식 market이 여러 개이고 `negRisk is false`이면 서로 동시에 참일 수 있는 독립
질문 묶음으로 처리한다.

- 각 market의 Yes/No를 독립적으로 표시하고 합이 100%인 것처럼 쌓지 않는다.
- event 하나의 “1위 확률”이나 “팽팽함”으로 합성하지 않는다.
- 현재 확신 목록이 필요하면 event가 아니라 자식 binary market을 같은 유형끼리만
  비교하고 부모 event를 함께 표시한다.

자식 market이 여러 개인데 `negRisk`가 없거나 읽히지 않으면 `unknown_multi`다.
독립형처럼 raw 확률을 나열하되 구성비로 정규화하지 않고 데이터 주의 목록에 둔다.

### 3-5. 교차 유형 합성 점수는 만들지 않는다

초기 계획의 entropy 기반 `consensus_strength`와 카테고리 중앙값은 제거한다. 결과
개수와 event 유형이 다른 값을 같은 열에서 비교하면 분야별 event 구성 자체가 점수에
섞이기 때문이다.

“굳음과 팽팽함”은 다음 현재값으로 답한다.

| 유형 | 주 값 | 보조 값 | 비교 범위 |
|---|---|---|---|
| binary event | `leader_probability` | Yes·No 원값 | binary끼리 |
| exclusive multi | `leader_probability` | `leader_margin`, outcome 수 | 같은 유형끼리 |
| independent multi | 각 child의 binary leader 확률 | 부모 event | child binary끼리 |
| unknown multi | 순위 없음 | raw child 확률과 경고 | 데이터 주의만 |

카테고리 카드는 합성 확신 평균·중앙값을 갖지 않는다. event 유형 분포가 다른
카테고리를 거래 활동과 데이터 품질로는 비교할 수 있지만 하나의 확신 점수로는
비교하지 않는다.

---

## 4. 카테고리 체계

### 4-1. 원칙

- 모든 열린 event는 대표 카테고리 하나만 갖는다.
- Polymarket 원래 태그는 전부 `tags` 배열에 보존하고 보조 필터로 사용한다.
- 지역 태그는 `regions`, 운영·보상·UI 태그는 `system_tags`로 분리한다.
- 운영 태그가 붙었다는 이유로 event 자체를 버리지 않는다.
- 매핑이 없거나 서로 충돌하면 `other`에 둔다.
- 분류는 결정론적 allowlist이며 LLM을 사용하지 않는다.
- `taxonomy_version`과 적용 근거 `category_reason`을 현재 manifest에 기록한다.

### 4-2. 1차 대표 카테고리

| 키 | 화면 이름 | 대표 태그 예시 |
|---|---|---|
| `politics` | 정치·선거 | Politics, Elections, US Election, Midterms |
| `geopolitics` | 지정학·국제 | Geopolitics, World, Middle East |
| `economy_finance` | 경제·금융 | Economy, Finance, Macro Indicators, Fed Rates |
| `crypto` | 크립토 | Crypto, Bitcoin, Ethereum, Crypto Prices |
| `technology_ai` | 기술·AI | Tech, AI, Big Tech |
| `business` | 기업·비즈니스 | Business, Companies, Deals |
| `sports` | 스포츠 | Sports, Soccer, NFL, MLB, Formula 1 |
| `culture` | 문화·엔터테인먼트 | Culture, Awards, Movies, Music, Games |
| `science_health` | 과학·건강 | Science, Space, Health, Medicine |
| `weather_climate` | 날씨·기후 | Weather, Climate, Temperature |
| `law_regulation` | 법률·규제 | Legal, Courts, Regulation |
| `other` | 기타·미분류 | 대표 분야로 확정할 수 없는 모든 event |

위 표는 시작점이지 완결 목록이 아니다. 0단계가 raw tag 빈도를 모은 뒤 2단계에서
실제 allowlist와 대표 카테고리 precedence를 고정한다.

### 4-3. 실측 뒤 정하는 분류 승격 기준

named category 비율 90%를 사전에 고정하지 않는다. 0단계 태그 실측 후 다음을 모두
수행한다.

1. `other` event를 빈도순으로 정렬한다.
2. `other`의 상위 20개 raw tag를 모두 검토한다.
3. 각 tag를 named category에 매핑하거나, 지역·운영 tag 또는 진짜 미분류로 남긴
   이유를 코드 주석과 fixture에 기록한다.
4. 그 결과의 named category 비율을 초기 `NAMED_CATEGORY_TARGET`으로 고정한다.
5. 이후 taxonomy version에서 이 비율이 낮아지면 원본 tag drift로 판정한다.

분류 목표는 event를 숨기는 기준이 아니다. 목표 미달이어도 `other`로 모두 남지만,
“분야별로 보기 쉽다”는 제품 목적을 충족하지 못했으므로 공개 승격은 멈춘다.

---

## 5. 대시보드 지표와 기본 표본

### 5-1. 상단 범위·활동 지표

| 지표 | 정의 | 기본 모집단 |
|---|---|---|
| 열린 event | `open_event_count` | 모든 상태 |
| 열린 market | 열린 event의 nested market 수 합 | 모든 상태 |
| 24시간 거래량 | event `volume24hr`의 유효값 합 | 모든 상태, 결측 별도 |
| 현재 유동성 | 해석 가능한 event liquidity 합 | 모든 상태, 결측 별도 |
| 카테고리 커버리지 | named category event / 열린 event | 모든 상태 |
| 확률 커버리지 | 현재 확률을 안전하게 읽은 event / 열린 event | 모든 상태 |

`volume24hr`는 별도 거래 이력을 내려받아 계산하지 않고 Gamma event 응답의 현재
rolling 집계값만 쓴다. 날짜별로 저장하거나 다음 갱신과 비교하지 않는다.

### 5-2. 가격과 유동성 상태를 구분한다

`null`을 0으로 바꾸지 않는다. 가격과 유동성 품질은 먼저 별도 축으로 계산한다.

```text
price_status     = ok | unavailable
liquidity_status = ok | low | zero | missing
```

event liquidity 결정 순서:

1. event의 `liquidity`가 유효한 0 이상 숫자면 그것을 쓴다.
2. event 값이 없고 모든 열린 child market의 동일 단위 liquidity가 유효하면 한 번만
   합산하고 `liquidity_source=markets_sum`을 기록한다.
3. 일부 child만 있거나 단위를 확인할 수 없으면 합을 만들지 않고 `missing`이다.

event와 child liquidity를 동시에 더하지 않는다. 실제 0은 `zero`, 결측은 `missing`,
0보다 크고 USD 1,000 미만은 `low`다. USD 1,000은 운영자가 조정할 수 있는 표시용
기본값이며 수집 제외에는 쓰지 않는다.

필터 편의를 위한 단일 `data_status`는 다음 우선순위로 파생한다.

```text
unavailable > liquidity_missing > no_liquidity > low_liquidity > ok
```

### 5-3. “전부 포함”과 “순위 기본 표본”을 분리한다

- **수집·회계·상단 범위·카테고리 수·전체 탐색기:** 모든 상태를 포함한다.
- **거래 활동 합계:** 유효 거래량이 있는 모든 event를 포함하고 결측 수를 병기한다.
- **컨센서스 강함·팽팽함 순위:** 기본은 `data_status=ok`만 사용한다.
- **주의 event 포함 토글:** 사용자가 켜면 가격이 읽히는 `low_liquidity`,
  `no_liquidity`, `liquidity_missing`도 순위에 추가한다.
- **데이터 주의 목록:** 모든 비정상 상태를 항상 보여 준다.

이는 얇은 event를 버리는 것이 아니다. 전체 모집단과 요약 순위의 신뢰 가능한 기본
표본을 분리한 것이다.

### 5-4. 카테고리 카드와 진단 목록

카테고리 카드:

- event 수와 전체 대비 비중
- market 수
- 24시간 거래량과 전체 대비 비중
- 현재 유동성과 결측 수
- `ok`·`low`·`zero`·`missing`·`unavailable` 수
- 24시간 거래량이 가장 큰 event 한 건
- binary / exclusive multi / independent multi / unknown multi 구성

기본 정렬은 24시간 거래량 내림차순이며 event 수·유동성으로 바꿀 수 있다.
합성 컨센서스 강도 정렬은 없다.

진단 목록:

- **가장 활발한 event:** 24시간 거래량 상위
- **binary 컨센서스가 강함:** `ok` binary의 leader 확률 상위
- **binary가 팽팽함:** `ok` binary의 leader 확률이 50%에 가까운 순
- **다지선다 선두가 뚜렷함:** `ok` exclusive multi의 leader 확률과 margin 상위
- **다지선다 경합:** `ok` exclusive multi의 leader margin 하위
- **독립 market의 한쪽 쏠림:** `ok` child binary를 같은 유형끼리 비교
- **데이터 주의:** 유동성 결측·0·낮음, 가격 없음, unknown multi

각 순위는 유형 이름과 표본 조건을 제목에 직접 표시한다.

---

## 6. 웹 화면 정보 구조

경로는 `https://nunchi.live/polymarket`이다. 첫 화면은 필터를 누르지 않아도 현재
전체 범위와 분야별 활동을 답해야 한다.

### 6-1. 기본 화면

```text
┌ Polymarket 현재 컨센서스 ─ 갱신 14:15 KST · complete ┐
│ 열린 event │ market │ 24h 거래량 │ 유동성 │ 확률 커버리지 │
└────────────────────────────────────────────────────────┘

[분야별 현재 활동 — 24h 거래량 수평 막대, 전체 상태]

[카테고리 카드]
정치·선거  event ... · market ... · 24h ... · 품질 주의 ...
스포츠     event ... · market ... · 24h ... · 품질 주의 ...

[현재 주목할 event — 기본 표본: 정상 데이터만]
활발함 | binary 강함/경합 | 다지선다 선두/경합 | 데이터 주의
[주의 event 포함] 토글

[전체 event 탐색기]
카테고리 · 태그 · 지역 · 유형 · 상태 · 검색 · 정렬
event 행 → 현재 leader와 확률
상세 열기 → 모든 child market의 현재 확률
```

### 6-2. 사용하는 시각화

- **분야별 현재 활동:** 0에서 시작하는 정렬 수평 막대
- **binary 확률:** Yes/No 막대와 숫자 라벨
- **배타적 다지선다:** 검증 통과 시 결과별 수평 막대, 상세에서 전 결과 표시
- **독립·unknown multi:** 서로 쌓지 않은 child 확률 막대
- **정확한 값 탐색:** 서버 페이지가 있는 표 또는 모바일 카드

사용하지 않는 시각화:

- 선·영역·캔들·sparkline 등 시간축 그래프
- 기간 변화 화살표와 과거 비교 색상
- 카테고리가 많은 원형 차트나 면적을 왜곡하는 bubble chart
- 서버가 생성한 PNG 차트
- event 유형을 섞은 합성 확신 게이지

### 6-3. 필터와 정렬

```text
category=<key>
tag=<slug-or-label>
region=<key>
type=binary|exclusive_multi|independent_multi|unknown_multi
status=ok|low_liquidity|no_liquidity|liquidity_missing|unavailable
q=<title search>
sort=volume24hr|volume_total|liquidity|leader_probability|end_at
order=asc|desc
page>=1
page_size=1..100
```

`leader_probability` 정렬은 binary와 exclusive multi처럼 event leader가 정의된
유형에만 적용한다. 서로 다른 유형을 한 순위에 섞지 않도록 type 없이 요청하면
422로 거절하고 화면은 먼저 유형을 선택하게 한다.

URL query에 필터·정렬·페이지를 반영해 새로고침과 링크 공유 후에도 같은 화면이
열린다.

### 6-4. 표현과 접근성

- 확률·금액·상태는 숫자와 텍스트로 직접 표기하고 색만으로 구분하지 않는다.
- 긴 제목은 두 줄 요약 후 전체 제목을 펼칠 수 있게 한다.
- 키보드로 필터, 정렬, 상세 열기를 사용할 수 있게 한다.
- 360px 모바일에서는 표를 카드 목록으로 바꾼다.
- 외부 폰트·CDN·원격 차트 런타임을 추가하지 않는다.
- Polymarket 문자열은 `textContent` 또는 서버 HTML escape로만 넣는다.
- 외부 링크는 `noopener noreferrer`를 적용한다.

---

## 7. 현재 generation·자원 계약

### 7-1. compact manifest와 상세 shard

웹 프로세스가 모든 child market을 메모리에 중복 보관하지 않게 현재값을 두 층으로
나눈다.

```text
data/webpub/polymarket/
  current.json                         compact manifest + event index
  refresh_status.json                  시도·실패·자원·freshness
  generations/<generation_id>/
    details-<category>-<shard>.jsonl   event별 전체 현재 결과
```

`current.json`의 event 목록에는 검색·필터·목록에 필요한 필드와 leader만 둔다. 모든
child 현재 확률은 detail JSONL에 event당 한 줄로 보존한다. compact event는
`detail_ref {file, byte_offset, byte_length}`를 가져 상세 API가 해당 줄만 읽는다.

원본 Gamma 페이지는 저장하거나 한꺼번에 들고 있지 않는다. 한 페이지를 받으면
검증·중복 제거·정규화하고 detail shard에 append한 뒤 raw 페이지를 해제한다.

### 7-2. manifest 회계 필드

```json
{
  "schema_version": 1,
  "taxonomy_version": 1,
  "generation_id": "20260830T141500+0900",
  "generated_at": "2026-08-30T14:15:00+09:00",
  "source": "polymarket_gamma",
  "pagination_mode": "keyset",
  "coverage_status": "complete",
  "stats": {
    "fetched_record_count": 0,
    "unique_event_count": 0,
    "duplicate_event_count": 0,
    "missing_identity_count": 0,
    "excluded_non_open_count": 0,
    "open_event_count": 0,
    "consensus_ready_event_count": 0,
    "unavailable_event_count": 0,
    "page_count": 0,
    "shifted_page_count": 0,
    "source_request_count": 0,
    "retry_count": 0,
    "response_bytes": 0,
    "walk_seconds": 0.0,
    "market_count": 0,
    "categorized_ratio": 0.0,
    "consensus_coverage": 0.0,
    "volume24hr": null,
    "volume24hr_missing_count": 0,
    "liquidity": null,
    "liquidity_missing_count": 0
  },
  "categories": [],
  "events": []
}
```

compact event 최소 필드:

```text
id, slug, title, url
primary_category, category_reason
tags[], regions[], system_tags[]
end_at, active, closed, restricted
volume24hr, volume_total, liquidity, liquidity_source, open_interest
market_count, event_type
price_status, liquidity_status, data_status
leader {label, probability}, runner_up_probability, leader_margin
quality {raw_price_sum, missing_prices, warnings[]}
detail_ref {file, byte_offset, byte_length}
```

### 7-3. 원자적 승격과 last-good

1. 새 temporary generation directory에 detail shard를 쓴다.
2. compact manifest를 임시 파일에 쓰고 다시 읽는다.
3. 회계 등식, 모든 detail byte range, JSON schema와 shard hash를 검증한다.
4. generation directory를 최종 이름으로 rename한다.
5. 마지막에만 `current.json`을 `os.replace`해 새 generation을 가리킨다.
6. 실패하면 current와 이전 generation을 그대로 유지하고 status만 갱신한다.
7. 성공 후에도 직전 generation 하나는 남기고 그보다 오래된 현재값만 정리한다.

날짜별 이력은 만들지 않는다. 직전 generation 보존은 배포 중 열린 요청과 rollback을
위한 last-good 두 벌일 뿐 UI·API에서 과거로 조회할 수 없다.

### 7-4. 1GB 인스턴스 자원 상한과 사전 분기

0단계와 1단계가 다음 임시 안전 상한을 모두 통과해야 API 구현으로 간다.

| 항목 | 상한 |
|---|---|
| compact `current.json` | 16 MiB 이하 |
| detail shard 한 개 | 16 MiB 이하 |
| refresh peak RSS 증가분 | 192 MiB 이하 |
| webpub Polymarket RSS 증가분 | 96 MiB 이하 |
| 운영 peak의 `/proc/meminfo` MemAvailable | 256 MiB 이상, swap 증가 없음 |
| refresh rolling 24h CPU | 900 CPU-second(0.25 CPU-hour) 이하 |

분기 규칙:

- raw 페이지는 결과와 무관하게 항상 streaming 정규화한다.
- detail shard가 16 MiB를 넘으면 같은 카테고리를 event ID hash로 다시 나눈다.
- compact manifest가 16 MiB 또는 webpub 증가분이 96 MiB를 넘으면 event 목록도
  카테고리 shard로 바꾸고 전량 immutable 인덱스 설계를 중단한다. webpub은 bounded
  LRU로 필요한 shard만 읽는다.
- refresh RSS가 192 MiB를 넘으면 API·화면 구현을 멈추고 페이지별 임시 저장 구조를
  다시 설계한다. 주기만 늘려 메모리 문제를 숨기지 않는다.
- 위 구조로도 MemAvailable 256 MiB를 지키지 못하면 별도 인스턴스 또는 제품 범위
  재설계를 결정하기 전에는 배포하지 않는다.

### 7-5. 별도 프로세스 CPU 회계

refresh job은 Linux `resource.getrusage(RUSAGE_SELF)`로 매 실행의 user+system
CPU-second와 max RSS를 `refresh_status.json`에 기록하고 rolling 24h 합계를 만든다.

기본 15분 주기에서 900 CPU-second/일은 2 vCPU 하루 용량의 약 0.52%다. 봇의 평시
9% 목표와 합쳐도 micro 인스턴스의 10% baseline 아래에 남기기 위한 상한이다.

- 900초를 넘으면 먼저 주기를 30분으로 늘린다.
- 30분에서도 넘으면 refresh CPU를 프리필터의 외부 foreground로 차감하는 공유
  상태 계약을 구현하거나 별도 인스턴스로 옮긴다.
- 이 상한을 통과하기 전에는 “별도 프로세스라 영향 없음”이라고 판정하지 않는다.

---

## 8. 읽기 전용 웹 API

| 경로 | 내용 |
|---|---|
| `GET /polymarket` | 대시보드 HTML |
| `GET /api/polymarket/summary` | freshness, 회계, 상단 지표, 카테고리 집계 |
| `GET /api/polymarket/events` | 검색·필터·정렬·페이지 event 목록 |
| `GET /api/polymarket/events/{event_id}` | detail shard에서 읽은 전체 현재 결과 |
| `GET /api/polymarket/categories` | 카테고리·raw tag·지역 필터 값 |
| `GET /api/polymarket/health` | 마지막 성공·실패·자원·coverage 상태 |

webpub은 compact manifest만 메모리에 둔다. mtime이 바뀌었을 때 새 immutable 인덱스를
만들고 detail은 byte range로 읽는다. 7-4 임계 초과 분기가 발동하면 compact
category shard도 bounded LRU로 바꾼다.

ETag는 `generated_at` 하나로 만들지 않는다.

```text
etag = sha256(
  generation_id + route_name + canonical_known_query + response_variant
)
```

필터·정렬·page가 다른 응답은 서로 다른 ETag를 가진다.

- 알려진 `sort`, `order`, enum, page, page_size 값이 잘못되면 422다.
- `page_size > 100`도 422다.
- 알려지지 않은 query parameter는 무시한다. UTM 같은 링크 부가값으로 페이지를
  깨뜨리지 않는다.
- GET·HEAD 외에 상태를 바꾸는 route는 만들지 않는다.

---

## 9. 갱신 주기와 freshness

### 9-1. 기본 주기와 재시도

- 기본은 systemd `Type=oneshot` 15분 주기다.
- 이전 실행이 끝나지 않았으면 같은 service를 중복 실행하지 않는다.
- 재시도·오류 분류를 새로 만들지 않는다. 현행 `_JsonEndpoint`의 timeout·연결 오류·
  429·5xx·`Retry-After` 상한 로직을 새 패키지로 옮겨 재사용한다.
- 4xx는 재시도하지 않고, 451은 설정된 프록시로 같은 probe·수집 경로를 사용한다.
- Neurons, 인증 토큰, 주문 API는 사용하지 않는다.

5분은 기본값이 아니다. 다음을 모두 만족하고 운영자가 더 높은 신선도를 실제로
요구할 때만 예외로 낮춘다.

```text
successful_walk_source_request_count × 288 <= 3,000 requests/day
5분 가정 rolling CPU <= 900 seconds/day
full walk p95 wall time < 60 seconds
coverage_status complete 유지
```

15분에서도 `successful_walk_source_request_count × 96 > 5,000 requests/day`이거나
CPU 상한을 넘으면 30분으로 늘린다. status에는 재시도를 포함한 실제 request 수와
24시간 합계도 기록한다.

환경 변수는 운영자가 조정할 표시 기준만 둔다.

```text
POLYMARKET_PROXY_URL=
POLYMARKET_WEB_LOW_LIQUIDITY=1000
```

timeout과 파일 경로처럼 운영 중 조정하지 않는 값은 `shared/core/config.py`의 literal
상수로 둔다.

### 9-2. 실제 주기로 freshness를 판정한다

refresh status는 `last_attempt_at`, `previous_attempt_at`, `last_success_at`, 최근
attempt 간격을 보존한다. `observed_interval_seconds`는 최근 최대 5개 attempt 간격의
중앙값으로 job이 계산한다. 웹은 별도 환경값을 사람이 timer와 맞춘다고 가정하지
않고 이 값을 읽는다.

| 상태 | 기준 | 화면 |
|---|---|---|
| warming_up | attempt 간격이 아직 2개 미만 | 마지막 성공 시각과 준비 중 배지 |
| 정상 | 마지막 성공이 observed interval 2회 이내 | 갱신 시각과 정상 배지 |
| 지연 | 2회 초과, 6회 이내 | 전체 폭 갱신 지연 경고 |
| stale | 6회 초과 | last-good 유지, stale 경고 |
| 없음 | 정상 generation이 한 번도 없음 | 원천 수집 실패 안내, API 503 |

timer unit의 15분 기본값과 첫 실행 fallback은 unit/config 계약 테스트로 한 번 더
검증한다. 정상 파일을 빈 값으로 덮어쓰지 않는 것이 최우선 규약이다.

---

## 10. 파일 배치와 철수 범위

### 10-1. 새 파일

```text
web/polymarket/dashboard/
  __init__.py
  transport.py           현행 _JsonEndpoint 재사용
  client.py              0단계에서 선택한 events 전수 순회
  models.py              현재 event/market 모델과 가격 검증
  taxonomy.py            대표 카테고리·지역·운영 tag
  storage.py             manifest·detail shard·원자적 generation

web/polymarket/refresh.py
web/polymarket/repository.py

infra/systemd/stock-chatbot-polymarket-refresh.service
infra/systemd/stock-chatbot-polymarket-refresh.timer
```

### 10-2. 수정 파일과 문서 계약

```text
web/server.py             /polymarket와 /api/polymarket/*
web/pages.py       nav와 공통 shell
shared/core/config.py        현재 웹에 필요한 literal·env만 유지
.env.example              POLYMARKET_WEB_LOW_LIQUIDITY와 proxy
infra/Caddyfile.example  공개 읽기 정책
infra/server-ops.md        설치·상태·장애·철수 절차 교체
README.md                 keyset smoke와 현재 웹 설명으로 교체
code_guide.md                 과거 승격·90일·Telegram 가정 제거, 현재 계획서명 수정
web/tests/test_webpub.py  페이지·API 회귀
web/tests/test_polymarket_smoke.py  events keyset·offset 실제 프로브
```

`code_guide.md`의 현재 Polymarket 승격, `/polymarket` 명령, 90일 이력,
`polymarket_rules.py` allowlist 설명은 6단계 완료 시점의 사실과 충돌하므로 함께
교체한다. 계획서 목록도 `web/docs/polymarket-dashboard.md`로 바꾼다.

### 10-3. 제거·이동할 코드와 설정

새 웹의 4b와 독립 배포가 검증된 뒤 제거한다.

- `telegram_bot/features/market_sentiment/handlers.py`의 `/polymarket` command·gate·caption
- `telegram_bot/handlers/navigation.py`의 폴리마켓 메뉴·callback
- `telegram_bot/features/market_sentiment/snapshot.py`의 08:35 job
- `telegram_bot/features/market_sentiment/feature.py`의 client/store 주입과 scheduler wiring
- `telegram_bot/features/market_sentiment/chart.py`의 `render_polymarket_chart`
- `telegram_bot/features/market_sentiment/polymarket_rules.py`
- `telegram_bot/features/market_sentiment/polymarket_history.py`
- `app/polymarket_backfill.py`
- `telegram_bot/state/polymarket_consensus.py`와 `telegram_bot/state/__init__.py` export
- 기존 client를 옮긴 뒤 `telegram_bot/features/market_sentiment/polymarket.py`
- proxy factory를 옮긴 뒤 `telegram_bot/features/market_sentiment/polymarket_proxy.py`

삭제할 기존 config 이름:

```text
POLYMARKET_CONSENSUS_FILE
POLYMARKET_BACKFILL_FILE
POLYMARKET_CLOB_URL
POLYMARKET_ENABLED
POLYMARKET_PANEL_ENABLED
POLYMARKET_MIN_VOLUME
POLYMARKET_MIN_LIQUIDITY
POLYMARKET_MAX_SPREAD
POLYMARKET_MAX_HORIZON_DAYS
POLYMARKET_RETENTION_DAYS
```

`POLYMARKET_BASE_URL`, `POLYMARKET_PROXY_URL`, `POLYMARKET_TIMEOUT`은 새 transport에
필요한 동안 유지한다. `POLYMARKET_WEB_LOW_LIQUIDITY`는 표시용 새 env다.

기존 테스트 8개의 처리는 명시적으로 나눈다.

| 테스트 | 처리 |
|---|---|
| `test_polymarket_client.py` | transport·events parser 테스트로 이동·재작성 |
| `test_polymarket_proxy.py` | 새 transport proxy 테스트로 이동 |
| `test_polymarket_smoke.py` | 유지하고 keyset·offset 프로브로 교체 |
| `test_polymarket_consensus.py` | current generation 회계·last-good 테스트로 대체 후 삭제 |
| `test_polymarket_history.py` | 과거 이력 제거와 함께 삭제 |
| `test_polymarket_panel.py` | webpub 화면·API 테스트로 대체 후 삭제 |
| `test_polymarket_rules.py` | taxonomy·price model fixture로 대체 후 삭제 |
| `test_polymarket_wiring.py` | systemd 계약·Telegram 부재 회귀로 대체 후 삭제 |

### 10-4. 기존 이력의 보관과 복구 가능성

기존 `data/market_sentiment/polymarket_*.json`은 웹 승격 뒤 한 번 백업하고
런타임에서는 더 이상 읽지 않는다. 새 서비스는 이를 변환하거나 이력으로 가져오지
않는다.

과거 시계열을 다시 원하면 자동 복구 경로는 없다. git에서
`polymarket_history.py`와 `app/polymarket_backfill.py`를 되살리고 CLOB 이력으로
재구성해야 한다. 이는 현재만 본다는 제품 결정을 되돌리는 별도 변경이다.

---

## 11. 구현 단계

각 단계는 그 지점에서 멈춰도 기존 봇과 공개 웹을 깨뜨리지 않아야 한다.

### 0단계 — 운영 서버 원천 프로브: 착수 블로커

- opt-in smoke에 raw `/events/keyset` 두 페이지와 full walk를 추가한다.
- 레거시 `/events` offset도 같은 조건으로 측정한다.
- Lightsail 직접 경로와 451 시 proxy 경로에서 실행한다.
- page size, event·market 수, duplicate·shift, tag 빈도, bytes, wall, CPU, RSS를 남긴다.

완료 기준:

- 2-2의 A 또는 B 경로 하나가 전수 순회 가능하다고 실측된다.
- 선택 경로, 실제 page size, 안정성 허용 기준이 테스트와 client 계약에 고정된다.
- C이면 구현을 중단하고 원천 계약을 다시 쓴다.

### 1단계 — streaming 현재 모델과 자원 prototype

- 선택한 pagination client와 현행 `_JsonEndpoint` transport를 옮긴다.
- 중복·missing identity·non-open 회계를 구현한다.
- 세 유형과 `unknown_multi`, 가격 tolerance fixture를 구현한다.
- 페이지별 streaming 정규화와 compact/detail prototype을 만든다.
- 15분·5분·30분 가정의 일일 요청·CPU 비용을 계산한다.

완료 기준:

- 2-4 회계 등식이 모두 맞고 반복 cursor는 종료된다.
- raw 페이지를 다음 요청까지 보관하지 않는다.
- 7-4 RSS·파일·MemAvailable 상한과 7-5 CPU 상한을 통과한다.
- 실패하면 API 구현 전에 shard 또는 인스턴스 분기를 확정한다.

### 2단계 — taxonomy와 현재 generation

- 실측 tag 전체를 분야·지역·운영 tag로 나눈다.
- `other` 상위 20개 tag를 검토하고 초기 실측 target을 고정한다.
- manifest·detail reference·원자적 generation·last-good을 구현한다.

완료 기준:

- category event 합이 `open_event_count`와 같다.
- 미분류도 `other`에 남고 품질 상태와 독립적으로 회계된다.
- 모든 detail byte range와 hash가 검증된다.
- 실패한 실행이 current generation을 바꾸지 않는다.

### 3단계 — 읽기 전용 API

- summary·events·detail·categories·health를 추가한다.
- 검색·filter·sort·pagination·canonical ETag를 테스트한다.
- 알려지지 않은 query는 무시하고 알려진 잘못된 값만 422로 처리한다.

완료 기준:

- API 합계가 manifest와 일치한다.
- list에는 compact leader만, detail에는 해당 event의 모든 현재 child가 나온다.
- GET·HEAD 외에 상태를 바꾸는 경로가 없다.

### 4a단계 — 요약 화면

- 상단 범위, 분야 활동 막대, category 카드, 유형별 진단 목록을 구현한다.
- 기본 순위 표본 `ok`와 주의 포함 toggle을 구현한다.
- freshness·coverage·데이터 주의를 첫 화면에 표시한다.

완료 기준:

- 상호작용 없이 0-1 질문 중 전체 범위, 분야 활동, leader, 주요 경합, 데이터
  주의를 답한다.
- 시간축·기간 선택·과거 추세·PNG·합성 strength가 없다.
- 4a는 제한 공개할 수 있지만 이 단계만으로 Telegram을 철수하지 않는다.

### 4b단계 — 전체 event 탐색기

- category·tag·region·type·status·검색·정렬·페이지·URL 상태를 구현한다.
- event detail을 열 때만 모든 child 현재 확률을 읽는다.
- 데스크톱·태블릿·360px·키보드 사용을 점검한다.

완료 기준:

- 모든 열린 event를 category 또는 검색을 통해 찾을 수 있다.
- filter와 URL query가 일치하고 새로고침 뒤 같은 결과가 열린다.
- list·detail·summary가 같은 generation을 사용한다.

### 5단계 — 독립 배포

- resource limit이 있는 one-shot service와 15분 timer를 설치한다.
- Caddy 뒤 `/polymarket`을 열고 direct·proxy·last-good·stale을 확인한다.
- 실제 requests/day, CPU 24h, RSS, MemAvailable, 파일 크기, 응답 p95를 기록한다.

완료 기준:

- 봇을 중지해도 갱신과 웹 열람이 계속된다.
- 웹 방문을 반복해도 Gamma 호출 수가 늘지 않는다.
- 실패 후 last-good이 경고와 함께 열린다.
- CPU·메모리 상한을 넘지 않으며 넘으면 7-4·7-5 분기를 먼저 실행한다.

### 6단계 — Telegram·과거 경로 철수와 문서 교체

- 10-3의 code·config·test를 이동 또는 삭제한다.
- 기존 데이터는 백업 후 런타임에서 분리한다.
- `README.md`, `.env.example`, `code_guide.md`, `infra/server-ops.md`를 현재 구조로
  교체한다.
- 전체 ruff·pytest와 Telegram 메뉴 회귀를 실행한다.

완료 기준:

- Telegram Polymarket command·menu·callback·job·state가 없다.
- history·backfill·chart·promotion gate와 죽은 config가 import되지 않는다.
- `code_guide.md`가 과거 90일/승격 가정을 설명하거나 삭제된 계획서를 가리키지 않는다.
- `/market`, 뉴스, research 기능이 회귀 없이 통과한다.
- 웹 대시보드가 Polymarket의 유일한 사용자 화면이다.

---

## 12. 검증 게이트

| # | 검증 | 통과 기준 | 실패 시 |
|---|---|---|---|
| G0 | 원천 전수 순회 | 운영 Lightsail에서 A 또는 B 경로가 끝까지 전진 | 구현 착수 중단 |
| W0 | event 회계 | fetched = unique + duplicate; unique = open + non-open; open = ready + unavailable | current 교체 중단 |
| W1 | pagination 안정성 | 반복 cursor 없음, duplicate·shift가 프로브 기준 이내 | last-good 유지, 경로 재검토 |
| W2 | category 품질 | `other` 상위 20 tag 검토 + 실측 후 고정한 named target 충족 | taxonomy 보강 |
| W3 | 가격·유형 | tolerance 0.05와 binary·negRisk·independent·unknown fixture 일치 | 확률 표시 중단 |
| W4 | 순위 표본 | 기본 순위는 `ok`만, 주의 포함은 명시 toggle | 요약 공개 중단 |
| W5 | API 집계 | summary·events·detail이 같은 generation과 합계를 사용 | API 수정 |
| W6 | ETag·query | generation+route+canonical query별 ETag, unknown query 무시 | cache/query 수정 |
| W7 | freshness | observed interval로 상태 판정, 시각 항상 표시 | timer/status 수정 |
| W8 | last-good | 실패가 current hash를 바꾸지 않음 | 원자적 승격 수정 |
| W9 | 메모리 | manifest 16 MiB, shard 16 MiB, RSS·MemAvailable 상한 충족 | 7-4 분기 |
| W10 | CPU·요청량 | 24h CPU 900초 이하, 선택 주기의 요청 상한 충족 | 주기 증가·공유 회계 |
| W11 | 무이력 | history endpoint·기간 filter·시계열·날짜별 저장 없음 | 남은 경로 제거 |
| W12 | Telegram 분리 | 봇 정지 중 웹 성공, 봇에 Polymarket wiring 없음 | 결합 import 제거 |
| W13 | 읽기 전용 | 웹·API에 상태 변경 method 없음 | route 제거 |
| W14 | 접근성·반응형 | keyboard·360px·desktop에서 정보 손실 없음 | 화면 승격 보류 |

---

## 13. 테스트 범위

### 실제 원천 smoke

- `/events/keyset` 실제 limit, cursor 전진, 두 번째 page ID, full walk
- 레거시 `/events` offset 실제 limit, deterministic order, duplicate·shift
- Lightsail direct와 451 proxy
- page/event/market/tag 수, bytes, wall, CPU, RSS 출력

### 수집·회계·parser

- keyset·offset 종료, 반복 cursor, 동일 page 방어
- `_JsonEndpoint` timeout·429·5xx·Retry-After와 4xx 즉시 실패
- duplicate·missing identity·non-open 회계 등식
- 문자열 JSON·배열 outcomes
- 가격 합 경계 0.95·1.05 통과와 밖의 실패
- `negRisk` true·false·missing 유형 판정
- malformed event가 전체 page를 죽이지 않고 unavailable로 남음

### taxonomy·지표

- 분야·지역·운영 tag 분리와 단일 category 배정
- 다중 분야 precedence, ambiguous → other, category 합계
- liquidity event 우선·child 합계·부분 결측·실제 0 구분
- 기본 순위 `ok`, toggle 후 flagged 포함
- binary와 exclusive multi 순위가 서로 섞이지 않음

### generation·API

- streaming page 해제, detail byte offset·length·hash
- 원자적 current, last-good, 직전 generation 보존
- category·tag·region·type·status filter 조합
- search·sort·pagination·canonical ETag
- unknown query 무시, known invalid query 422
- compact list와 full detail 계약
- 빈·깨진·stale·degraded·failed 상태

### 자원·화면·분리

- process CPU·RSS status와 rolling 24h 계산
- systemd 15분·Nice·MemoryMax·timeout 계약
- summary 4a와 explorer 4b, HTML escape, external link 속성
- mobile·keyboard·빈 상태·coverage·stale 경고
- Telegram command·callback·menu·job 부재
- 기존 봇 기능 전체 회귀

---

## 14. 위험과 대응

| 위험 | 신호 | 대응 |
|---|---|---|
| events keyset이 문서와 다름 | cursor·page 반복, limit 절단 | G0에서 offset 검증, 둘 다 실패하면 구현 중단 |
| offset 순회 중 목록 이동 | duplicate·shift 증가 | dedup·품질 공개, 프로브 기준 초과 시 last-good 유지 |
| Gamma schema 변경 | unavailable·missing identity 급증 | generation 승격 중단, parser 수정 |
| tag 체계 변경 | named target 하락, other 상위 tag 교체 | 상위 20 재검토, taxonomy version 증가 |
| 낮은 유동성 극단값 | 0%·100%가 순위 점유 | 기본 순위 `ok`, flagged toggle로만 포함 |
| event 유형 혼합 왜곡 | binary와 다지선다 수치가 한 순위에 등장 | 유형별 leader·margin만 사용, 합성 strength 금지 |
| snapshot 비대화 | manifest·shard·RSS 상한 초과 | streaming·재shard·bounded LRU, 필요 시 별도 인스턴스 |
| 별도 job CPU가 봇 계측 밖 | refresh 24h CPU 상한 초과 | 30분, 외부 foreground 차감 또는 별도 인스턴스 |
| freshness 설정 불일치 | timer 변경 뒤 stale 판정 오류 | observed interval을 status에서 계산 |
| 지역 차단 451 | Lightsail direct 실패 | 같은 smoke를 proxy에서 통과시킨 뒤 사용 |
| 확률을 사실로 오해 | 높은 값을 확정 예측으로 해석 | 면책, 유형·유동성·종료일 동시 표시 |
| 웹 장애가 봇에 번짐 | 공유 import·resource 경합 | 파일 경계, 낮은 systemd priority, 회귀·자원 gate |

---

## 15. 완료 정의

다음을 모두 만족해야 이 계획이 끝난다.

- 운영 Lightsail에서 검증된 events 전수 순회 경로를 사용한다.
- `/polymarket` 웹에서 한 번의 성공한 순회가 관측한 모든 열린 event가 category별로
  보인다.
- duplicate·non-open·unavailable 회계와 coverage 상태가 숨겨지지 않는다.
- 사용자가 각 event의 현재 leader와 상세 child 확률을 읽을 수 있다.
- 합성 `consensus_strength` 없이 같은 유형 안에서 강함과 경합을 비교한다.
- 수집·탐색은 전부 포함하고 순위 기본 표본은 `ok`로 분리된다.
- category·tag·region·type·status 검색과 정렬이 동작한다.
- freshness와 last-good, CPU·RSS·요청량 상태가 운영 계약에 들어간다.
- 과거 이력 수집·저장·백필·기간 선택·시계열·PNG 차트가 없다.
- Polymarket Telegram command·menu·callback·job·state와 죽은 config가 없다.
- 봇을 중지해도 Polymarket 갱신과 웹 열람이 동작한다.
- 기존 시장 감성·이상 탐지·뉴스·research 기능은 회귀 없이 유지된다.
- `.env.example`, `README.md`, `code_guide.md`, `infra/server-ops.md`가 현재 구조와 맞다.

완료 뒤 이 계획서는 삭제하고 구현 이력은 git이 맡는다.
